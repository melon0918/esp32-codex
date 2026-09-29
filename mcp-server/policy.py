"""Read the workspace safety policy and classify control effects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


POLICIES = {"auto", "confirm-write", "confirm-all"}
DEFAULT_POLICY = "confirm-write"
MAX_POLICY_BYTES = 4096


def read_workbench_policy(workspace: str | None) -> dict[str, Any]:
    """Read esp32-ide/workbench-policy.json without following symlinks.

    Missing, invalid, oversized, unreadable, or symlinked policy files use the
    conservative confirm-write default.
    """
    if not workspace:
        return {"policy": DEFAULT_POLICY, "source": "default", "reason": "no_workspace"}

    directory = Path(workspace) / "esp32-ide"
    path = directory / "workbench-policy.json"
    try:
        if directory.is_symlink() or path.is_symlink():
            return {"policy": DEFAULT_POLICY, "source": "default", "reason": "symlink"}
        if path.stat().st_size > MAX_POLICY_BYTES:
            return {"policy": DEFAULT_POLICY, "source": "default", "reason": "too_large"}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {"policy": DEFAULT_POLICY, "source": "default", "reason": "missing"}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"policy": DEFAULT_POLICY, "source": "default", "reason": "unreadable_or_invalid"}

    selected = value.get("policy") if isinstance(value, dict) else None
    if not isinstance(selected, str) or selected not in POLICIES:
        return {"policy": DEFAULT_POLICY, "source": "default", "reason": "invalid_policy"}
    return {"policy": selected, "source": "workbench-policy.json", "reason": "configured"}


def confirmation_required(policy: str, effect: str) -> bool:
    if effect not in {"safe", "control", "write"}:
        raise ValueError(f"unknown action effect: {effect}")
    if policy == "auto" or effect == "safe":
        return False
    if policy == "confirm-write":
        return effect == "write"
    if policy == "confirm-all":
        return True
    return True
