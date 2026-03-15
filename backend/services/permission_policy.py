"""Permission policy evaluation for SDK permission requests.

Evaluates whether a given SDK permission request should be auto-approved,
forwarded to the operator, or denied based on the active PermissionMode
and workspace context.
"""

from __future__ import annotations

import os
from enum import StrEnum

import structlog

log = structlog.get_logger()


class PolicyDecision(StrEnum):
    """Result of evaluating a permission request against the active policy."""

    approve = "approve"
    ask = "ask"


def _is_path_within_workspace(path: str, workspace: str) -> bool:
    """Check whether *path* is inside (or equal to) *workspace*."""
    try:
        rp = os.path.realpath(path)
        rw = os.path.realpath(workspace)
        return rp == rw or rp.startswith(rw + os.sep)
    except (TypeError, ValueError):
        return False


def _hits_protected_path(path: str, workspace: str, protected: list[str]) -> bool:
    """Return True if *path* matches any entry in the protected-paths list."""
    if not protected:
        return False
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(workspace))
    except (TypeError, ValueError):
        return False
    for pp in protected:
        pp_stripped = pp.rstrip("/")
        if rel == pp_stripped or rel.startswith(pp_stripped + "/"):
            return True
    return False


def evaluate(
    *,
    kind: str,
    workspace_path: str,
    protected_paths: list[str],
    possible_paths: list[str] | None = None,
    file_name: str | None = None,
    path: str | None = None,
    read_only: bool | None = None,
) -> PolicyDecision:
    """Evaluate a permission request under ``auto`` mode.

    ``permissive`` and ``supervised`` modes are short-circuited by the
    caller before reaching this function.

    Parameters
    ----------
    kind:
        The SDK ``PermissionRequestKind`` value (``shell``, ``read``,
        ``write``, ``url``, ``mcp``, ``custom-tool``, ``memory``).
    workspace_path:
        Absolute path to the job's worktree / workspace directory.
    protected_paths:
        Relative path prefixes from ``.tower.yml`` that always require
        operator approval.
    possible_paths:
        Paths the SDK reports might be read or written.
    file_name:
        File being written to (from ``PermissionRequest.file_name``).
    path:
        File or directory being read (from ``PermissionRequest.path``).
    read_only:
        Whether an MCP tool is declared read-only.
    """

    # --- Memory operations are always safe ---
    if kind == "memory":
        return PolicyDecision.approve

    # --- Reads within the workspace are safe ---
    if kind == "read":
        target = path or file_name
        if target and _is_path_within_workspace(target, workspace_path):
            return PolicyDecision.approve
        # If no path info, approve reads (conservative toward usability)
        if target is None:
            return PolicyDecision.approve
        # Read outside workspace → ask
        return PolicyDecision.ask

    # --- Writes within workspace (unless protected) are safe ---
    if kind == "write":
        target = file_name or path
        if target:
            if not _is_path_within_workspace(target, workspace_path):
                return PolicyDecision.ask
            if _hits_protected_path(target, workspace_path, protected_paths):
                return PolicyDecision.ask
            return PolicyDecision.approve
        # No path info for a write → ask to be safe
        return PolicyDecision.ask

    # --- Shell commands always require approval in auto mode ---
    if kind == "shell":
        return PolicyDecision.ask

    # --- URL fetches always require approval ---
    if kind == "url":
        return PolicyDecision.ask

    # --- MCP tools: approve if read-only, otherwise ask ---
    if kind == "mcp":
        if read_only:
            return PolicyDecision.approve
        return PolicyDecision.ask

    # --- Custom tools: ask ---
    if kind == "custom-tool":
        return PolicyDecision.ask

    # --- Unknown kind → ask ---
    log.warning("unknown_permission_kind", kind=kind)
    return PolicyDecision.ask
