#!/usr/bin/env python3
"""
Color Science & Reference Look DNA Analyzer for DaVinci Resolve (macOS & Windows).
Analyzes any color-graded reference image (and optional ungraded source clip frame)
to compute:
  1. Shadow / Midtone / Highlight RGB & Luminance DNA
  2. Exact ASC-CDL parameters (Slope, Offset, Power, Saturation) for DaVinci Resolve
  3. A precision 33x33x33 3D .cube LUT with zone-weighted split-toning & filmic roll-off
  4. A side-by-side Before | Reference | After comparison image
"""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def rgb_to_hex(r: float, g: float, b: float) -> str:
    ri = int(clamp(round(r * 255.0), 0, 255))
    gi = int(clamp(round(g * 255.0), 0, 255))
    bi = int(clamp(round(b * 255.0), 0, 255))
    return f"#{ri:02X}{gi:02X}{bi:02X}"


def load_image_rgb_float(img_path: Path, sample_size: int = 128) -> list:
    """
    Load an image and return a list of (r, g, b) floats in [0.0, 1.0].
    Uses Pillow if available; falls back to ffmpeg rawvideo rgb24 or macOS sips.
    """
    img_path = Path(img_path).resolve()
    if not img_path.exists():
        raise FileNotFoundError(f"Image not found: {img_path}")

    # 1. Try Pillow
    try:
        from PIL import Image

        with Image.open(img_path) as im:
            rgb_im = im.convert("RGB").resize((sample_size, sample_size))
            raw = rgb_im.tobytes()
            pixels = []
            for i in range(0, len(raw), 3):
                pixels.append((raw[i] / 255.0, raw[i + 1] / 255.0, raw[i + 2] / 255.0))
            return pixels
    except ImportError:
        pass

    # 2. Fallback: ffmpeg rawvideo rgb24
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(img_path),
                "-vf",
                f"scale={sample_size}:{sample_size}",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            capture_output=True,
            timeout=30,
        )
        raw = proc.stdout
        if len(raw) >= 3:
            pixels = []
            for i in range(0, len(raw) - 2, 3):
                pixels.append((raw[i] / 255.0, raw[i + 1] / 255.0, raw[i + 2] / 255.0))
            return pixels
    except Exception:
        pass

    raise RuntimeError(f"Could not decode image {img_path}. Install Pillow (`pip install Pillow`) or ffmpeg.")


def percentile(sorted_vals: list, pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = clamp(pct / 100.0 * (len(sorted_vals) - 1), 0.0, len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    return lerp(sorted_vals[lo], sorted_vals[hi], idx - lo)


def analyze_image_color_dna(pixels: list) -> dict:
    """Compute 3-zone (Shadow / Midtone / Highlight) colorimetric statistics."""
    n = max(1, len(pixels))
    r_all = sorted(p[0] for p in pixels)
    g_all = sorted(p[1] for p in pixels)
    b_all = sorted(p[2] for p in pixels)

    lumas = []
    sats = []
    shadows = []
    midtones = []
    highlights = []

    for r, g, b in pixels:
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        lumas.append(y)
        cmax = max(r, g, b)
        cmin = min(r, g, b)
        sats.append(cmax - cmin)
        if y < 0.28:
            shadows.append((r, g, b, y))
        elif y <= 0.72:
            midtones.append((r, g, b, y))
        else:
            highlights.append((r, g, b, y))

    lumas_sorted = sorted(lumas)

    def zone_avg(zone_list, default_y):
        if not zone_list:
            return {
                "count_pct": 0.0,
                "rgb": [default_y, default_y, default_y],
                "luma": default_y,
                "cast": [0.0, 0.0, 0.0],
                "hex": rgb_to_hex(default_y, default_y, default_y),
            }
        zn = len(zone_list)
        mr = sum(x[0] for x in zone_list) / zn
        mg = sum(x[1] for x in zone_list) / zn
        mb = sum(x[2] for x in zone_list) / zn
        my = sum(x[3] for x in zone_list) / zn
        return {
            "count_pct": round(zn * 100.0 / n, 1),
            "rgb": [round(mr, 4), round(mg, 4), round(mb, 4)],
            "luma": round(my, 4),
            "cast": [round(mr - my, 4), round(mg - my, 4), round(mb - my, 4)],
            "hex": rgb_to_hex(mr, mg, mb),
        }

    mean_r = sum(r_all) / n
    mean_g = sum(g_all) / n
    mean_b = sum(b_all) / n
    mean_y = sum(lumas) / n
    mean_sat = sum(sats) / n

    std_r = math.sqrt(sum((x - mean_r) ** 2 for x in r_all) / n)
    std_g = math.sqrt(sum((x - mean_g) ** 2 for x in g_all) / n)
    std_b = math.sqrt(sum((x - mean_b) ** 2 for x in b_all) / n)
    std_y = math.sqrt(sum((x - mean_y) ** 2 for x in lumas) / n)

    sh_info = zone_avg(shadows, 0.12)
    mid_info = zone_avg(midtones, 0.46)
    hi_info = zone_avg(highlights, 0.84)

    warmth = round((mean_r - mean_b) * 100.0, 2)
    tint = round((mean_g - 0.5 * (mean_r + mean_b)) * 100.0, 2)

    if warmth > 6 and sh_info["cast"][2] > 0.015:
        mood_label = "Cinematic Teal & Orange Split-Tone"
    elif warmth > 8:
        mood_label = "Warm Golden / Kodak Film Look"
    elif warmth < -6:
        mood_label = "Cool Crisp / Cyberpunk Steel Look"
    elif std_y < 0.16:
        mood_label = "Low-Contrast Matte / Filmic Log Roll-Off"
    else:
        mood_label = "High-Contrast Natural Cinema Grade"

    return {
        "mood_description": mood_label,
        "mean_rgb": [round(mean_r, 4), round(mean_g, 4), round(mean_b, 4)],
        "std_rgb": [round(std_r, 4), round(std_g, 4), round(std_b, 4)],
        "luma": {
            "mean": round(mean_y, 4),
            "std": round(std_y, 4),
            "p05_black_point": round(percentile(lumas_sorted, 5), 4),
            "p50_median": round(percentile(lumas_sorted, 50), 4),
            "p95_white_point": round(percentile(lumas_sorted, 95), 4),
        },
        "channel_percentiles": {
            "p05": [
                round(percentile(r_all, 5), 4),
                round(percentile(g_all, 5), 4),
                round(percentile(b_all, 5), 4),
            ],
            "p50": [
                round(percentile(r_all, 50), 4),
                round(percentile(g_all, 50), 4),
                round(percentile(b_all, 50), 4),
            ],
            "p95": [
                round(percentile(r_all, 95), 4),
                round(percentile(g_all, 95), 4),
                round(percentile(b_all, 95), 4),
            ],
        },
        "saturation_mean": round(mean_sat, 4),
        "warmth_index": warmth,
        "tint_index": tint,
        "zones": {
            "shadows": sh_info,
            "midtones": mid_info,
            "highlights": hi_info,
        },
    }


def neutral_baseline_dna() -> dict:
    """Standard neutral Rec.709 baseline statistics when no source frame is provided."""
    return {
        "mean_rgb": [0.45, 0.45, 0.45],
        "std_rgb": [0.22, 0.22, 0.22],
        "luma": {
            "mean": 0.45,
            "std": 0.22,
            "p05_black_point": 0.05,
            "p50_median": 0.45,
            "p95_white_point": 0.92,
        },
        "channel_percentiles": {
            "p05": [0.05, 0.05, 0.05],
            "p50": [0.45, 0.45, 0.45],
            "p95": [0.92, 0.92, 0.92],
        },
        "saturation_mean": 0.24,
        "zones": {
            "shadows": {"rgb": [0.12, 0.12, 0.12], "luma": 0.12, "cast": [0.0, 0.0, 0.0]},
            "midtones": {"rgb": [0.46, 0.46, 0.46], "luma": 0.46, "cast": [0.0, 0.0, 0.0]},
            "highlights": {"rgb": [0.84, 0.84, 0.84], "luma": 0.84, "cast": [0.0, 0.0, 0.0]},
        },
    }


def compute_cdl_and_lut_recipe(ref_dna: dict, src_dna: dict = None, strength: float = 1.0) -> dict:
    """
    Solve for ASC-CDL (Slope, Offset, Power, Saturation) and 3D LUT zone transfer parameters
    that map src_dna (or neutral Rec.709 baseline) to ref_dna.
    """
    strength = clamp(float(strength), 0.0, 1.5)
    base = src_dna if src_dna else neutral_baseline_dna()

    slope = [1.0, 1.0, 1.0]
    offset = [0.0, 0.0, 0.0]
    power = [1.0, 1.0, 1.0]

    for c in range(3):
        src_span = max(0.25, base["channel_percentiles"]["p95"][c] - base["channel_percentiles"]["p05"][c])
        ref_span = max(0.25, ref_dna["channel_percentiles"]["p95"][c] - ref_dna["channel_percentiles"]["p05"][c])
        raw_slope = ref_span / src_span

        # Blend highlight color cast into slope
        hi_cast_delta = ref_dna["zones"]["highlights"]["cast"][c] - base["zones"]["highlights"]["cast"][c]
        raw_slope = raw_slope * 0.65 + (1.0 + hi_cast_delta * 0.9) * 0.35
        s_val = lerp(1.0, clamp(raw_slope, 0.72, 1.35), strength)
        slope[c] = round(s_val, 4)

        # Offset from black point + shadow color cast
        blk_delta = (ref_dna["channel_percentiles"]["p05"][c] - base["channel_percentiles"]["p05"][c]) * 0.45
        sh_cast_delta = (ref_dna["zones"]["shadows"]["cast"][c] - base["zones"]["shadows"]["cast"][c]) * 0.65
        o_val = lerp(0.0, clamp(blk_delta + sh_cast_delta, -0.14, 0.14), strength)
        offset[c] = round(o_val, 4)

        # Power (Gamma) from midtone ratio & midtone cast
        src_mid = clamp(base["zones"]["midtones"]["rgb"][c], 0.18, 0.82)
        ref_mid = clamp(ref_dna["zones"]["midtones"]["rgb"][c], 0.18, 0.82)
        after_so = clamp(src_mid * s_val + o_val, 0.12, 0.88)
        try:
            raw_power = math.log(ref_mid) / math.log(after_so)
        except (ValueError, ZeroDivisionError):
            raw_power = 1.0
        # Dampen extreme gamma swings so skin tones remain natural
        raw_power = 1.0 + (raw_power - 1.0) * 0.55
        p_val = lerp(1.0, clamp(raw_power, 0.76, 1.28), strength)
        power[c] = round(p_val, 4)

    src_sat = max(0.08, base.get("saturation_mean", 0.24))
    ref_sat = max(0.05, ref_dna.get("saturation_mean", 0.24))
    raw_sat = 1.0 + ((ref_sat / src_sat) - 1.0) * 0.65
    sat_val = round(lerp(1.0, clamp(raw_sat, 0.60, 1.38), strength), 4)

    return {
        "strength": strength,
        "cdl": {
            "Slope": f"{slope[0]:.4f} {slope[1]:.4f} {slope[2]:.4f}",
            "Offset": f"{offset[0]:.4f} {offset[1]:.4f} {offset[2]:.4f}",
            "Power": f"{power[0]:.4f} {power[1]:.4f} {power[2]:.4f}",
            "Saturation": f"{sat_val:.4f}",
            "slope_vec": slope,
            "offset_vec": offset,
            "power_vec": power,
            "saturation_float": sat_val,
        },
        "lut_split_tone": {
            "shadow_cast": [
                round((ref_dna["zones"]["shadows"]["cast"][i] - base["zones"]["shadows"]["cast"][i]) * strength, 4)
                for i in range(3)
            ],
            "midtone_cast": [
                round((ref_dna["zones"]["midtones"]["cast"][i] - base["zones"]["midtones"]["cast"][i]) * strength, 4)
                for i in range(3)
            ],
            "highlight_cast": [
                round(
                    (ref_dna["zones"]["highlights"]["cast"][i] - base["zones"]["highlights"]["cast"][i]) * strength,
                    4,
                )
                for i in range(3)
            ],
            "black_floor": round(lerp(0.0, ref_dna["luma"]["p05_black_point"] * 0.35, strength), 4),
            "white_ceiling": round(lerp(1.0, max(0.88, ref_dna["luma"]["p95_white_point"]), strength), 4),
            "contrast_s_curve": round(
                lerp(1.0, clamp(ref_dna["luma"]["std"] / max(0.12, base["luma"]["std"]), 0.82, 1.25), strength), 4
            ),
            "saturation": sat_val,
        },
    }


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    t = clamp((x - edge0) / max(1e-6, edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def generate_3d_cube_lut(
    recipe: dict,
    output_cube_path: Path,
    title: str = "VamshiCreates_RefGrade_DNA",
    grid_size: int = 33,
    include_cdl_in_lut: bool = False,
) -> Path:
    """
    Write a standard 33x33x33 3D .cube LUT file.
    By default, since ASC-CDL is applied on Node 1 in DaVinci Resolve, the 3D LUT provides
    the secondary split-toning, filmic S-curve roll-off, and chromatic refinement (or can bake
    both CDL + split-toning when include_cdl_in_lut=True).
    """
    output_cube_path = Path(output_cube_path).resolve()
    output_cube_path.parent.mkdir(parents=True, exist_ok=True)

    st = recipe["lut_split_tone"]
    cdl = recipe["cdl"]
    slope = cdl["slope_vec"]
    offset = cdl["offset_vec"]
    power = cdl["power_vec"]

    sh_cast = st["shadow_cast"]
    mid_cast = st["midtone_cast"]
    hi_cast = st["highlight_cast"]
    blk_floor = st["black_floor"]
    wht_ceil = st["white_ceiling"]
    contrast = st["contrast_s_curve"]
    sat = st["saturation"] if include_cdl_in_lut else lerp(1.0, st["saturation"], 0.35)

    lines = [
        f'TITLE "{title}"',
        "# Generated by davinci-color-dna (Reference-to-Clip Color Science Engine)",
        f"LUT_3D_SIZE {grid_size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
        "",
    ]

    denom = float(grid_size - 1)
    for b_idx in range(grid_size):
        b_in = b_idx / denom
        for g_idx in range(grid_size):
            g_in = g_idx / denom
            for r_idx in range(grid_size):
                r_in = r_idx / denom
                rgb = [r_in, g_in, b_in]

                if include_cdl_in_lut:
                    for c in range(3):
                        v = clamp(rgb[c] * slope[c] + offset[c], 0.0, 1.0)
                        rgb[c] = math.pow(v, power[c]) if v > 0.0 else 0.0

                y = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]

                # Zone weights (Shadows / Midtones / Highlights)
                w_sh = 1.0 - smoothstep(0.08, 0.42, y)
                w_hi = smoothstep(0.55, 0.92, y)
                w_mid = max(0.0, 1.0 - w_sh - w_hi)

                for c in range(3):
                    v = rgb[c]
                    # S-curve contrast around midpoint 0.46
                    centered = v - 0.46
                    v = 0.46 + centered * contrast
                    # Apply 3-zone split-tone color cast
                    v += (
                        w_sh * sh_cast[c] * 0.55
                        + w_mid * mid_cast[c] * 0.40
                        + w_hi * hi_cast[c] * 0.55
                    )
                    # Filmic toe (black floor) & shoulder (highlight roll-off)
                    v = lerp(blk_floor, wht_ceil, clamp(v, 0.0, 1.0))
                    rgb[c] = clamp(v, 0.0, 1.0)

                # Luma-preserving saturation refinement
                y_new = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
                for c in range(3):
                    rgb[c] = clamp(y_new + (rgb[c] - y_new) * sat, 0.0, 1.0)

                lines.append(f"{rgb[0]:.6f} {rgb[1]:.6f} {rgb[2]:.6f}")

    output_cube_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_cube_path


def create_comparison_strip(
    before_path: Path, ref_path: Path, after_path: Path, out_path: Path
) -> str:
    """Create a side-by-side Before | Reference | After visual verification image."""
    try:
        from PIL import Image, ImageDraw

        imgs = []
        labels = ["BEFORE (UNGRADED)", "REFERENCE IMAGE", "AFTER (DAVINCI GRADE)"]
        paths = [before_path, ref_path, after_path]
        target_h = 540
        for p in paths:
            if p and Path(p).exists():
                with Image.open(p) as im:
                    im_rgb = im.convert("RGB")
                    w, h = im_rgb.size
                    new_w = int(round(w * (target_h / float(max(1, h)))))
                    imgs.append(im_rgb.resize((new_w, target_h)))
            else:
                imgs.append(Image.new("RGB", (960, target_h), (18, 18, 24)))

        pad = 16
        header_h = 48
        total_w = sum(im.size[0] for im in imgs) + pad * 4
        total_h = target_h + header_h + pad * 2
        canvas = Image.new("RGB", (total_w, total_h), (12, 14, 20))
        draw = ImageDraw.Draw(canvas)

        x_cur = pad
        for idx, im in enumerate(imgs):
            draw.text((x_cur + 12, 16), labels[idx], fill=(240, 244, 255))
            canvas.paste(im, (x_cur, header_h))
            x_cur += im.size[0] + pad

        out_path = Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path)
        return str(out_path)
    except Exception:
        return ""


def analyze_and_build_grade_package(
    reference_path: str,
    source_frame_path: str = "",
    output_dir: str = ".davinci-color-dna",
    look_name: str = "RefGrade_DNA",
    strength: float = 1.0,
) -> dict:
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    ref_p = Path(reference_path).resolve()
    ref_pixels = load_image_rgb_float(ref_p)
    ref_dna = analyze_image_color_dna(ref_pixels)

    src_dna = None
    if source_frame_path and Path(source_frame_path).exists():
        src_pixels = load_image_rgb_float(Path(source_frame_path).resolve())
        src_dna = analyze_image_color_dna(src_pixels)

    recipe = compute_cdl_and_lut_recipe(ref_dna, src_dna, strength=strength)

    safe_name = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in look_name)
    secondary_cube = out_dir / f"{safe_name}_SplitTone.cube"
    full_cube = out_dir / f"{safe_name}_FullLook.cube"

    generate_3d_cube_lut(recipe, secondary_cube, title=f"{safe_name}_SplitTone", include_cdl_in_lut=False)
    generate_3d_cube_lut(recipe, full_cube, title=f"{safe_name}_FullLook", include_cdl_in_lut=True)

    report = {
        "look_name": safe_name,
        "reference_image": str(ref_p),
        "source_frame": str(Path(source_frame_path).resolve()) if source_frame_path and Path(source_frame_path).exists() else None,
        "reference_color_dna": ref_dna,
        "source_color_dna": src_dna,
        "recipe": recipe,
        "luts": {
            "split_tone_cube": str(secondary_cube),
            "full_look_cube": str(full_cube),
        },
    }

    report_json_path = out_dir / f"{safe_name}_grade_plan.json"
    report_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["grade_plan_json"] = str(report_json_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze reference image Color DNA and generate CDL + 3D .cube LUT")
    parser.add_argument("--reference", required=True, help="Path to the color-graded reference image")
    parser.add_argument("--source-frame", default="", help="Optional path to the ungraded source clip still")
    parser.add_argument("--output-dir", default=".davinci-color-dna", help="Output directory for LUTs and JSON plan")
    parser.add_argument("--look-name", default="RefGrade_DNA", help="Name prefix for generated LUT and grade plan")
    parser.add_argument("--strength", type=float, default=1.0, help="Grade intensity from 0.0 to 1.5 (default: 1.0)")
    args = parser.parse_args()

    try:
        res = analyze_and_build_grade_package(
            reference_path=args.reference,
            source_frame_path=args.source_frame,
            output_dir=args.output_dir,
            look_name=args.look_name,
            strength=args.strength,
        )
        print(json.dumps(res, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
