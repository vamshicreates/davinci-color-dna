#!/usr/bin/env python3
"""
Model Context Protocol (MCP) stdio server for DaVinci Resolve Reference-to-Clip Color Grading.
Works on macOS and Windows.
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CLI_SCRIPT = SCRIPT_DIR / "davinci_cli.py"
ANALYZE_SCRIPT = SCRIPT_DIR / "analyze_reference_grade.py"

TOOLS = [
    {
        "name": "davinci_status",
        "description": "Check DaVinci Resolve installation, Python scripting API connection, and active project/timeline on macOS or Windows.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "davinci_list_clips",
        "description": "List all video clips on a track in the active DaVinci Resolve timeline with frame ranges, timecodes, node counts, and grade versions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {
                    "type": "integer",
                    "description": "1-based video track index (default: 1).",
                }
            },
        },
    },
    {
        "name": "davinci_analyze_reference_image",
        "description": "Analyze a color-graded reference image (and optional source clip frame) to extract Shadow/Midtone/Highlight Color DNA, ASC-CDL values, and generate a 33x33x33 3D .cube LUT.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference_path": {
                    "type": "string",
                    "description": "Path to the color-graded reference image (.png, .jpg, .webp).",
                },
                "source_frame_path": {
                    "type": "string",
                    "description": "Optional path to an ungraded source clip frame.",
                },
                "look_name": {
                    "type": "string",
                    "description": "Name prefix for the generated LUT and grade plan.",
                },
                "strength": {
                    "type": "number",
                    "description": "Look intensity from 0.0 to 1.5 (default: 1.0).",
                },
            },
            "required": ["reference_path"],
        },
    },
    {
        "name": "davinci_match_reference_live",
        "description": "Bring DaVinci Resolve to the foreground, switch to the Color Page, capture the ungraded clip frame, compute exact Source-to-Reference Color DNA (ASC-CDL + 33x33x33 3D .cube LUT), apply the grade step-by-step LIVE in the Color Viewer, and export a Before | Reference | After comparison strip.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference_path": {
                    "type": "string",
                    "description": "Path to the color-graded reference image.",
                },
                "track_index": {
                    "type": "integer",
                    "description": "1-based video track index (default: 1).",
                },
                "item_index": {
                    "type": "integer",
                    "description": "0-based clip index on the track (default: 0).",
                },
                "strength": {
                    "type": "number",
                    "description": "Grade intensity from 0.0 to 1.5 (default: 1.0).",
                },
                "look_name": {
                    "type": "string",
                    "description": "Optional custom name for the generated LUT and grade version.",
                },
            },
            "required": ["reference_path"],
        },
    },
    {
        "name": "davinci_apply_cdl",
        "description": "Fine-tune ASC-CDL primary color parameters (Slope, Offset, Power, Saturation) live on a clip in DaVinci Resolve.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "integer", "description": "1-based video track index (default: 1)."},
                "item_index": {"type": "integer", "description": "0-based clip index (default: 0)."},
                "node_index": {"type": "integer", "description": "1-based node index (default: 1)."},
                "slope": {"type": "string", "description": "RGB Slope string, e.g. '1.05 1.00 0.95'."},
                "offset": {"type": "string", "description": "RGB Offset string, e.g. '-0.01 0.00 0.02'."},
                "power": {"type": "string", "description": "RGB Power string, e.g. '0.98 1.00 1.02'."},
                "saturation": {"type": "number", "description": "Saturation multiplier, e.g. 1.08."},
            },
        },
    },
    {
        "name": "davinci_copy_grade",
        "description": "Copy the matched reference grade from a source clip to multiple target clips on the timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_track": {"type": "integer", "description": "1-based source video track index (default: 1)."},
                "from_item": {"type": "integer", "description": "0-based source clip index (default: 0)."},
                "to_items": {
                    "type": "string",
                    "description": "Comma-separated 0-based target clip indices, e.g. '1,2,3,4'.",
                },
            },
            "required": ["to_items"],
        },
    },
]


def run_cmd(cmd: list, timeout: int = 180) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.stdout.strip() or proc.stderr.strip()


def handle_call_tool(name: str, args: dict) -> dict:
    try:
        if name == "davinci_status":
            out = run_cmd([sys.executable, str(CLI_SCRIPT), "status"])
        elif name == "davinci_list_clips":
            cmd = [
                sys.executable,
                str(CLI_SCRIPT),
                "list-clips",
                "--track-index",
                str(args.get("track_index", 1)),
            ]
            out = run_cmd(cmd)
        elif name == "davinci_analyze_reference_image":
            cmd = [
                sys.executable,
                str(ANALYZE_SCRIPT),
                "--reference",
                args["reference_path"],
                "--look-name",
                args.get("look_name", "RefGrade_DNA"),
                "--strength",
                str(args.get("strength", 1.0)),
            ]
            if args.get("source_frame_path"):
                cmd += ["--source-frame", args["source_frame_path"]]
            out = run_cmd(cmd)
        elif name == "davinci_match_reference_live":
            cmd = [
                sys.executable,
                str(CLI_SCRIPT),
                "match-reference",
                "--reference",
                args["reference_path"],
                "--track-index",
                str(args.get("track_index", 1)),
                "--item-index",
                str(args.get("item_index", 0)),
                "--strength",
                str(args.get("strength", 1.0)),
            ]
            if args.get("look_name"):
                cmd += ["--look-name", args["look_name"]]
            out = run_cmd(cmd)
        elif name == "davinci_apply_cdl":
            cmd = [
                sys.executable,
                str(CLI_SCRIPT),
                "apply-cdl",
                "--track-index",
                str(args.get("track_index", 1)),
                "--item-index",
                str(args.get("item_index", 0)),
                "--node-index",
                str(args.get("node_index", 1)),
                "--slope",
                args.get("slope", "1.0 1.0 1.0"),
                "--offset",
                args.get("offset", "0.0 0.0 0.0"),
                "--power",
                args.get("power", "1.0 1.0 1.0"),
                "--saturation",
                str(args.get("saturation", 1.0)),
            ]
            out = run_cmd(cmd)
        elif name == "davinci_copy_grade":
            cmd = [
                sys.executable,
                str(CLI_SCRIPT),
                "copy-grade",
                "--from-track",
                str(args.get("from_track", 1)),
                "--from-item",
                str(args.get("from_item", 0)),
                "--to-items",
                args["to_items"],
            ]
            out = run_cmd(cmd)
        else:
            return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}

        return {"content": [{"type": "text", "text": out}]}
    except Exception as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "davinci-color-dna", "version": "1.0.0"},
                },
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            result = handle_call_tool(params.get("name", ""), params.get("arguments", {}))
            resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
