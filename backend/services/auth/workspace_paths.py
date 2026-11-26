"""Resolve explicit tool targets without cross-platform separator ambiguity."""

from __future__ import annotations

import os
import stat
from collections import deque
from pathlib import Path, PureWindowsPath


def _local_path(raw: str, *, link: bool = False) -> Path:
    if "\x00" in raw or "://" in raw:
        raise ValueError("Tool target is not a local path")
    # Windows readlink returns this prefix for ordinary local junctions.
    if link and os.name == "nt" and raw.startswith("\\\\?\\"):
        local = PureWindowsPath(raw[4:])
        if len(local.drive) == 2 and local.drive[1] == ":" and local.root:
            raw = raw[4:]
    windows = PureWindowsPath(raw)
    if windows.drive.startswith("\\\\") or raw.startswith("//"):
        raise ValueError("Remote and device targets are not workspace paths")
    if os.name != "nt":
        if windows.drive or "\\" in raw:
            raise ValueError("Windows path syntax cannot be verified on this host")
    else:
        if bool(windows.drive) != bool(windows.root):
            raise ValueError("Drive-relative and rooted targets require an explicit local drive")
        components = windows.parts[1:] if windows.anchor else windows.parts
        if any(
            PureWindowsPath(part).is_reserved() or ":" in part or part not in {".", ".."} and part.endswith((" ", "."))
            for part in components
        ):
            raise ValueError("Tool target uses an unsupported Windows alias")
    return Path(raw)


def _relative_target(root: Path, supplied_root: Path, candidate: Path) -> tuple[str, ...]:
    if not candidate.is_absolute():
        candidate = root / candidate
    for base in (root, supplied_root):
        if candidate.is_relative_to(base):
            relative = candidate.relative_to(base)
            if not Path(os.path.normpath(base / relative)).is_relative_to(base):
                break
            return relative.parts
    raise ValueError("Tool target is outside the workspace")


def workspace_glob_root(workspace: str, raw: str) -> Path:
    """Validate glob syntax and resolve its fixed, contained search root."""
    parts = _local_path(raw).parts
    wildcard = next(
        (index for index, part in enumerate(parts) if any(char in part for char in "*?[")),
        None,
    )
    if wildcard is None:
        return workspace_target(workspace, raw)
    if any(part == ".." for part in parts[wildcard:]) or any(char in raw for char in "{}"):
        raise ValueError("Glob suffix cannot traverse upward or expand alternative paths")
    # Wildcards are not literal Windows filenames. Resolve only the fixed root;
    # subsequent explicit file reads still require their own containment check.
    return workspace_target(workspace, str(Path(*parts[:wildcard])))


def workspace_target(workspace: str, raw: str) -> Path:
    candidate = _local_path(raw)
    supplied_root = Path(os.path.abspath(workspace))
    root = Path(workspace).resolve(strict=True)
    parts = deque(_relative_target(root, supplied_root, candidate))
    current = root
    links = 0
    while parts:
        part = parts.popleft()
        if part == "..":
            if current == root:
                raise ValueError("Tool target is outside the workspace")
            current = current.parent
            continue
        child = current / part
        try:
            info = child.lstat()
        except FileNotFoundError:
            current = child
            continue
        tag = getattr(info, "st_reparse_tag", 0)
        if tag and tag not in {
            getattr(stat, "IO_REPARSE_TAG_SYMLINK", None),
            getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None),
        }:
            raise ValueError("Tool target uses an unsupported reparse point")
        if stat.S_ISLNK(info.st_mode) or tag:
            links += 1
            if links > 40:
                raise ValueError("Tool target has too many symbolic links")
            destination = _local_path(os.readlink(child), link=True)
            if not destination.is_absolute():
                destination = current / destination
            # Inspect the link itself, never follow an unchecked target or suffix.
            parts.extendleft(reversed(_relative_target(root, supplied_root, destination)))
            current = root
        else:
            current = child
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Tool target is outside the workspace")
    return resolved
