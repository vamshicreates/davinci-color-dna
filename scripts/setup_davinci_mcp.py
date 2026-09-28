#!/usr/bin/env python3
"""
One-command cross-platform installer for davinci-color-dna (macOS & Windows).
Detects DaVinci Resolve, ensures the writable MCP_ColorDNA LUT folder exists,
and registers the MCP server in Antigravity / Claude / Cursor configs.
"""

import json
import os
import platform
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MCP_SERVER_SCRIPT = SCRIPT_DIR / "davinci_mcp_server.py"

sys.path.insert(0, str(SCRIPT_DIR))
from davinci_cli import get_resolve_paths  # noqa: E402


def get_mcp_config_paths() -> list:
    home = Path.home()
    paths = [
        home / ".gemini" / "antigravity" / "mcp_config.json",
        home / ".gemini" / "settings.json",
        home / ".cursor" / "mcp.json",
    ]
    if platform.system() == "Darwin":
        paths.append(
            home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
    elif platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    return paths


def register_mcp_server(resolve_info: dict) -> list:
    updated = []
    env_map = {}
    if resolve_info.get("api_dir"):
        env_map["RESOLVE_SCRIPT_API"] = resolve_info["api_dir"]
    if resolve_info.get("lib_path"):
        env_map["RESOLVE_SCRIPT_LIB"] = resolve_info["lib_path"]

    entry = {
        "command": sys.executable,
        "args": [str(MCP_SERVER_SCRIPT)],
        "env": env_map,
    }
    for cfg_path in get_mcp_config_paths():
        if not cfg_path.parent.exists():
            continue
        data = {}
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
            data["mcpServers"] = {}
        data["mcpServers"]["davinci-color-dna"] = entry
        try:
            cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            updated.append(str(cfg_path))
        except Exception:
            pass
    return updated


def ensure_lut_dirs(resolve_info: dict) -> list:
    ready_dirs = []
    for d in resolve_info.get("lut_dirs", []):
        try:
            sub = Path(d) / "MCP_ColorDNA"
            sub.mkdir(parents=True, exist_ok=True)
            ready_dirs.append(str(sub))
        except Exception:
            pass
    return ready_dirs


def main() -> int:
    info = get_resolve_paths()
    lut_dirs = ensure_lut_dirs(info)
    updated_configs = register_mcp_server(info)
    print(
        json.dumps(
            {
                "status": "ready",
                "os": info["os"],
                "davinci_installed": info["installed"],
                "app_path": info["app_path"],
                "lut_folders_ready": lut_dirs,
                "mcp_server": str(MCP_SERVER_SCRIPT),
                "updated_configs": updated_configs,
                "external_scripting_reminder": (
                    "In DaVinci Resolve, ensure Preferences -> System -> General -> "
                    "External scripting using is set to 'Local'."
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
