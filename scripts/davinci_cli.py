#!/usr/bin/env python3
"""
Cross-platform (macOS & Windows) Live Color Grading CLI for DaVinci Resolve.
Brings DaVinci Resolve to the foreground, switches to the Color Page, captures the
ungraded clip frame, computes Reference-to-Clip Color DNA (ASC-CDL + 33x33x33 3D .cube LUT),
and applies the grade step-by-step live in the Color Viewer.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from analyze_reference_grade import (  # noqa: E402
    analyze_and_build_grade_package,
    create_comparison_strip,
    lerp,
)


def get_resolve_paths() -> dict:
    """Return platform-specific DaVinci Resolve binary, scripting API, and LUT paths."""
    sys_name = platform.system()
    home = Path.home()

    if sys_name == "Darwin":
        app_path = "/Applications/DaVinci Resolve/DaVinci Resolve.app"
        api_dir = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        lib_path = f"{app_path}/Contents/Libraries/Fusion/fusionscript.so"
        lut_dirs = [
            Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"),
            home / "Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT",
        ]
        return {
            "os": "macOS",
            "installed": Path(app_path).exists(),
            "app_path": app_path,
            "api_dir": api_dir,
            "lib_path": lib_path,
            "modules_dir": f"{api_dir}/Modules",
            "lut_dirs": lut_dirs,
        }

    if sys_name == "Windows":
        prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        prog_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
        exe_path = os.path.join(prog_files, "Blackmagic Design", "DaVinci Resolve", "Resolve.exe")
        api_dir = os.path.join(prog_data, "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting")
        lib_path = os.path.join(prog_files, "Blackmagic Design", "DaVinci Resolve", "fusionscript.dll")
        lut_dirs = [
            Path(prog_data) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "LUT",
            Path(appdata) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "LUT",
        ]
        return {
            "os": "Windows",
            "installed": Path(exe_path).exists(),
            "app_path": exe_path,
            "api_dir": api_dir,
            "lib_path": lib_path,
            "modules_dir": os.path.join(api_dir, "Modules"),
            "lut_dirs": lut_dirs,
        }

    return {
        "os": sys_name,
        "installed": False,
        "app_path": None,
        "api_dir": None,
        "lib_path": None,
        "modules_dir": None,
        "lut_dirs": [],
    }


def bring_resolve_to_front() -> None:
    """Activate the DaVinci Resolve window to the foreground on macOS or Windows."""
    sys_name = platform.system()
    try:
        if sys_name == "Darwin":
            subprocess.run(
                ["osascript", "-e", 'tell application "DaVinci Resolve" to activate'],
                capture_output=True,
                timeout=8,
            )
        elif sys_name == "Windows":
            vbs = 'Set sh = CreateObject("WScript.Shell")\r\nsh.AppActivate "DaVinci Resolve"\r\n'
            with tempfile.NamedTemporaryFile("w", suffix=".vbs", delete=False, encoding="utf-8") as vf:
                vf.write(vbs)
                vbs_path = vf.name
            subprocess.run(["cscript", "//Nologo", vbs_path], capture_output=True, timeout=8)
            try:
                os.remove(vbs_path)
            except OSError:
                pass
    except Exception:
        pass


def connect_resolve() -> tuple:
    """Configure environment variables and connect to DaVinci Resolve's Python API."""
    info = get_resolve_paths()
    if not info["installed"]:
        return None, f"DaVinci Resolve is not installed on this {info['os']} machine."

    os.environ["RESOLVE_SCRIPT_API"] = info["api_dir"]
    os.environ["RESOLVE_SCRIPT_LIB"] = info["lib_path"]
    if info["modules_dir"] and info["modules_dir"] not in sys.path:
        sys.path.insert(0, info["modules_dir"])

    try:
        import DaVinciResolveScript as dvr_script  # type: ignore

        resolve = dvr_script.scriptapp("Resolve")
        if resolve is None:
            return (
                None,
                "DaVinci Resolve is either not running or External Scripting is not enabled. "
                "Open DaVinci Resolve and ensure Preferences -> System -> General -> External scripting using is set to 'Local'.",
            )
        return resolve, None
    except Exception as exc:
        return None, f"Failed to load DaVinciResolveScript module: {exc}"


def frames_to_timecode(frame: int, fps: float) -> str:
    fps_int = max(1, int(round(float(fps))))
    total_frames = max(0, int(round(frame)))
    ff = total_frames % fps_int
    total_sec = total_frames // fps_int
    ss = total_sec % 60
    mm = (total_sec // 60) % 60
    hh = total_sec // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def install_lut_into_resolve(cube_path: Path, look_name: str) -> dict:
    """Install a .cube file into DaVinci Resolve's Master/User LUT directory."""
    info = get_resolve_paths()
    safe_filename = f"{look_name}.cube"
    installed_paths = []
    rel_path = f"MCP_ColorDNA/{safe_filename}"

    for lut_root in info["lut_dirs"]:
        try:
            target_dir = lut_root / "MCP_ColorDNA"
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / safe_filename
            shutil.copy2(cube_path, dest)
            installed_paths.append(str(dest))
        except Exception:
            continue

    return {
        "installed": len(installed_paths) > 0,
        "installed_paths": installed_paths,
        "set_lut_rel_path": rel_path,
        "source_cube": str(cube_path),
    }


def get_active_context(resolve):
    pm = resolve.GetProjectManager()
    proj = pm.GetCurrentProject() if pm else None
    tl = proj.GetCurrentTimeline() if proj else None
    fps = 24.0
    if tl:
        try:
            fps = float(tl.GetSetting("timelineFrameRate") or 24.0)
        except Exception:
            fps = 24.0
    return pm, proj, tl, fps


def cmd_status(_args) -> int:
    info = get_resolve_paths()
    resolve, err = connect_resolve()
    result = {
        "os": info["os"],
        "installed": info["installed"],
        "app_path": info["app_path"],
        "connected": resolve is not None,
    }
    if err:
        result["connection_note"] = err
    if resolve:
        _, proj, tl, fps = get_active_context(resolve)
        result["current_page"] = resolve.GetCurrentPage()
        result["project"] = proj.GetName() if proj else None
        result["timeline"] = tl.GetName() if tl else None
        result["fps"] = fps
    print(json.dumps(result, indent=2))
    return 0 if info["installed"] else 1


def cmd_list_clips(args) -> int:
    resolve, err = connect_resolve()
    if not resolve:
        print(json.dumps({"error": err}, indent=2))
        return 1

    _, proj, tl, fps = get_active_context(resolve)
    if not tl:
        print(json.dumps({"error": "No active timeline open in DaVinci Resolve."}, indent=2))
        return 1

    track_idx = int(args.track_index)
    items = tl.GetItemListInTrack("video", track_idx) or []
    clips_out = []
    for idx, item in enumerate(items):
        start_f = item.GetStart()
        end_f = item.GetEnd()
        mid_f = int((start_f + end_f) // 2)
        graph = item.GetNodeGraph()
        num_nodes = graph.GetNumNodes() if graph else 0
        ver = None
        try:
            ver = item.GetCurrentVersion()
        except Exception:
            pass
        clips_out.append({
            "item_index": idx,
            "track_index": track_idx,
            "name": item.GetName(),
            "start_frame": start_f,
            "end_frame": end_f,
            "duration_frames": item.GetDuration(),
            "midpoint_timecode": frames_to_timecode(mid_f, fps),
            "num_nodes": num_nodes,
            "current_version": ver,
        })

    print(
        json.dumps(
            {
                "project": proj.GetName(),
                "timeline": tl.GetName(),
                "fps": fps,
                "track_index": track_idx,
                "clip_count": len(clips_out),
                "clips": clips_out,
            },
            indent=2,
        )
    )
    return 0


def cmd_match_reference(args) -> int:
    """
    Full 5-Stage Live Foreground Reference-to-Clip Color Grading Pipeline:
      Stage 1: Bring DaVinci Resolve to front, switch to Color Page, scrub to target clip, export before_grade.png
      Stage 2: Add non-destructive Grade Version & import Reference Still into Resolve Gallery
      Stage 3: Analyze Reference vs. Source Frame & progressively dial in ASC-CDL live on screen
      Stage 4: Install & apply custom 33x33x33 3D .cube LUT live on node graph
      Stage 5: Scrub playhead across clip, export after_grade.png, and build Before | Reference | After strip
    """
    ref_path = Path(args.reference).resolve()
    if not ref_path.exists():
        print(json.dumps({"error": f"Reference image not found: {ref_path}"}, indent=2))
        return 1

    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    look_name = args.look_name or f"RefDNA_{ref_path.stem}"
    safe_look = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in look_name)[:40]

    resolve, err = connect_resolve()
    if not resolve:
        # Offline fallback: still generate full Color DNA + .cube LUT + CDL recipe so nothing is blocked
        offline_pkg = analyze_and_build_grade_package(
            reference_path=str(ref_path),
            source_frame_path="",
            output_dir=str(state_dir),
            look_name=safe_look,
            strength=args.strength,
        )
        lut_install = install_lut_into_resolve(Path(offline_pkg["luts"]["full_look_cube"]), f"{safe_look}_Full")
        print(
            json.dumps(
                {
                    "status": "prepared_offline",
                    "note": err,
                    "grade_package": offline_pkg,
                    "lut_install": lut_install,
                },
                indent=2,
            )
        )
        return 0

    _, proj, tl, fps = get_active_context(resolve)
    if not tl:
        print(json.dumps({"error": "No active timeline in DaVinci Resolve."}, indent=2))
        return 1

    track_idx = int(args.track_index)
    item_idx = int(args.item_index)
    items = tl.GetItemListInTrack("video", track_idx) or []
    if item_idx < 0 or item_idx >= len(items):
        print(
            json.dumps(
                {"error": f"Clip item_index {item_idx} out of range on video track {track_idx} (count={len(items)})."},
                indent=2,
            )
        )
        return 1

    clip = items[item_idx]
    start_f = clip.GetStart()
    end_f = clip.GetEnd()
    hero_f = int(start_f + (end_f - start_f) * 0.45)
    hero_tc = frames_to_timecode(hero_f, fps)

    # =========================================================================
    # STAGE 1: Bring Resolve to Front, Switch to Color Page & Export Before Still
    # =========================================================================
    bring_resolve_to_front()
    resolve.OpenPage("color")
    time.sleep(0.35)
    tl.SetCurrentTimecode(hero_tc)
    time.sleep(0.25)

    before_png = state_dir / f"{safe_look}_before.png"
    try:
        proj.ExportCurrentFrameAsStill(str(before_png))
    except Exception:
        pass

    # =========================================================================
    # STAGE 2: Create Non-Destructive Grade Version & Import Reference Still
    # =========================================================================
    version_name = f"AI_{safe_look}_{int(time.time()) % 10000}"
    try:
        clip.AddVersion(version_name, 0)
        clip.LoadVersionByName(version_name, 0)
    except Exception:
        pass

    gallery_imported = False
    try:
        gal = proj.GetGallery()
        if gal:
            album = gal.GetCurrentStillAlbum()
            if album:
                gallery_imported = bool(album.ImportStills([str(ref_path)]))
    except Exception:
        pass

    # =========================================================================
    # STAGE 3: Compute Color DNA & Progressively Dial In ASC-CDL Live on Screen
    # =========================================================================
    grade_pkg = analyze_and_build_grade_package(
        reference_path=str(ref_path),
        source_frame_path=str(before_png) if before_png.exists() else "",
        output_dir=str(state_dir),
        look_name=safe_look,
        strength=args.strength,
    )
    cdl_target = grade_pkg["recipe"]["cdl"]
    s_vec = cdl_target["slope_vec"]
    o_vec = cdl_target["offset_vec"]
    p_vec = cdl_target["power_vec"]
    sat_target = cdl_target["saturation_float"]

    # Animate CDL in 3 visible steps (35% -> 70% -> 100%) so the user watches the grade dial in live
    cdl_applied = False
    for step_t in (0.35, 0.70, 1.0):
        cur_s = [lerp(1.0, s_vec[i], step_t) for i in range(3)]
        cur_o = [lerp(0.0, o_vec[i], step_t) for i in range(3)]
        cur_p = [lerp(1.0, p_vec[i], step_t) for i in range(3)]
        cur_sat = lerp(1.0, sat_target, step_t)
        cdl_map = {
            "NodeIndex": "1",
            "Slope": f"{cur_s[0]:.4f} {cur_s[1]:.4f} {cur_s[2]:.4f}",
            "Offset": f"{cur_o[0]:.4f} {cur_o[1]:.4f} {cur_o[2]:.4f}",
            "Power": f"{cur_p[0]:.4f} {cur_p[1]:.4f} {cur_p[2]:.4f}",
            "Saturation": f"{cur_sat:.4f}",
        }
        try:
            cdl_applied = bool(clip.SetCDL(cdl_map))
        except Exception:
            pass
        time.sleep(0.25)

    # =========================================================================
    # STAGE 4: Install & Apply Custom 33x33x33 3D .cube Split-Tone LUT Live
    # =========================================================================
    graph = clip.GetNodeGraph()
    num_nodes = graph.GetNumNodes() if graph else 1
    # If CDL succeeded on Node 1, apply the complementary SplitTone .cube LUT;
    # if CDL was skipped, apply the FullLook .cube LUT that bakes both CDL + SplitTone.
    chosen_cube = (
        Path(grade_pkg["luts"]["split_tone_cube"])
        if cdl_applied
        else Path(grade_pkg["luts"]["full_look_cube"])
    )
    lut_install = install_lut_into_resolve(chosen_cube, safe_look)

    lut_applied = False
    lut_node_used = 1 if num_nodes < 2 else 2
    if graph and lut_install["installed"]:
        try:
            proj.RefreshLUTList()
        except Exception:
            pass
        time.sleep(0.2)
        for candidate_path in [lut_install["set_lut_rel_path"]] + lut_install["installed_paths"]:
            try:
                if graph.SetLUT(lut_node_used, candidate_path):
                    lut_applied = True
                    break
            except Exception:
                pass
        try:
            graph.SetNodeLabel(1, f"DNA_{safe_look}")
        except Exception:
            pass

    # =========================================================================
    # STAGE 5: Live Playhead Scrubbing & Before / Reference / After Verification
    # =========================================================================
    for frac in (0.25, 0.65, 0.45):
        f_pos = int(start_f + (end_f - start_f) * frac)
        try:
            tl.SetCurrentTimecode(frames_to_timecode(f_pos, fps))
        except Exception:
            pass
        time.sleep(0.22)

    after_png = state_dir / f"{safe_look}_after.png"
    try:
        proj.ExportCurrentFrameAsStill(str(after_png))
    except Exception:
        pass

    comp_strip = ""
    if before_png.exists() and after_png.exists():
        comp_strip = create_comparison_strip(
            before_png,
            ref_path,
            after_png,
            state_dir / f"{safe_look}_comparison_before_ref_after.png",
        )

    print(
        json.dumps(
            {
                "status": "success",
                "live_stages_completed": 5,
                "project": proj.GetName(),
                "timeline": tl.GetName(),
                "clip": {
                    "track_index": track_idx,
                    "item_index": item_idx,
                    "name": clip.GetName(),
                    "version_created": version_name,
                    "hero_timecode": hero_tc,
                },
                "applied": {
                    "cdl_applied": cdl_applied,
                    "cdl_values": cdl_target,
                    "lut_applied": lut_applied,
                    "lut_node": lut_node_used,
                    "lut_info": lut_install,
                    "gallery_still_imported": gallery_imported,
                },
                "reference_mood": grade_pkg["reference_color_dna"]["mood_description"],
                "artifacts": {
                    "before_png": str(before_png) if before_png.exists() else None,
                    "after_png": str(after_png) if after_png.exists() else None,
                    "comparison_strip_png": comp_strip or None,
                    "grade_plan_json": grade_pkg["grade_plan_json"],
                },
            },
            indent=2,
        )
    )
    return 0


def cmd_apply_cdl(args) -> int:
    resolve, err = connect_resolve()
    if not resolve:
        print(json.dumps({"error": err}, indent=2))
        return 1

    bring_resolve_to_front()
    resolve.OpenPage("color")
    _, _, tl, _ = get_active_context(resolve)
    if not tl:
        print(json.dumps({"error": "No active timeline in DaVinci Resolve."}, indent=2))
        return 1

    items = tl.GetItemListInTrack("video", int(args.track_index)) or []
    idx = int(args.item_index)
    if idx < 0 or idx >= len(items):
        print(json.dumps({"error": f"Clip index {idx} out of range."}, indent=2))
        return 1

    clip = items[idx]
    cdl_map = {
        "NodeIndex": str(args.node_index),
        "Slope": args.slope,
        "Offset": args.offset,
        "Power": args.power,
        "Saturation": str(args.saturation),
    }
    ok = bool(clip.SetCDL(cdl_map))
    print(json.dumps({"status": "success" if ok else "failed", "clip": clip.GetName(), "cdl": cdl_map}, indent=2))
    return 0 if ok else 1


def cmd_copy_grade(args) -> int:
    resolve, err = connect_resolve()
    if not resolve:
        print(json.dumps({"error": err}, indent=2))
        return 1

    bring_resolve_to_front()
    resolve.OpenPage("color")
    _, _, tl, _ = get_active_context(resolve)
    if not tl:
        print(json.dumps({"error": "No active timeline in DaVinci Resolve."}, indent=2))
        return 1

    items = tl.GetItemListInTrack("video", int(args.from_track)) or []
    src_idx = int(args.from_item)
    if src_idx < 0 or src_idx >= len(items):
        print(json.dumps({"error": f"Source clip index {src_idx} out of range."}, indent=2))
        return 1

    src_clip = items[src_idx]
    target_indices = [int(x.strip()) for x in args.to_items.split(",") if x.strip().isdigit()]
    target_clips = [items[i] for i in target_indices if 0 <= i < len(items) and i != src_idx]

    ok = bool(src_clip.CopyGrades(target_clips)) if target_clips else False
    print(
        json.dumps(
            {
                "status": "success" if ok else "failed",
                "source_clip": src_clip.GetName(),
                "copied_to_clips": [c.GetName() for c in target_clips],
            },
            indent=2,
        )
    )
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="DaVinci Resolve Live Foreground Color Grading CLI (macOS & Windows)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Check DaVinci Resolve installation and live scripting connection")

    p_list = sub.add_parser("list-clips", help="List clips on a video track in the active timeline")
    p_list.add_argument("--track-index", type=int, default=1, help="1-based video track index (default: 1)")

    p_match = sub.add_parser(
        "match-reference",
        help="Analyze a color-graded reference image and live-grade a clip in DaVinci Resolve on screen",
    )
    p_match.add_argument("--reference", required=True, help="Path to the color-graded reference image")
    p_match.add_argument("--track-index", type=int, default=1, help="1-based video track index (default: 1)")
    p_match.add_argument("--item-index", type=int, default=0, help="0-based clip index on track (default: 0)")
    p_match.add_argument("--strength", type=float, default=1.0, help="Look intensity 0.0 to 1.5 (default: 1.0)")
    p_match.add_argument("--look-name", default="", help="Optional custom name for the generated LUT & version")
    p_match.add_argument("--state-dir", default=".davinci-color-dna", help="Directory for stills, LUTs, and comparison strip")

    p_cdl = sub.add_parser("apply-cdl", help="Fine-tune ASC-CDL values live on a clip")
    p_cdl.add_argument("--track-index", type=int, default=1)
    p_cdl.add_argument("--item-index", type=int, default=0)
    p_cdl.add_argument("--node-index", type=int, default=1)
    p_cdl.add_argument("--slope", default="1.0 1.0 1.0")
    p_cdl.add_argument("--offset", default="0.0 0.0 0.0")
    p_cdl.add_argument("--power", default="1.0 1.0 1.0")
    p_cdl.add_argument("--saturation", type=float, default=1.0)

    p_copy = sub.add_parser("copy-grade", help="Copy the matched grade from one clip to other clips on the track")
    p_copy.add_argument("--from-track", type=int, default=1)
    p_copy.add_argument("--from-item", type=int, default=0)
    p_copy.add_argument("--to-items", required=True, help="Comma-separated 0-based clip indices, e.g. '1,2,3'")

    args = parser.parse_args()
    if args.command == "status":
        return cmd_status(args)
    if args.command == "list-clips":
        return cmd_list_clips(args)
    if args.command == "match-reference":
        return cmd_match_reference(args)
    if args.command == "apply-cdl":
        return cmd_apply_cdl(args)
    if args.command == "copy-grade":
        return cmd_copy_grade(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
