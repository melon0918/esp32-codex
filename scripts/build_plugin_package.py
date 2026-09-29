"""Build the self-contained Codex plugin payload from the ESP32 project."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = PROJECT_ROOT / "plugins" / "esp32-codex"


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"required plugin source directory missing: {source}")
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", ".pytest_cache"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, default=PLUGIN_ROOT)
    args = parser.parse_args()
    target = args.plugin_root.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    copy_tree(PROJECT_ROOT / "mcp-server", target / "mcp-server")
    copy_tree(PROJECT_ROOT / "tests", target / "tests")
    copy_tree(PROJECT_ROOT / "docs", target / "docs")
    copy_tree(PROJECT_ROOT / "web-panel" / "host", target / "web-panel" / "host")
    copy_tree(PROJECT_ROOT / "web-panel" / "prototype", target / "web-panel" / "prototype")
    scripts_source = PROJECT_ROOT / "plugins" / "esp32-codex" / "scripts"
    (target / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("launch_mock.cmd", "launch_bridge.cmd"):
        source = scripts_source / name
        destination = target / "scripts" / name
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
    # Prefer the bridge tracked with this repository so a clean clone builds
    # independently. Keep the sibling fallback for the existing ESP32 IDE tree.
    bridge_source = PROJECT_ROOT / "bridge.py"
    if not bridge_source.is_file():
        bridge_source = PROJECT_ROOT / ".." / "bridge.py"
    shutil.copy2(bridge_source.resolve(strict=True), target / "bridge.py")
    for name in ("requirements.txt", "requirements-bridge.txt"):
        shutil.copy2(PROJECT_ROOT / name, target / name)

    command = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"
    launcher = (target / "scripts" / "launch_bridge.cmd").resolve(strict=True)
    config = {
        "mcpServers": {
            "esp32-codex": {
                "command": command,
                "args": ["/d", "/c", str(launcher)],
                "enabled": True,
                "startup_timeout_sec": 30,
                "tool_timeout_sec": 1800,
            }
        }
    }
    config_path = target / ".mcp.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built ESP32 Codex plugin payload: {target}")
    print(f"MCP launcher (real bridge; explicit connect required): {launcher}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
