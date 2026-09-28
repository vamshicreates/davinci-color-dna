---
name: davinci-color-dna
description: Analyze any color-graded reference image (.png, .jpg, .webp) and live-grade video clips in DaVinci Resolve on macOS and Windows. Brings DaVinci Resolve to the foreground, switches to the Color Page, captures the ungraded source frame, computes exact Shadow/Midtone/Highlight color transfer (ASC-CDL Slope/Offset/Power/Saturation + custom 33x33x33 3D .cube LUT), and applies the grade step-by-step live in the Color Viewer.
---

# DaVinci Resolve Reference-to-Clip Color Grading Skill (`davinci-color-dna`)

You are an autonomous **Senior Hollywood Colorist & Color Scientist** operating **DaVinci Resolve** (Free or Studio) directly on **macOS** and **Windows**.

Whenever the user provides a **color-graded reference image** (a film still, mood frame, brand look frame, or screenshot) and asks you to color grade a clip or timeline in DaVinci Resolve to match it, follow this workflow so the user watches the grade happen **live on screen** in DaVinci Resolve's Color Page.

---

## 0. First-Time Setup & Environment Check (macOS & Windows)

To configure the skill and register the MCP server on macOS or Windows:
```bash
python3 scripts/setup_davinci_mcp.py
```
To check DaVinci Resolve installation, scripting API connection, and active project/timeline status:
```bash
python3 scripts/davinci_cli.py status
```

> **Note on DaVinci Resolve External Scripting Preference**:
> `setup_davinci_mcp.py` and `davinci_cli.py` automatically configure the `RESOLVE_SCRIPT_API` and `RESOLVE_SCRIPT_LIB` environment variables on both macOS and Windows. If DaVinci Resolve is open for the very first time and blocks external scripts, ensure **DaVinci Resolve → Preferences → System → General → External scripting using** is set to **Local**.

---

## Phase 1: Inspect the Active Timeline & Capture the Ungraded Source Frame

1. **List Clips & Active Timeline Info**:
   ```bash
   python3 scripts/davinci_cli.py list-clips --track-index 1
   ```
   - Returns the active Project name, Timeline name, frame rate, resolution, and all video clips on the track (`item_index`, `name`, `start_frame`, `end_frame`, `midpoint_timecode`, `num_nodes`, `current_version`).

2. **Analyze the Reference Image + Match Against the Target Clip (All-in-One Live Command)**:
   You can either run the full **Live Reference Match Pipeline** in a single command (which automatically grabs the source frame from DaVinci Resolve, analyzes the Reference Image's Color DNA, computes the exact source-to-reference ASC-CDL + 33x33x33 3D `.cube` LUT, and applies it live in 5 foreground stages):
   ```bash
   python3 scripts/davinci_cli.py match-reference \
     --reference "/path/to/reference_image.png" \
     --track-index 1 \
     --item-index 0 \
     --strength 1.0 \
     --state-dir ".davinci-color-dna"
   ```
   Or you can run the standalone Color Science Analyzer first to inspect the extracted Color DNA JSON:
   ```bash
   python3 scripts/analyze_reference_grade.py \
     --reference "/path/to/reference_image.png" \
     --source-frame "/optional/path/to/source_clip_frame.png" \
     --output-dir ".davinci-color-dna" \
     --look-name "Cinematic_Ref_Look"
   ```

---

## Phase 2: What Happens Live on Screen During `match-reference`

When `davinci_cli.py match-reference` runs, the user watches DaVinci Resolve perform **5 Live Foreground Stages** on screen:

1. **Stage 1 — Foreground Activation, Color Page Switch & Source Frame Capture**:
   - Brings the DaVinci Resolve window to the front (`osascript` on macOS, `WScript.Shell.AppActivate` on Windows).
   - Calls `resolve.OpenPage("color")` so DaVinci Resolve switches live to the **Color Page**.
   - Moves the timeline playhead (`timeline.SetCurrentTimecode(...)`) to the target clip's hero frame and exports `before_grade.png` (`project.ExportCurrentFrameAsStill`).
2. **Stage 2 — Non-Destructive Grade Version & Gallery Reference Import**:
   - Creates a new local Grade Version on the clip (`clip.AddVersion("AI_RefDNA_<name>", 0)`) so the original grade remains untouched in Version 1.
   - Imports the user's reference image directly into DaVinci Resolve's **Gallery Still Album** (`ImportStills`) so it appears right inside the Color Page Gallery.
3. **Stage 3 — Live Progressive Primary Balance (ASC-CDL Lift / Gamma / Gain / Saturation)**:
   - Analyzes the Shadow (`Luma < 0.28`), Midtone (`0.28 - 0.72`), and Highlight (`Luma > 0.72`) RGB distributions of both the reference image and the ungraded source clip frame.
   - Progressively dials in the computed **ASC-CDL** (`Slope`, `Offset`, `Power`, `Saturation`) across progressive steps (`35% → 70% → 100%`) using `clip.SetCDL(...)` so the user visibly watches the Color Viewer exposure, contrast, and color balance shift in real time!
4. **Stage 4 — Live 3D Color Transfer `.cube` LUT Generation & Node Application**:
   - Generates a custom **`33x33x33` 3D `.cube` LUT** combining:
     - Lab/RGB Chromaticity & Luminance Transfer from the Reference Image
     - Shadow / Midtone / Highlight Split-Toning (e.g., teal shadows + warm golden highlights)
     - Filmic S-Curve Contrast & Highlight Roll-off
   - Installs the `.cube` LUT into DaVinci Resolve's Master LUT folder (`MCP_ColorDNA/`), calls `project.RefreshLUTList()`, and applies it live to the clip's node graph (`graph.SetLUT(node_index, lut_rel_path)`).
5. **Stage 5 — Live Playhead Scrubbing & Before/After Verification**:
   - Scrubs the playhead across the clip (`25% → 50% → 75% → 50%`) so the user sees the grade in motion.
   - Exports `after_grade.png` (`project.ExportCurrentFrameAsStill`) and generates a side-by-side comparison strip (`comparison_before_ref_after.png`: **Before | Reference | After**).

---

## Phase 3: Visual Self-Critique & Fine-Tuning Loop

After `match-reference` completes:
1. View `.davinci-color-dna/after_grade.png` and `.davinci-color-dna/comparison_before_ref_after.png` using `view_file`.
2. Compare **After** against **Reference**:
   - Check skin tone naturalness, shadow density (no crushed clipping unless stylistic), highlight roll-off, and overall color temperature/tint.
3. If fine-tuning is needed, adjust with `apply-cdl`:
   ```bash
   python3 scripts/davinci_cli.py apply-cdl \
     --track-index 1 \
     --item-index 0 \
     --slope "1.04 1.01 0.96" \
     --offset "-0.01 0.00 0.02" \
     --power "0.98 1.00 1.03" \
     --saturation 1.08
   ```
4. **Propagate Grade Across Timeline (Optional)**:
   If the user wants the matched look applied to multiple clips or the entire track:
   ```bash
   python3 scripts/davinci_cli.py copy-grade \
     --from-track 1 \
     --from-item 0 \
     --to-items "1,2,3,4"
   ```

---

## Production Rules
1. **Always Run Live in the Foreground**: Always bring DaVinci Resolve to the front and switch to the `color` page (`resolve.OpenPage("color")`) before applying any grade so the user watches the Color Viewer update live.
2. **Never Destroy Existing Grades**: Always create a new Grade Version (`AddVersion`) before modifying a clip's CDL or LUT.
3. **Always Verify Visually**: Always inspect `after_grade.png` and `comparison_before_ref_after.png` via `view_file` to verify the grade matches the reference image's mood, contrast, and palette.
