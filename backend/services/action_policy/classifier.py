"""Application-owned action and decision contracts, including legacy wire values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Mapping

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


# Product-owned soft warning/escalation USD defaults; None disables that line.
USD_CEILING_DEFAULTS: Mapping[Preset, tuple[float | None, float | None]] = MappingProxyType(
    {
        Preset.autonomous: (None, None),
        Preset.supervised: (20.0, 40.0),
        Preset.locked: (5.0, 10.0),
    }
)

WAIVABLE_REASON_CODES = frozenset(
    {
        "budget_pressure",
        "external_mutation",
        "git_mutation_supervised",
        "git_mutation_locked",
        "readonly_shell_locked",
        "cp_usd_ceiling",
        "cp_usd_ceiling_warn",
    }
)


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
    arguments_json: str = ""
    tool_definition: str = ""


@dataclass(frozen=True, slots=True)
class Classification:
    """Normalized decision, retaining the legacy bridge's measured risk fields."""

    recommended_action: RecommendedAction
    reason_code: str
    risk_score: int | None
    risk_band: str
    effect: str | None
    mechanism: str
    reason: str


@dataclass
class RepoPolicy:
    """Application-owned settings captured by each permission invocation."""

    preset: Preset = Preset.supervised
    batch_window_seconds: float = 5.0
    mcp_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    usd_ceilings: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
