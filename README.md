# DaVinci Resolve Reference-to-Clip Color Grading (`davinci-color-dna`)

**Cross-Platform (macOS & Windows) AI Agent Skill & MCP Server for DaVinci Resolve.**

Drop in **any color-graded reference image** (`.png`, `.jpg`, `.webp`) and watch DaVinci Resolve come to the foreground, switch to the **Color Page**, capture your ungraded clip frame, compute the exact **Shadow / Midtone / Highlight Color DNA** (ASC-CDL Lift/Gamma/Gain/Saturation + custom `33x33x33` 3D `.cube` Split-Tone LUT), and apply the grade **live on screen** in the Color Viewer.

---

## Key Features

- **3-Zone Color Science & Reference Look Engine (`analyze_reference_grade.py`)**:
  - Analyzes both your **Color-Graded Reference Image** and the **Ungraded Source Clip Frame** exported directly from your DaVinci Resolve timeline.
  - Separates pixel distributions into **Shadows** (`Luma < 0.28`), **Midtones** (`0.28 - 0.72`), and **Highlights** (`Luma > 0.72`) to solve for:
    1. **Primary ASC-CDL (`Slope`, `Offset`, `Power`, `Saturation`)** for DaVinci Resolve's `timeline_item.SetCDL(...)`.
    2. **A Custom `33x33x33` 3D `.cube` LUT** with smooth zone-weighted split-toning (e.g., teal/cyan shadows + warm golden highlights), filmic toe/shoulder roll-off, and S-curve contrast.
- **5-Stage Live Foreground Execution (`davinci_cli.py`)**:
  - Works on **macOS** and **Windows**:
    1. **Stage 1**: Brings DaVinci Resolve to the front, switches live to the **Color Page** (`resolve.OpenPage("color")`), scrubs to the target clip, and exports `before_grade.png`.
    2. **Stage 2**: Creates a non-destructive Grade Version (`AddVersion`) and imports the reference image into DaVinci Resolve's **Gallery Still Album** (`ImportStills`).
    3. **Stage 3**: Progressively dials in the computed **ASC-CDL** (`35% -> 70% -> 100%`) so the user visibly watches exposure, contrast, and color balance transform live in the Color Viewer.
    4. **Stage 4**: Installs the custom `33x33x33` `.cube` LUT into DaVinci Resolve's Master LUT directory (`MCP_ColorDNA/`), refreshes the LUT list, and applies it live to the node graph (`graph.SetLUT`).
    5. **Stage 5**: Scrubs the playhead across the clip (`25% -> 65% -> 45%`), exports `after_grade.png`, and builds a side-by-side **`Before | Reference | After`** verification strip.

---

## One-Time Prerequisite in DaVinci Resolve

Open DaVinci Resolve once and ensure:
**DaVinci Resolve → Preferences → System → General → External scripting using** is set to **`Local`**.

---

## Quick Installation & Usage Prompts (macOS & Windows)

### Prompt 1 — Install Skill & MCP Server
```text
Install the DaVinci Resolve Color DNA skill from https://github.com/vamshicreates/davinci-color-dna into my global skills directory (~/.gemini/config/skills/davinci-color-dna and ~/.agents/skills/davinci-color-dna) and run `python3 scripts/setup_davinci_mcp.py` so DaVinci Resolve is connected and ready.
```

### Prompt 2 — Grade Active Clip Live from a Reference Image
```text
Use the davinci-color-dna skill.
I have attached / provided a color-graded reference image at: /path/to/reference_image.png
Bring DaVinci Resolve to the front, switch to the Color Page, and grade my clip on Video Track 1 (Clip 0) LIVE on screen to match the exact color palette, contrast, shadow tint, and highlight roll-off of this reference image. Show me the Before | Reference | After comparison when done.
```

---

## Repository Structure

```text
davinci-color-dna/
├── SKILL.md                           # Agent instructions & live grading workflow
├── README.md                          # Documentation & copy-paste prompts
├── LICENSE                            # MIT License
└── scripts/
    ├── analyze_reference_grade.py     # 3-Zone Color Science Solver & 33x33x33 .cube LUT generator
    ├── davinci_cli.py                 # Cross-platform (macOS + Windows) Live DaVinci Resolve bridge
    ├── davinci_mcp_server.py          # Zero-dependency JSON-RPC 2.0 MCP Server
    └── setup_davinci_mcp.py           # Auto-installer for Antigravity, Claude, and Cursor
```
