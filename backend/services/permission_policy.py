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

from backend.models.domain import PermissionMode

log = structlog.get_logger()


class PolicyDecision(StrEnum):
    """Result of evaluating a permission request against the active policy."""

    approve = "approve"
    ask = "ask"
    deny = "deny"


# ---------------------------------------------------------------------------
# Hard-gated shell commands — ALWAYS require operator approval regardless of
# permission mode.  These are irreversible or bypass CodePlane controls
# (e.g. merging outside the managed merge flow).
# ---------------------------------------------------------------------------
_HARD_GATED_SHELL_RE = re.compile(
    r"(?i)"
    # git merge / pull / rebase / cherry-pick — bypass CodePlane merge controls
    r"(?:^\s*git\s+(?:merge|pull|rebase|cherry-pick)\b)"
    # git reset --hard — destructive history rewrite
    r"|(?:^\s*git\s+reset\s+.*--hard\b)"
    r"|(?:^\s*git\s+reset\s+--hard\b)",
)


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

_APPROVE = PolicyDecision.approve
_DENY = PolicyDecision.deny
_ASK = PolicyDecision.ask

# Sentinel callables for special-case evaluation
_SHELL_RO = "shell_readonly"  # approve if readonly shell, else <fallback>
_PATH_WS = "path_in_ws"  # approve if target is inside workspace
_MCP_RO = "mcp_readonly"  # approve if mcp tool is read-only
_READ_WS = "read_in_ws"  # approve if read target is in workspace (deny otherwise)


class _Rule(NamedTuple):
    decision: PolicyDecision | str
    fallback: PolicyDecision = _APPROVE  # used by compound rules


# (mode, kind) → rule.  Missing entries fall through to the mode default.
_RULES: dict[tuple[str, str], _Rule] = {
    # ── AUTO ──────────────────────────────────────────────────────────
    (PermissionMode.auto, "read"): _Rule(_APPROVE),
    (PermissionMode.auto, "memory"): _Rule(_APPROVE),
    (PermissionMode.auto, "write"): _Rule(_PATH_WS, _APPROVE),  # approve; workspace path check first
    (PermissionMode.auto, "shell"): _Rule(_APPROVE),
    (PermissionMode.auto, "mcp"): _Rule(_APPROVE),
    (PermissionMode.auto, "url"): _Rule(_APPROVE),
    (PermissionMode.auto, "custom-tool"): _Rule(_APPROVE),
    # ── READ_ONLY ────────────────────────────────────────────────────
    (PermissionMode.read_only, "memory"): _Rule(_APPROVE),
    (PermissionMode.read_only, "read"): _Rule(_READ_WS),
    (PermissionMode.read_only, "shell"): _Rule(_SHELL_RO, _DENY),
    (PermissionMode.read_only, "mcp"): _Rule(_MCP_RO, _DENY),
    (PermissionMode.read_only, "write"): _Rule(_DENY),
    (PermissionMode.read_only, "url"): _Rule(_DENY),
    (PermissionMode.read_only, "custom-tool"): _Rule(_DENY),
    # ── APPROVAL_REQUIRED ────────────────────────────────────────────
    (PermissionMode.approval_required, "memory"): _Rule(_APPROVE),
    (PermissionMode.approval_required, "read"): _Rule(_APPROVE),
    (PermissionMode.approval_required, "shell"): _Rule(_SHELL_RO, _ASK),
    (PermissionMode.approval_required, "write"): _Rule(_ASK),
    (PermissionMode.approval_required, "url"): _Rule(_ASK),
    (PermissionMode.approval_required, "mcp"): _Rule(_MCP_RO, _ASK),
    (PermissionMode.approval_required, "custom-tool"): _Rule(_ASK),
}

# Default decisions when a (mode, kind) pair is not in the table.
_MODE_DEFAULTS: dict[str, PolicyDecision] = {
    PermissionMode.auto: _APPROVE,
    PermissionMode.read_only: _DENY,
    PermissionMode.approval_required: _ASK,
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
            return _APPROVE
        if possible_paths and all(_is_path_within_workspace(p, workspace_path) for p in possible_paths):
            return _APPROVE
        return rule.fallback

    if decision == _SHELL_RO:
        cmd = full_command_text or ""
        return _APPROVE if _READONLY_SHELL_RE.match(cmd) else rule.fallback

    if decision == _MCP_RO:
        return _APPROVE if read_only else rule.fallback

    if decision == _READ_WS:
        target = file_name or path
        if target is None or _is_path_within_workspace(target, workspace_path):
            return _APPROVE
        return _DENY

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
from functools import partial as _partial  # noqa: E402

evaluate_auto = _partial(evaluate, "auto")
evaluate_read_only = _partial(evaluate, "read_only")
evaluate_approval_required = _partial(evaluate, "approval_required")


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
    # Hard-gated commands always require approval, regardless of mode.
    if kind == "shell" and full_command_text and _HARD_GATED_SHELL_RE.search(full_command_text):
        log.info("hard_gated_command", command=full_command_text, mode=mode)
        return _ASK

    rule = _RULES.get((mode, kind))
    if rule is None:
        default = _MODE_DEFAULTS.get(mode, _ASK)
        if default == _ASK:
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
