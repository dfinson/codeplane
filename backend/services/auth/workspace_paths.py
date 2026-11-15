"""Resolve explicit tool targets without cross-platform separator ambiguity."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


def workspace_target(workspace: str, raw: str) -> Path:
    if "\x00" in raw or "://" in raw:
        raise ValueError("Tool target is not a local path")
    if os.name != "nt" and PureWindowsPath(raw).drive:
        raise ValueError("Windows drive targets cannot be verified on this host")
    root = Path(workspace).resolve(strict=True)
    candidate = Path(raw.replace("\\", "/"))
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Tool target is outside the workspace")
    return resolved
