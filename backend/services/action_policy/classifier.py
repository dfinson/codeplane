"""Application-owned action and decision contracts, including legacy wire values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

log = structlog.get_logger()


class RecommendedAction(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    ESCALATE = "escalate"
    DENY = "deny"
    TRANSFORM = "transform"


SECURITY_CRITICAL_REASON_CODES = frozenset(
    {
        "destructive_action",
        "raw_block_device_write",
        "filesystem_format",
        "persistence_mechanism_write",
        "fork_bomb_pattern",
        "credential_exposure",
        "piped_network_exec",
        "protected_path",
        "risk_score_critical",
    }
)


def is_security_critical(reason_code: str | None) -> bool:
    """Preserve non-waivable legacy reasons at both trust and monitor boundaries."""
    if not reason_code:
        return False
    return reason_code in SECURITY_CRITICAL_REASON_CODES or reason_code.startswith("internal_error")


class Preset(StrEnum):
    autonomous = "autonomous"
    supervised = "supervised"
    locked = "locked"


class ActionKind(StrEnum):
    file = "file"
    sdk_tool = "sdk_tool"
    mcp_tool = "mcp_tool"
    shell = "shell"


@dataclass(frozen=True, slots=True)
class Action:
    """Describes a single agent action to be classified."""

    kind: ActionKind
    # File operations
    path: str | None = None
    is_binary: bool = False
    outside_worktree: bool = False
    # Shell
    command: str | None = None
    # SDK tool
    tool_name: str | None = None
    # MCP tool
    mcp_server: str | None = None
    mcp_tool: str | None = None
    mcp_read_only: bool = False
    # Context
    job_id: str | None = None
    workspace_path: str | None = None


@dataclass(frozen=True, slots=True)
class Classification:
    """Normalized decision, retaining the legacy bridge's measured risk fields."""

    recommended_action: RecommendedAction
    reason_code: str
    risk_score: int
    risk_band: str
    effect: str | None
    mechanism: str
    reason: str


@dataclass
class RepoPolicy:
    """In-memory policy loaded from DB at job start.

    Wholesale adoption slimmed this to the two settings CodePlane still owns: the
    ``preset`` (which selects a TraceForge governance profile) and the approval
    ``batch_window_seconds``. The retired path/action/cost rule tables and the
    MCP reversibility hints are gone — governance classifies natively.
    """

    preset: Preset = Preset.supervised
    batch_window_seconds: float = 5.0
    mcp_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
