"""Bounded, read-only ESP32 workspace discovery matching the DSH profile rules."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


PROFILE_META = {
    "hiwonder": {"label": "幻尔小车", "entry": "/corex.py"},
    "generic": {"label": "通用 ESP32", "entry": "/main.py"},
}
GENERIC_PRESETS = (
    {
        "id": "quad",
        "label": "四足机器人",
        "files": ("quad.py", "robot.py", "robot_wifi.py"),
        "content": ("from quad import", "import robot_wifi", "RobotWifi("),
        "entry": "/main.py",
    },
)
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", ".idea",
    ".vscode", "dist", "build", ".dsh",
}
MAX_DEPTH = 3
MAX_PY_FILES = 48
MAX_CANDIDATES = 128
MAX_DIRECTORY_ENTRIES = 512
MAX_BOARD_JSON_BYTES = 64 * 1024
MAX_SOURCE_SCAN_BYTES = 128 * 1024


class WorkspaceError(ValueError):
    """An input path cannot be inspected safely."""


def _wsl_mount_to_windows_path(value: str) -> str | None:
    """Convert only the explicit WSL /mnt/<drive>/ form; leave other hosts untouched."""
    match = re.fullmatch(r"/mnt/([A-Za-z])(?:/(.*))?", value)
    if match is None:
        return None
    drive = match.group(1).upper()
    tail = (match.group(2) or "").replace("/", "\\")
    return f"{drive}:\\{tail}" if tail else f"{drive}:\\"


def normalize_workspace_path(value: str) -> str:
    """Normalize WSL mounted-drive inputs when this plugin runs on Windows."""
    if not isinstance(value, str) or os.name != "nt":
        return value
    return _wsl_mount_to_windows_path(value) or value


def absolute_directory(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceError("需要提供工作区绝对路径")
    if any(ord(character) < 32 for character in value):
        raise WorkspaceError("工作区路径不能包含控制字符")
    path = Path(normalize_workspace_path(value)).expanduser()
    if not path.is_absolute():
        raise WorkspaceError("工作区路径必须是绝对路径")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceError(f"工作区路径不存在或无法读取: {exc}") from exc
    if not resolved.is_dir():
        raise WorkspaceError("工作区路径不是目录")
    return resolved


def _collect_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending and len(files) < MAX_PY_FILES:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = []
                for index, entry in enumerate(iterator):
                    if index >= MAX_DIRECTORY_ENTRIES:
                        break
                    entries.append(entry)
                entries.sort(key=lambda item: item.name.casefold())
        except OSError:
            continue
        subdirectories: list[Path] = []
        for entry in entries:
            if len(files) >= MAX_PY_FILES:
                break
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".py"):
                    files.append(Path(entry.path))
                elif (
                    depth < MAX_DEPTH
                    and entry.is_dir(follow_symlinks=False)
                    and entry.name.casefold() not in SKIP_DIRS
                ):
                    subdirectories.append(Path(entry.path))
            except OSError:
                continue
        pending.extend((child, depth + 1) for child in reversed(subdirectories))
    return files


def _read_board_config(root: Path) -> dict[str, Any] | None:
    config_dir = root / "esp32-ide"
    config_path = config_dir / "board.json"
    try:
        if config_dir.is_symlink() or config_path.is_symlink():
            return None
        if config_path.stat().st_size > MAX_BOARD_JSON_BYTES:
            return None
        raw = config_path.read_text(encoding="utf-8-sig")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("type") not in PROFILE_META:
        return None
    profile = value["type"]
    meta = PROFILE_META[profile]
    label = value.get("label")
    entry = value.get("entry")
    if not isinstance(label, str) or not label.strip():
        label = meta["label"]
    if not isinstance(entry, str) or not entry.startswith("/"):
        entry = meta["entry"]
    return {
        "workspacePath": str(root),
        "profile": profile,
        "profileLabel": label,
        "entry": entry,
        "presetId": "",
        "claimed": True,
        "detection": "board.json",
    }


def detect_workspace(value: str | Path) -> dict[str, Any]:
    root = absolute_directory(str(value))
    configured = _read_board_config(root)
    if configured is not None:
        configured["files"] = _root_python_names(Path(configured["workspacePath"]))
        configured["detected"] = True
        return configured

    python_files = _collect_python_files(root)
    hit: dict[str, Any] | None = None

    if any(re.search(r"^(四路巡线|写入ESP32)\.py$", path.name, re.IGNORECASE) for path in python_files):
        hit = {
            "workspacePath": str(root),
            "profile": "hiwonder",
            "profileLabel": PROFILE_META["hiwonder"]["label"],
            "entry": PROFILE_META["hiwonder"]["entry"],
            "presetId": "",
            "claimed": False,
            "detection": "hiwonder-filename",
        }

    if hit is None:
        for preset in GENERIC_PRESETS:
            by_directory: dict[Path, set[str]] = {}
            for path in python_files:
                by_directory.setdefault(path.parent, set()).add(path.name.casefold())
            matching_directory = next(
                (
                    directory
                    for directory, names in by_directory.items()
                    if all(name.casefold() in names for name in preset["files"])
                ),
                None,
            )
            if matching_directory is not None:
                hit = {
                    "workspacePath": str(matching_directory),
                    "profile": "generic",
                    "profileLabel": preset["label"],
                    "entry": preset["entry"],
                    "presetId": preset["id"],
                    "claimed": False,
                    "detection": "generic-preset-files",
                }
                break

    if hit is None:
        for path in python_files[:16]:
            try:
                with path.open("rb") as source:
                    content = source.read(MAX_SOURCE_SCAN_BYTES).decode("utf-8", errors="replace")
            except OSError:
                continue
            if "import Hiwonder" in content:
                hit = {
                    "workspacePath": str(root),
                    "profile": "hiwonder",
                    "profileLabel": PROFILE_META["hiwonder"]["label"],
                    "entry": PROFILE_META["hiwonder"]["entry"],
                    "presetId": "",
                    "claimed": False,
                    "detection": "hiwonder-import",
                }
                break
            for preset in GENERIC_PRESETS:
                if any(signature in content for signature in preset["content"]):
                    hit = {
                        "workspacePath": str(path.parent),
                        "profile": "generic",
                        "profileLabel": preset["label"],
                        "entry": preset["entry"],
                        "presetId": preset["id"],
                        "claimed": False,
                        "detection": "generic-preset-content",
                    }
                    break
            if hit is not None:
                break

    if hit is None:
        return {
            "detected": False,
            "workspacePath": str(root),
            "profile": "",
            "profileLabel": "",
            "entry": "",
            "presetId": "",
            "claimed": False,
            "detection": "none",
            "files": _root_python_names(root),
        }

    workspace_root = Path(hit["workspacePath"])
    hit["detected"] = True
    hit["files"] = _root_python_names(workspace_root)
    return hit


def _root_python_names(root: Path) -> list[str]:
    try:
        names: list[str] = []
        with os.scandir(root) as iterator:
            for index, entry in enumerate(iterator):
                if index >= MAX_DIRECTORY_ENTRIES:
                    break
                try:
                    if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".py"):
                        names.append(entry.name)
                except OSError:
                    continue
        return sorted(names)
    except OSError:
        return []


def list_workspaces(value: str) -> dict[str, Any]:
    root = absolute_directory(value)
    candidates = [root]
    truncated = False
    skipped_candidates = 0
    try:
        with os.scandir(root) as iterator:
            children = []
            for index, child in enumerate(iterator):
                if index >= MAX_DIRECTORY_ENTRIES:
                    truncated = True
                    break
                children.append(child)
            children.sort(key=lambda item: item.name.casefold())
    except OSError as exc:
        raise WorkspaceError(f"无法列出工作区目录: {exc}") from exc
    for child in children:
        try:
            if child.is_symlink() or child.name.casefold() in SKIP_DIRS:
                continue
            is_directory = child.is_dir(follow_symlinks=False)
        except OSError:
            skipped_candidates += 1
            continue
        if is_directory:
            if len(candidates) >= MAX_CANDIDATES:
                truncated = True
                break
            candidates.append(Path(child.path))

    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            info = detect_workspace(candidate)
            resolved = str(Path(info["workspacePath"]).resolve())
        except (WorkspaceError, OSError):
            skipped_candidates += 1
            continue
        if not info["detected"]:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append(info)
    return {
        "root": str(root),
        "workspaces": found,
        "searchedCandidates": len(candidates),
        "skippedCandidates": skipped_candidates,
        "truncated": truncated,
        "source": "local_workspace",
        "simulated": False,
    }


def validate_board_path(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise WorkspaceError("板载路径必须以 / 开头")
    if "\\" in value or '"' in value or "'" in value or "\x00" in value:
        raise WorkspaceError("板载路径含有不支持的字符")
    if any(ord(character) < 32 for character in value):
        raise WorkspaceError("板载路径不能包含控制字符")
    parts = value.split("/")
    if len(parts) < 2 or any(part in {"", "..", "."} for part in parts[1:]):
        raise WorkspaceError("板载路径需要文件名，且不能包含空段、. 或 .. 路径段")
    if len(value) > 240:
        raise WorkspaceError("板载路径过长")
    return value
