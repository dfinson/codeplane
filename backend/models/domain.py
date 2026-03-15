"""Domain dataclasses and value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    waiting_for_approval = "waiting_for_approval"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


# Terminal states have no further transitions
TERMINAL_STATES: frozenset[str] = frozenset(
    {
        JobState.succeeded,
        JobState.failed,
        JobState.canceled,
    }
)

# Active states (job is occupying a worktree)
ACTIVE_STATES: frozenset[str] = frozenset(
    {
        JobState.queued,
        JobState.running,
        JobState.waiting_for_approval,
    }
)

# Valid state transitions: (from_state) -> set of valid to_states
_VALID_TRANSITIONS: dict[str | None, set[str]] = {
    None: {JobState.running, JobState.queued},
    JobState.queued: {JobState.running, JobState.canceled},
    JobState.running: {
        JobState.waiting_for_approval,
        JobState.succeeded,
        JobState.failed,
        JobState.canceled,
    },
    JobState.waiting_for_approval: {
        JobState.running,
        JobState.failed,
        JobState.canceled,
    },
    # Terminal states can transition back to running for job resumption
    JobState.succeeded: {JobState.running},
    JobState.failed: {JobState.running},
    JobState.canceled: {JobState.running},
}


class InvalidStateTransitionError(Exception):
    """Raised when a job state transition is not allowed."""

    def __init__(self, from_state: str | None, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Invalid state transition: {from_state!r} -> {to_state!r}")


def validate_state_transition(from_state: str | None, to_state: str) -> None:
    """Validate a job state transition. Raises InvalidStateTransitionError if invalid."""
    valid_targets = _VALID_TRANSITIONS.get(from_state, set())
    if to_state not in valid_targets:
        raise InvalidStateTransitionError(from_state, to_state)


class PermissionMode(StrEnum):
    """Controls how the agent adapter handles SDK permission requests.

    permissive  — auto-approve everything, no operator prompts.
    auto        — auto-approve reads/writes within workspace; ask for
                  shell commands, URL fetches, writes outside workspace,
                  and writes to protected paths.
    supervised  — ask the operator for every permission request.
    readonly    — deny any mutation (write/shell/url), approve reads.
    """

    permissive = "permissive"
    auto = "auto"
    supervised = "supervised"
    readonly = "readonly"


class SessionEventKind(StrEnum):
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
    job_id: str = ""
    model: str | None = None
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    protected_paths: list[str] = field(default_factory=list)
    permission_mode: PermissionMode = PermissionMode.auto
    # Injected by RuntimeService for supervised mode; callable[[description, proposed_action], Awaitable[str]]
    blocking_permission_handler: object = None
    # Set when resuming a job to reconnect to an existing Copilot SDK session
    resume_sdk_session_id: str | None = None


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
    pr_url: str | None = None
    merge_status: str | None = None  # not_merged | merged | conflict | pr_created
    title: str | None = None
    permission_mode: str = "auto"
    session_count: int = 1
    sdk_session_id: str | None = None
    model: str | None = None


@dataclass
class Approval:
    """Domain representation of an approval request."""

    id: str
    job_id: str
    description: str
    proposed_action: str | None
    requested_at: datetime
    resolved_at: datetime | None = None
    resolution: str | None = None


@dataclass
class Artifact:
    """Domain representation of an artifact record."""

    id: str
    job_id: str
    name: str
    type: str
    mime_type: str
    size_bytes: int
    disk_path: str
    phase: str
    created_at: datetime
