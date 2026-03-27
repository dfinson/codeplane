"""Domain dataclasses and value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    waiting_for_approval = "waiting_for_approval"
    review = "review"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


# Terminal states have no further transitions
TERMINAL_STATES: frozenset[JobState] = frozenset(
    {
        JobState.completed,
        JobState.failed,
        JobState.canceled,
    }
)

# Active states (job is occupying a worktree)
ACTIVE_STATES: frozenset[JobState] = frozenset(
    {
        JobState.queued,
        JobState.running,
        JobState.waiting_for_approval,
        JobState.review,
    }
)


class Resolution(StrEnum):
    """User-facing disposition of a completed job.

    Distinct from ``Job.merge_status`` which tracks only the *git merge
    operation* outcome.  ``resolution`` captures the *user's decision* about
    what to do with the agent's work after it finishes.
    """

    unresolved = "unresolved"
    merged = "merged"
    pr_created = "pr_created"
    discarded = "discarded"
    conflict = "conflict"


# Valid state transitions: (from_state) -> set of valid to_states
_VALID_TRANSITIONS: dict[str | None, set[str]] = {
    None: {JobState.running, JobState.queued},
    JobState.queued: {JobState.running, JobState.canceled},
    JobState.running: {
        JobState.waiting_for_approval,
        JobState.review,
        JobState.failed,
        JobState.canceled,
    },
    JobState.waiting_for_approval: {
        JobState.running,
        JobState.failed,
        JobState.canceled,
    },
    # Review: agent exited cleanly, awaiting operator decision
    JobState.review: {
        JobState.running,  # operator reruns / sends follow-up
        JobState.completed,  # operator resolves (merge, PR, discard)
        JobState.canceled,
    },
    # Terminal states can transition back to running for job resumption
    JobState.completed: {JobState.running},
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

    auto              — Everything auto-approved within worktree. No prompts.
    read_only         — Allow reads + grep/find. Block all writes/mutations.
    approval_required — Always allow read_file. Require approval for
                        shell commands (except grep/find), URL fetches,
                        and any write operations.
    """

    auto = "auto"
    read_only = "read_only"
    approval_required = "approval_required"


class SessionEventKind(StrEnum):
    log = "log"
    transcript = "transcript"
    file_changed = "file_changed"
    approval_request = "approval_request"
    model_downgraded = "model_downgraded"
    done = "done"
    error = "error"


@dataclass
class SessionEvent:
    kind: SessionEventKind
    payload: dict[str, Any]


@dataclass
class SessionConfig:
    workspace_path: str
    prompt: str
    job_id: str = ""
    sdk: str = "copilot"
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
    """Domain representation of a coding job.

    ``merge_status`` vs ``resolution`` — these track two distinct lifecycle phases:

    * **merge_status** — The outcome of the *git merge operation* performed
      automatically when the agent session completes.  Values are purely
      mechanical: ``not_merged`` | ``merged`` | ``conflict``.  Set once by
      ``MergeService`` and never changed by user action.

    * **resolution** — The *user-facing disposition* of the completed job,
      reflecting what the user (or auto-completion policy) decided to do with
      the agent's work.  Governed by the ``Resolution`` enum:
      ``unresolved`` | ``merged`` | ``pr_created`` | ``discarded`` | ``conflict``.
      Updated when the user explicitly resolves a job via the UI or API.
    """

    id: str
    repo: str
    prompt: str
    state: JobState
    base_ref: str
    branch: str | None
    worktree_path: str | None
    session_id: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    pr_url: str | None = None
    merge_status: str | None = None
    """Git merge operation outcome: ``not_merged`` | ``merged`` | ``conflict``."""
    resolution: Resolution | None = None
    """User-facing job disposition (see :class:`Resolution`)."""
    archived_at: datetime | None = None
    title: str | None = None
    worktree_name: str | None = None
    permission_mode: PermissionMode = PermissionMode.auto
    session_count: int = 1
    sdk_session_id: str | None = None
    model: str | None = None
    sdk: str = "copilot"
    failure_reason: str | None = None
    verify: bool | None = None
    self_review: bool | None = None
    max_turns: int | None = None
    verify_prompt: str | None = None
    self_review_prompt: str | None = None
    version: int = 1
    parent_job_id: str | None = None


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
    # When True this approval was triggered by a hard-blocked operation (e.g.
    # git reset --hard) and MUST NOT be auto-resolved by a blanket trust grant.
    # The operator must explicitly click Approve for each occurrence.
    requires_explicit_approval: bool = False


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
