"""Permission policy evaluation for SDK permission requests.

Evaluates tool-call permission decisions based on the active PermissionMode.

Modes
-----
AUTO             — approve everything within the current worktree.
READ_ONLY        — approve reads and grep/find; deny everything else.
APPROVAL_REQUIRED — approve read_file; require approval for shells
                    (except grep/find), URL fetches, and writes.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from typing import NamedTuple

import structlog

log = structlog.get_logger()


class PolicyDecision(StrEnum):
    """Result of evaluating a permission request against the active policy."""

    approve = "approve"
    ask = "ask"
    deny = "deny"


# Read-only shell commands that are always safe.
# Covers Unix (grep, ls, cat …), Windows cmd (dir, findstr, where …),
# and PowerShell cmdlets (Get-ChildItem, Select-String …).
_READONLY_SHELL_RE = re.compile(
    r"^\s*("
    # Unix
    r"grep|egrep|fgrep|rg|find|ls|cat|head|tail|wc|sort|diff|file|stat|du|tree"
    r"|echo|pwd|which|type|printenv|env|more|less"
    # Windows cmd builtins
    r"|dir|findstr|where|fc|more"
    # PowerShell cmdlets & common aliases
    r"|Get-ChildItem|Get-Content|Get-Item|Get-ItemProperty|Get-Location"
    r"|Select-String|Measure-Object|Compare-Object|Test-Path|Resolve-Path"
    r"|Write-Output|Out-Host|Format-List|Format-Table"
    r"|gci|gc|gi|sls|measure|compare"
    r")\b",
    re.IGNORECASE,
)


def _is_path_within_workspace(path: str, workspace: str) -> bool:
    """Check whether *path* is inside (or equal to) *workspace*."""
    try:
        rp = os.path.realpath(path)
        rw = os.path.realpath(workspace)
        return rp == rw or rp.startswith(rw + os.sep)
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

_A = PolicyDecision.approve
_D = PolicyDecision.deny
_K = PolicyDecision.ask  # "asK" — avoids shadowing `ask` builtin

# Sentinel callables for special-case evaluation
_SHELL_RO = "shell_readonly"  # approve if readonly shell, else <fallback>
_PATH_WS = "path_in_ws"  # approve if target is inside workspace
_MCP_RO = "mcp_readonly"  # approve if mcp tool is read-only
_READ_WS = "read_in_ws"  # approve if read target is in workspace (deny otherwise)


class _Rule(NamedTuple):
    decision: PolicyDecision | str
    fallback: PolicyDecision = _A  # used by compound rules


# (mode, kind) → rule.  Missing entries fall through to the mode default.
_RULES: dict[tuple[str, str], _Rule] = {
    # ── AUTO ──────────────────────────────────────────────────────────
    ("auto", "read"):        _Rule(_A),
    ("auto", "memory"):      _Rule(_A),
    ("auto", "write"):       _Rule(_PATH_WS, _A),  # approve; workspace path check first
    ("auto", "shell"):       _Rule(_A),
    ("auto", "mcp"):         _Rule(_A),
    ("auto", "url"):         _Rule(_A),
    ("auto", "custom-tool"): _Rule(_A),
    # ── READ_ONLY ────────────────────────────────────────────────────
    ("read_only", "memory"):      _Rule(_A),
    ("read_only", "read"):        _Rule(_READ_WS),
    ("read_only", "shell"):       _Rule(_SHELL_RO, _D),
    ("read_only", "mcp"):         _Rule(_MCP_RO, _D),
    ("read_only", "write"):       _Rule(_D),
    ("read_only", "url"):         _Rule(_D),
    ("read_only", "custom-tool"): _Rule(_D),
    # ── APPROVAL_REQUIRED ────────────────────────────────────────────
    ("approval_required", "memory"):      _Rule(_A),
    ("approval_required", "read"):        _Rule(_A),
    ("approval_required", "shell"):       _Rule(_SHELL_RO, _K),
    ("approval_required", "write"):       _Rule(_K),
    ("approval_required", "url"):         _Rule(_K),
    ("approval_required", "mcp"):         _Rule(_MCP_RO, _K),
    ("approval_required", "custom-tool"): _Rule(_K),
}

# Default decisions when a (mode, kind) pair is not in the table.
_MODE_DEFAULTS: dict[str, PolicyDecision] = {
    "auto": _A,
    "read_only": _D,
    "approval_required": _K,
}


def _resolve(
    rule: _Rule,
    *,
    workspace_path: str,
    file_name: str | None,
    path: str | None,
    possible_paths: list[str] | None,
    full_command_text: str | None,
    read_only: bool | None,
) -> PolicyDecision:
    """Resolve a rule entry into a concrete PolicyDecision."""
    decision = rule.decision

    if isinstance(decision, PolicyDecision):
        return decision

    if decision == _PATH_WS:
        target = file_name or path
        if target and _is_path_within_workspace(target, workspace_path):
            return _A
        if possible_paths and all(_is_path_within_workspace(p, workspace_path) for p in possible_paths):
            return _A
        return rule.fallback

    if decision == _SHELL_RO:
        cmd = full_command_text or ""
        return _A if _READONLY_SHELL_RE.match(cmd) else rule.fallback

    if decision == _MCP_RO:
        return _A if read_only else rule.fallback

    if decision == _READ_WS:
        target = file_name or path
        if target is None or _is_path_within_workspace(target, workspace_path):
            return _A
        return _D

    return rule.fallback  # pragma: no cover


def evaluate(
    mode: str,
    *,
    kind: str,
    workspace_path: str,
    possible_paths: list[str] | None = None,
    full_command_text: str | None = None,
    file_name: str | None = None,
    path: str | None = None,
    read_only: bool | None = None,
) -> PolicyDecision:
    """Evaluate a permission request against the given mode.

    This is the single public entry-point — callers pass the mode string
    directly instead of picking a mode-specific wrapper function.
    """
    return _evaluate(
        mode,
        kind=kind,
        workspace_path=workspace_path,
        possible_paths=possible_paths,
        full_command_text=full_command_text,
        file_name=file_name,
        path=path,
        read_only=read_only,
    )


# Keep legacy wrappers for backward compatibility
evaluate_auto = evaluate
evaluate_read_only = evaluate
evaluate_approval_required = evaluate


def _evaluate(
    mode: str,
    *,
    kind: str,
    workspace_path: str,
    possible_paths: list[str] | None = None,
    file_name: str | None = None,
    path: str | None = None,
    full_command_text: str | None = None,
    read_only: bool | None = None,
) -> PolicyDecision:
    """Core dispatcher: look up (mode, kind) in the rule table and resolve."""
    rule = _RULES.get((mode, kind))
    if rule is None:
        default = _MODE_DEFAULTS.get(mode, _K)
        if default == _K:
            log.warning("unknown_permission_kind", kind=kind)
        return default

    return _resolve(
        rule,
        workspace_path=workspace_path,
        file_name=file_name,
        path=path,
        possible_paths=possible_paths,
        full_command_text=full_command_text,
        read_only=read_only,
    )
