"""Local workspace claim and policy writes; never touches a physical board."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from policy import MAX_POLICY_BYTES, POLICIES, read_workbench_policy
from workspaces import (
    MAX_BOARD_JSON_BYTES, PROFILE_META, WorkspaceError, absolute_directory,
    detect_workspace, normalize_workspace_path, validate_board_path, _read_board_config,
)


def _root(path: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise WorkspaceError("workspace path is required")
    candidate = Path(normalize_workspace_path(path)).expanduser()
    if candidate.is_symlink():
        raise WorkspaceError("workspace root may not be a symlink")
    return absolute_directory(path)


def selectable_workspace(path: str) -> dict[str, Any]:
    """Validate an explicit selection without silently ignoring malformed board.json."""
    root = _root(path)
    for candidate in (root,):
        directory = candidate / "esp32-ide"
        board = directory / "board.json"
        if directory.is_symlink() or board.is_symlink():
            raise WorkspaceError("workspace board.json path may not be a symlink")
        if board.exists() and _read_board_config(candidate) is None:
            raise WorkspaceError("existing board.json is invalid; selection refused")
    info = detect_workspace(root)
    if not info.get("detected"):
        raise WorkspaceError("target workspace is not recognized; claim it first")
    selected_root = Path(info["workspacePath"])
    if selected_root != root:
        board = selected_root / "esp32-ide" / "board.json"
        if board.is_symlink() or (board.exists() and _read_board_config(selected_root) is None):
            raise WorkspaceError("selected workspace board.json is invalid")
    entry = validate_board_path(info["entry"])
    if not entry.lower().endswith(".py"):
        raise WorkspaceError("workspace entry is not a Python file")
    if info["profile"] not in PROFILE_META:
        raise WorkspaceError("workspace profile is not supported")
    return info


def _config_directory(root: Path) -> Path:
    directory = root / "esp32-ide"
    if directory.is_symlink():
        raise WorkspaceError("workspace configuration directory may not be a symlink")
    directory.mkdir(exist_ok=True)
    if not directory.is_dir() or directory.is_symlink():
        raise WorkspaceError("workspace configuration directory is not safe")
    return directory


def _atomic_create(path: Path, payload: bytes) -> None:
    """Create without replacing a pre-existing file; never leave a partial target."""
    descriptor, temporary = tempfile.mkstemp(prefix=".codex-new-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Same-directory hard link installs the complete content atomically and
        # refuses a target created by another process. Unsupported FS => deny.
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def claim_workspace(path: str, profile: str, label: str | None, entry: str | None) -> dict[str, Any]:
    root = _root(path)
    if profile not in PROFILE_META:
        raise WorkspaceError("profile must be hiwonder or generic")
    chosen_label = PROFILE_META[profile]["label"] if label is None else label
    if (not isinstance(chosen_label, str) or not chosen_label.strip()
            or len(chosen_label) > 100 or any(ord(ch) < 32 for ch in chosen_label)):
        raise WorkspaceError("invalid workspace label")
    chosen_entry = PROFILE_META[profile]["entry"] if entry is None else entry
    validate_board_path(chosen_entry)
    if not chosen_entry.lower().endswith(".py"):
        raise WorkspaceError("workspace entry must be a Python file")
    directory = _config_directory(root)
    target = directory / "board.json"
    if target.exists() or target.is_symlink():
        raise WorkspaceError("existing board.json must not be overwritten")
    payload = (json.dumps(
        {"type": profile, "label": chosen_label, "entry": chosen_entry},
        ensure_ascii=False, indent=2,
    ) + "\n").encode("utf-8")
    if len(payload) > MAX_BOARD_JSON_BYTES:
        raise WorkspaceError("board.json is too large")
    try:
        _atomic_create(target, payload)
    except (OSError, ValueError) as exc:
        raise WorkspaceError(f"cannot atomically claim workspace: {exc}") from exc
    return detect_workspace(root)


def policy_snapshot(path: str) -> dict[str, Any]:
    root = _root(path)
    state = read_workbench_policy(str(root))
    target = root / "esp32-ide" / "workbench-policy.json"
    revision = "missing"
    if state["source"] == "workbench-policy.json":
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise WorkspaceError(f"cannot read policy revision: {exc}") from exc
        revision = hashlib.sha256(payload).hexdigest()
    elif state.get("reason") != "missing":
        raise WorkspaceError("existing policy is invalid; refusing to overwrite")
    return {**state, "revision": revision, "workspacePath": str(root)}


def set_workspace_policy(path: str, policy: str, expected_revision: str) -> dict[str, Any]:
    if policy not in POLICIES:
        raise WorkspaceError("unknown policy")
    current = policy_snapshot(path)
    if (not isinstance(expected_revision, str)
            or current["revision"] != expected_revision):
        raise WorkspaceError("policy changed since it was read; operation rejected")
    if current["policy"] == policy and current["source"] == "workbench-policy.json":
        return current
    root = Path(current["workspacePath"])
    directory = _config_directory(root)
    target = directory / "workbench-policy.json"
    if target.is_symlink():
        raise WorkspaceError("policy may not be a symlink")
    data: dict[str, Any] = {}
    if current["revision"] != "missing":
        try:
            raw = target.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected_revision:
                raise WorkspaceError("policy changed during update")
            data = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"cannot preserve existing policy: {exc}") from exc
        if not isinstance(data, dict):
            raise WorkspaceError("invalid policy object")
    data["policy"] = policy
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_POLICY_BYTES:
        raise WorkspaceError("new policy exceeds configured size limit")
    if current["revision"] == "missing":
        try:
            _atomic_create(target, payload)
        except OSError as exc:
            raise WorkspaceError(f"policy cannot be atomically created: {exc}") from exc
    else:
        descriptor, temp = tempfile.mkstemp(prefix=".codex-policy-", dir=str(directory))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != expected_revision:
                raise WorkspaceError("policy changed during update")
            os.replace(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
    return policy_snapshot(str(root))
