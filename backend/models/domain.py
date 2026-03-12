"""Domain dataclasses and value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class SessionEventKind(str, Enum):
    log = "log"
    transcript = "transcript"
    file_changed = "file_changed"
    approval_request = "approval_request"
    done = "done"
    error = "error"


@dataclass
class SessionEvent:
    kind: SessionEventKind
    payload: dict  # type: ignore[type-arg]


@dataclass
class SessionConfig:
    workspace_path: str
    prompt: str
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    protected_paths: list[str] = field(default_factory=list)


@dataclass
class MCPServerConfig:
    command: str
    args: list[str]
    env: dict[str, str] | None = None


@dataclass
class Job:
    id: str
    repo: str
    prompt: str
    state: str
    strategy: str
    base_ref: str
    branch: str | None
    worktree_path: str | None
    session_id: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
