"""Domain dataclasses and value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from backend.models.api_schemas import ArtifactType, ExecutionPhase


class JobState(StrEnum):
    preparing = "preparing"
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
        JobState.preparing,
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


class ApprovalResolution(StrEnum):
    """Outcome of an operator's approval decision."""

    approved = "approved"
    rejected = "rejected"


class GitMergeOutcome(StrEnum):
    """Outcome of the automatic git merge operation after an agent session.

    Distinct from :class:`Resolution` which captures the *user's decision*.
    This enum tracks only the mechanical result of the merge-back attempt.
    """

    not_merged = "not_merged"
    merged = "merged"
    conflict = "conflict"
    pr_created = "pr_created"


# Job state machine — authoritative transition table (see SPEC.md §12.2).
#
#   None ──► preparing ──► queued ──► running ──► review ──► completed
#                │            │          │  ▲        │  ▲        │
#                ▼            ▼          ▼  │        ▼  │        ▼
#             failed      canceled   waiting_for_approval     running
#               │                        │                      │
#               ▼                        ▼                      ▼
#            running                  canceled               running
#
# Terminal states (completed, failed, canceled) allow transition back
# to running for job resumption.
#
# Enforced by validate_state_transition(); all external callers go
# through JobService.transition_state().
_VALID_TRANSITIONS: dict[JobState | None, set[JobState]] = {
    None: {JobState.preparing, JobState.running, JobState.queued},
    JobState.preparing: {JobState.queued, JobState.failed, JobState.canceled},
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


class CodePlaneError(Exception):
    """Base exception for all CodePlane domain errors."""


class ServiceInitError(CodePlaneError):
    """Raised when a service is used before required dependencies are configured."""


class JobNotFoundError(CodePlaneError):
    """Raised when a job ID does not exist."""


class StateConflictError(CodePlaneError):
    """Raised when a job action conflicts with its current state."""


class RepoNotAllowedError(CodePlaneError):
    """Raised when a repo path is not in the allowlist."""


class ApprovalNotFoundError(CodePlaneError):
    """Raised when an approval request is not found."""


class ApprovalAlreadyResolvedError(CodePlaneError):
    """Raised when attempting to resolve an already-resolved approval."""


class SDKModelMismatchError(CodePlaneError):
    """Raised when a model is incompatible with the selected SDK."""


class ProjectNotFoundError(CodePlaneError):
    """Raised when a Project ID does not exist."""


class RepoAlreadyAssignedError(CodePlaneError):
    """Raised when a repo path already belongs to a different Project (NFR5)."""


class TaskLinkNotFoundError(CodePlaneError):
    """Raised when a TaskLink ID does not exist (Story 5.3)."""


class TaskLinkState(StrEnum):
    """Lifecycle state for a persisted TaskLink."""

    waiting = "waiting"
    ready = "ready"
    starting = "starting"
    running = "running"
    completed = "completed"
    failed = "failed"


class AgentSDK(StrEnum):
    """Supported agent SDK backends."""

    copilot = "copilot"
    claude = "claude"


class JobMode(StrEnum):
    """Execution mode for a job.

    standard          — Normal execution: agent receives task and runs freely.
    plan              — Plan-first: a planning session produces a structured plan,
                        the operator reviews it, then a fresh implementation session
                        executes the approved plan.
    plan_implementing — The planning phase is done and the implementation session
                        is running.  Persisted so crash recovery knows which phase
                        the job is in.
    """

    standard = "standard"
    plan = "plan"
    plan_implementing = "plan_implementing"


class JobSource(StrEnum):
    """How the job was created."""

    managed = "managed"
    copilot_cli = "copilot_cli"
    claude_cli = "claude_cli"
    integration = "integration"


class InvalidStateTransitionError(CodePlaneError):
    """Raised when a job state transition is not allowed."""

    def __init__(self, from_state: JobState | None, to_state: JobState) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Invalid state transition: {from_state!r} -> {to_state!r}")


def validate_state_transition(from_state: JobState | None, to_state: JobState) -> None:
    """Validate a job state transition. Raises InvalidStateTransitionError if invalid."""
    valid_targets = _VALID_TRANSITIONS.get(from_state, set())
    if to_state not in valid_targets:
        raise InvalidStateTransitionError(from_state, to_state)


class Preset(StrEnum):
    """Action policy preset — selects the ``traceforge.governance`` profile the
    router enforces (see ``action_policy/preset_profiles.py``).

    autonomous — Generous budget; protected-path writes escalate; no USD ceiling.
                 Escalations handled by the monitor sidecar.
    supervised — Moderate budget and USD ceiling; cost pressure and protected-path
                 writes escalate; monitor handles escalations.
    locked     — Tight budget and USD ceiling; protected-path writes denied; hard
                 tool-call ceiling; monitor disabled — escalations go to the human.
    """

    autonomous = "autonomous"
    supervised = "supervised"
    locked = "locked"


# Valid values for SessionConfig.session_kind — used for telemetry grouping.
SessionKind = Literal["job", "planning", "preflight", "memory_extraction", "memory_compaction", "narrator", "sidecar"]


# -- Sidecar session configuration -------------------------------------------

# When in the job lifecycle a sidecar runs.
SidecarPhase = Literal["preflight", "midflight", "postflight"]

# How long a sidecar session lives.
#   ephemeral  — single completion call, then discarded
#   windowed   — lives for a bounded window (max_turns or timeout_s), then expires
#   persistent — lives for the entire job
SidecarLifetime = Literal["ephemeral", "windowed", "persistent"]


@dataclass
class SidecarConfig:
    """Declares a named sidecar session attached to a job."""

    name: str
    phase: SidecarPhase
    lifetime: SidecarLifetime
    system_prompt: str
    # Only meaningful for windowed lifetime — session expires after this many
    # LLM calls (None = no turn limit).
    max_turns: int | None = None
    # Only meaningful for windowed lifetime — session expires after this many
    # seconds of wall-clock time (None = no time limit).
    timeout_s: float | None = None
    # Telemetry dimension — defaults to "sidecar" but preflight/postflight
    # sidecars may use their own session_kind for separate cost tracking.
    session_kind: SessionKind = "sidecar"


# Well-known sidecar names used by built-in consumers.
SIDECAR_ARBITER = "arbiter"  # stall detection
SIDECAR_PLANNER = "planner"  # plan inference + turn classification
SIDECAR_ENRICHER = "enricher"  # trail enrichment + activity titles


@dataclass
class Chat:
    """A persistent, purely conversational Chat with no git footprint.

    ``project_id`` is nullable — a Chat started from global nav has none
    until a Job is launched from it or it is attached to a chain (Stories
    5.2/5.3 settle it then). This dataclass and everything that produces
    it (``chat_service.py``) must never depend on ``GitService`` (AD-12).
    """

    id: str
    project_id: str | None
    title: str
    created_at: datetime
    last_message_at: datetime
    status: str = "open"
    task_link_id: str | None = None


@dataclass
class ChatMessage:
    """A single message in a Chat's transcript.

    Append-only: messages are never edited or deleted. Ordered by
    ``created_at`` to reconstruct the transcript used to seed a Job
    launched from the Chat (Story 5.2, AD-12).
    """

    id: str
    chat_id: str
    role: str
    content: str
    created_at: datetime


@dataclass
class ChatTurnResult:
    """Persisted result of one conversational Chat turn."""

    user_message: ChatMessage
    assistant_message: ChatMessage | None
    state: Literal["assistant", "error"]
    error: str | None = None


@dataclass
class SidecarTemplate:
    """A saved sidecar definition in the user's library."""

    id: str
    name: str
    description: str
    definition_json: str  # serialized SidecarDefinition JSON
    created_at: datetime
    last_used_at: datetime | None = None
    enabled: bool = True


# -- Telemetry TypedDicts -----------------------------------------------------
# Typed structures for data flowing through the telemetry persistence layer.


class TelemetrySpanRow(TypedDict, total=False):
    """Shape of a row returned by TelemetrySpansRepository.list_for_job()."""

    id: int
    job_id: str
    session_kind: str
    span_type: str  # "tool" | "llm"
    name: str
    started_at: float
    duration_ms: float
    attrs: dict[str, Any]
    tool_category: str | None
    tool_target: str | None
    turn_number: int | None
    execution_phase: ExecutionPhase | None
    is_retry: bool | None
    retries_span_id: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cost_usd: float | None
    tool_args_json: str | None
    result_size_bytes: int | None
    error_kind: str | None
    turn_id: str | None
    preceding_context: str | None
    motivation_summary: str | None
    edit_motivations: str | None
    created_at: str | None


class FileChurnRow(TypedDict):
    """Shape of a row returned by TelemetrySpansRepository.file_write_churn()."""

    tool_target: str
    write_count: int
    retry_count: int


class FileAccessStatsRow(TypedDict, total=False):
    """Shape returned by FileAccessRepository.reread_stats()."""

    total_accesses: int
    unique_files: int
    total_reads: int
    total_writes: int
    reread_count: int


class CostAttributionRow(TypedDict, total=False):
    """Shape of a row returned by CostAttributionRepository.for_job()."""

    dimension: str
    bucket: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    call_count: int
    cache_read_tokens: int
    cache_write_tokens: int
    model: str | None


class TelemetrySummaryRow(TypedDict, total=False):
    """Shape of a row returned by TelemetrySummaryRepository.get()."""

    job_id: str
    session_kind: str
    sdk: str
    model: str
    repo: str
    branch: str
    status: str
    created_at: str
    completed_at: str | None
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_cost_usd: float
    premium_requests: float
    llm_call_count: int
    total_llm_duration_ms: int
    tool_call_count: int
    tool_failure_count: int
    total_tool_duration_ms: int
    compactions: int
    tokens_compacted: int
    approval_count: int
    approval_wait_ms: int
    agent_messages: int
    operator_messages: int
    context_window_size: int
    current_context_tokens: int
    quota_json: str | None
    updated_at: str
    # Added by 0009_cost_attribution
    total_turns: int
    retry_count: int
    retry_cost_usd: float
    file_read_count: int
    file_write_count: int
    unique_files_read: int
    file_reread_count: int
    peak_turn_cost_usd: float
    avg_turn_cost_usd: float
    cost_first_half_usd: float
    cost_second_half_usd: float
    diff_lines_added: int
    diff_lines_removed: int
    # Added by 0012_error_kind
    agent_error_count: int
    # Added by 0017_add_subagent_cost
    subagent_cost_usd: float
    # Latency attribution fields
    parallelism_ratio: float
    idle_ms: int


# -- Analytics aggregation TypedDicts -----------------------------------------


class AggregateStats(TypedDict, total=False):
    """Shape returned by TelemetrySummaryRepository.aggregate()."""

    total_jobs: int
    review: int
    completed: int
    succeeded: int
    failed: int
    cancelled: int
    running: int
    total_cost_usd: float
    total_tokens: int
    avg_duration_ms: float
    total_premium_requests: float
    total_tool_calls: int
    total_tool_failures: int
    total_agent_errors: int
    total_cache_read: int
    total_input_tokens: int
    total_subagent_cost_usd: float
    total_retry_cost_usd: float
    total_retry_count: int


class CostByDayRow(TypedDict):
    """Shape returned by TelemetrySummaryRepository.cost_by_day()."""

    date: str
    cost: float
    jobs: int


class CostByRepoRow(TypedDict):
    """Shape returned by TelemetrySummaryRepository.cost_by_repo()."""

    repo: str
    job_count: int
    succeeded: int
    failed: int
    total_cost_usd: float
    total_tokens: int
    tool_calls: int
    avg_duration_ms: float
    premium_requests: float


class CostByModelRow(TypedDict, total=False):
    """Shape returned by TelemetrySummaryRepository.cost_by_model()."""

    model: str
    sdk: str
    job_count: int
    total_cost_usd: float
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    avg_duration_ms: float
    premium_requests: float
    total_turns: int
    total_tool_calls: int
    total_diff_lines: int
    cost_per_job: float
    cost_per_minute: float
    cost_per_turn: float
    cost_per_tool_call: float
    cost_per_diff_line: float
    cost_per_mtok: float
    cache_hit_rate: float


class ToolStatsRow(TypedDict, total=False):
    """Shape returned by TelemetrySpansRepository.tool_stats()."""

    name: str
    count: int
    avg_duration_ms: float
    total_duration_ms: float
    failure_count: int
    p50_duration_ms: float
    p95_duration_ms: float
    p99_duration_ms: float


class ShellCommandRow(TypedDict):
    """Shape returned by TelemetrySpansRepository.shell_command_breakdown()."""

    command: str
    call_count: int
    total_cost_usd: float
    avg_duration_ms: float
    job_count: int


class RetryCostSummary(TypedDict):
    """Shape returned by TelemetrySpansRepository.retry_cost_summary()."""

    retry_cost_usd: float
    retry_count: int
    total_spans: int
    total_cost_usd: float


class CostDimensionRow(TypedDict):
    """Shape returned by CostAttributionRepository.by_dimension()."""

    bucket: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    call_count: int
    cache_read_tokens: int
    cache_write_tokens: int
    job_count: int


class FleetCostRow(TypedDict):
    """Shape returned by CostAttributionRepository.fleet_summary()."""

    dimension: str
    bucket: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    call_count: int
    cache_read_tokens: int
    cache_write_tokens: int
    job_count: int
    avg_cost_per_job: float


class LatencyAttributionRow(TypedDict, total=False):
    """Shape of a row returned by LatencyAttributionRepository.for_job()."""

    dimension: str
    bucket: str
    wall_clock_ms: int
    sum_duration_ms: int
    span_count: int
    p50_ms: int
    p95_ms: int
    max_ms: int
    pct_of_total: float


class FleetLatencyRow(TypedDict):
    """Shape returned by LatencyAttributionRepository.fleet_summary()."""

    dimension: str
    bucket: str
    avg_wall_clock_ms: float
    avg_sum_duration_ms: float
    total_span_count: int
    job_count: int
    avg_pct_of_total: float


class FileAccessRow(TypedDict):
    """Shape returned by FileAccessRepository.most_accessed_files()."""

    file_path: str
    access_count: int
    read_count: int
    write_count: int
    job_count: int


class ModelComparisonRow(TypedDict, total=False):
    """Shape returned by TelemetrySummaryRepository.model_comparison()."""

    model: str
    sdk: str
    job_count: int
    avg_cost: float
    avg_duration_ms: float
    total_cost_usd: float
    premium_requests: float
    merged: int
    pr_created: int
    discarded: int
    failed: int
    avg_verify_turns: float | None
    verify_job_count: int
    avg_diff_lines: float
    cache_hit_rate: float
    cost_per_job: float
    cost_per_minute: float
    cost_per_turn: float
    cost_per_tool_call: float
    cost_per_diff_line: float


@dataclass
class SessionConfig:
    workspace_path: str
    prompt: str
    job_id: str = ""
    sdk: str = "copilot"
    model: str | None = None
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    protected_paths: list[str] = field(default_factory=list)
    blocking_permission_handler: Callable[[str, str], Awaitable[str]] | None = None
    # Set when resuming a job to reconnect to an existing Copilot SDK session
    resume_sdk_session_id: str | None = None
    # Curated context injected via system_message (hidden from transcript)
    memory_context: str | None = None
    # When set, REPLACES the default CODEPLANE_SYSTEM_PROMPT entirely.
    # Used by sidecars and preflight to define their own identity instead of
    # inheriting the main agent's "proceed with task execution" framing.
    system_prompt_override: str | None = None
    # Maximum agent turns before the session is forcibly ended (None = SDK default)
    max_turns: int | None = None
    # Built-in tools to block (e.g. ["Bash", "Write", "Edit"] for read-only sessions)
    disallowed_tools: list[str] = field(default_factory=list)
    # Telemetry dimension — classifies this session for cost tracking.
    # "job" for main agent sessions, or "preflight", "narrator", "sidecar" for sidecar sessions.
    session_kind: SessionKind = "job"
    execution_capabilities: frozenset[str] | None = None
    recipe_run_id: str | None = None
    recipe_revision: int | None = None
    step_instance_id: str | None = None
    attempt_id: str | None = None
    execution_epoch: str | None = None
    defer_start: bool = False


@dataclass(frozen=True)
class CurrentExecutionContext:
    job_id: str
    execution_token: str | None


@dataclass(frozen=True)
class CurrentExecutionDispatchResult:
    job_id: str
    execution_token: str | None
    status: str
    reason: str | None = None
    message_event_id: str | None = None


@dataclass
class JobSpec:
    """Parameters for creating a new job — bundles the inputs to JobService.create_job."""

    repo: str
    prompt: str
    project_id: str | None = None
    base_ref: str | None = None
    branch: str | None = None
    title: str | None = None
    description: str | None = None
    worktree_name: str | None = None
    preset: Preset = Preset.supervised
    model: str | None = None
    sdk: str | None = None
    verify: bool | None = None
    self_review: bool | None = None
    max_turns: int | None = None
    verify_prompt: str | None = None
    self_review_prompt: str | None = None
    enable_stall_detection: bool | None = None
    enable_plan_tracking: bool | None = None
    parent_job_id: str | None = None
    parent_job_context: str | None = None
    mode: JobMode = JobMode.standard


@dataclass
class Project:
    """Domain representation of a Project (AD-5) — the sole entity that owns
    repo-path membership. A single-repo Project is not a special case: it is
    a Project with one entry in ``repo_paths``.
    """

    id: str
    name: str
    repo_paths: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass
class ProjectSummary:
    """Batch Overview summary for one Project (Story 2.2 / CAP-2).

    Sourced from a single batch query across all Projects' jobs — never N
    sequential per-Project fetches. Zero-job Projects still produce a summary
    with all-zero counts and ``last_activity_at=None``.
    """

    id: str
    name: str
    repo_paths: list[str]
    active_job_count: int = 0
    awaiting_input_count: int = 0
    failed_count: int = 0
    last_activity_at: datetime | None = None


@dataclass
class ProjectMembershipImpact:
    """Consequences of removing repositories from a Project."""

    active_job_count: int = 0
    historical_job_count: int = 0
    task_link_count: int = 0
    tracker_link_count: int = 0


@dataclass
class TaskLink:
    """Domain representation of a TaskLink (Story 4.2, AD-9).

    A thin, Project-scoped correlation row — never a parallel execution
    model competing with ``Job``. Exactly one of ``story_node_id`` /
    ``tracker_ticket_ref`` is guaranteed non-null at creation. ``depends_on``
    holds composite ``"{repo_path}::{story_node_id}"`` strings since
    ``story_node_id`` is only unique within one repo.
    """

    id: str
    project_id: str
    repo_path: str
    story_node_id: str | None
    depends_on: list[str]
    job_id: str | None
    tracker_ticket_ref: str | None
    prompt_override: str | None
    epic_id: str | None
    created_at: datetime
    updated_at: datetime
    state: TaskLinkState = TaskLinkState.waiting
    tracker_link_id: str | None = None
    chain_root_id: str = ""
    output_routes: list[str] = field(default_factory=list)


@dataclass
class ChatChainStatus:
    """Read-only narration snapshot for a Chat attached to a Task Recipe chain (Story 5.3).

    Produced entirely by polling existing ``TaskLinkRepository``/``JobRepository``
    reads — never by calling ``GitService`` or any job-creation function.
    ``job_id``/``job_state`` are ``None`` when the attached TaskLink has not
    yet spawned a Job.
    """

    task_link_id: str
    story_node_id: str | None
    repo_path: str
    job_id: str | None
    job_state: str | None


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
    repo: str | None
    project_id: str
    """The Project that owns this repo (AD-5) — never null; a Job cannot
    exist without an owning Project."""
    prompt: str
    state: JobState
    base_ref: str | None
    branch: str | None
    worktree_path: str | None
    session_id: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    pr_url: str | None = None
    merge_status: GitMergeOutcome | None = None
    """Git merge operation outcome: ``not_merged`` | ``merged`` | ``conflict``."""
    resolution: Resolution | None = None
    """User-facing job disposition (see :class:`Resolution`)."""
    archived_at: datetime | None = None
    title: str | None = None
    description: str | None = None
    worktree_name: str | None = None
    preset: Preset = Preset.supervised
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
    enable_stall_detection: bool | None = None
    enable_plan_tracking: bool | None = None
    version: int = 1
    parent_job_id: str | None = None
    source: str = "managed"
    external_session_id: str | None = None
    integration_binding_id: str | None = None
    observation_only: bool = False
    tail_offset: int = 0
    mode: JobMode = JobMode.standard
    artifact_collection_status: str = "pending"
    artifact_collection_error: str | None = None
    artifact_collection_session_count: int = 0
    artifact_collection_updated_at: datetime | None = None

    @property
    def execution_repo(self) -> str:
        if self.observation_only or not self.repo:
            raise StateConflictError("Observation history has no executable repository")
        return self.repo

    @property
    def execution_base_ref(self) -> str:
        if self.observation_only or not self.base_ref:
            raise StateConflictError("Observation history has no executable base reference")
        return self.base_ref

    @property
    def execution_workspace(self) -> str:
        repo = self.execution_repo
        return self.worktree_path or repo


@dataclass
class Approval:
    """Domain representation of an approval request."""

    id: str
    job_id: str
    description: str
    proposed_action: str | None
    requested_at: datetime
    resolved_at: datetime | None = None
    resolution: ApprovalResolution | None = None
    # When True this approval was triggered by a hard-blocked operation (e.g.
    # git reset --hard) and MUST NOT be auto-resolved by a blanket trust grant.
    # The operator must explicitly click Approve for each occurrence.
    requires_explicit_approval: bool = False
    # Operator notes attached when resolving (e.g. rejection feedback for plan mode)
    notes: str | None = None


@dataclass
class Artifact:
    """Domain representation of an artifact record."""

    id: str
    job_id: str
    name: str
    type: ArtifactType
    mime_type: str
    size_bytes: int
    disk_path: str
    phase: ExecutionPhase
    created_at: datetime
