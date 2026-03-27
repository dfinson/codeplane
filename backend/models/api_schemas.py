"""Pydantic request/response schemas — single source of truth for the API contract."""

from __future__ import annotations

from datetime import UTC, datetime  # noqa: TC003 — Pydantic resolves annotations at runtime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from backend.models.domain import (  # noqa: TC001 — Pydantic resolves annotations at runtime
    JobState,
    PermissionMode,
    Resolution,
)


class CamelModel(BaseModel):
    """Base model that serializes field names to camelCase.

    All datetime fields are guaranteed to include UTC timezone info,
    even when loaded from SQLite (which strips timezone).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _ensure_utc_datetimes(cls, data: Any) -> Any:
        """Attach UTC to any naive datetime values before validation."""
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, datetime) and value.tzinfo is None:
                    data[key] = value.replace(tzinfo=UTC)
        return data


# --- Enums ---
# JobState, PermissionMode, and Resolution are imported from backend.models.domain
# (canonical definitions live there). Re-exported here for backward compatibility.

from enum import StrEnum  # noqa: E402 — after domain imports to keep grouping clear


class ApprovalResolution(StrEnum):
    approved = "approved"
    rejected = "rejected"


class ResolutionAction(StrEnum):
    merge = "merge"
    smart_merge = "smart_merge"
    create_pr = "create_pr"
    discard = "discard"
    agent_merge = "agent_merge"


class ArtifactType(StrEnum):
    diff_snapshot = "diff_snapshot"
    agent_summary = "agent_summary"
    session_snapshot = "session_snapshot"
    session_log = "session_log"
    agent_plan = "agent_plan"
    telemetry_report = "telemetry_report"
    approval_history = "approval_history"
    document = "document"
    custom = "custom"


class ExecutionPhase(StrEnum):
    environment_setup = "environment_setup"
    agent_reasoning = "agent_reasoning"
    verification = "verification"
    finalization = "finalization"
    post_completion = "post_completion"


class LogLevel(StrEnum):
    debug = "debug"
    info = "info"
    warn = "warn"
    error = "error"


class HealthStatus(StrEnum):
    healthy = "healthy"


class WorkspaceEntryType(StrEnum):
    file = "file"
    directory = "directory"


class TranscriptRole(StrEnum):
    agent = "agent"
    agent_delta = "agent_delta"  # incremental text chunk streamed before the complete agent message
    operator = "operator"
    tool_call = "tool_call"
    tool_running = "tool_running"
    reasoning = "reasoning"
    divider = "divider"


class DiffLineType(StrEnum):
    context = "context"
    addition = "addition"
    deletion = "deletion"


class DiffFileStatus(StrEnum):
    added = "added"
    modified = "modified"
    deleted = "deleted"
    renamed = "renamed"


# --- Request Models ---


class CreateJobRequest(CamelModel):
    repo: str
    prompt: str
    base_ref: str | None = None
    branch: str | None = None
    permission_mode: PermissionMode | None = None
    model: str | None = None
    sdk: str | None = None
    verify: bool | None = None
    self_review: bool | None = None
    max_turns: int | None = Field(None, ge=1, le=10)
    verify_prompt: str | None = Field(None, max_length=5000)
    self_review_prompt: str | None = Field(None, max_length=5000)

    @model_validator(mode="before")
    @classmethod
    def _validate_sdk(cls, values: Any) -> Any:
        sdk = values.get("sdk")
        if sdk is not None:
            from backend.services.agent_adapter import AgentSDK

            try:
                AgentSDK(sdk)
            except ValueError:
                valid = ", ".join(e.value for e in AgentSDK)
                raise ValueError(f"Unknown SDK {sdk!r}. Valid options: {valid}") from None
        return values


class SendMessageRequest(CamelModel):
    content: str = Field(min_length=1, max_length=10_000)


class ResumeJobRequest(CamelModel):
    instruction: str | None = Field(default=None, max_length=50_000)


class ContinueJobRequest(CamelModel):
    instruction: str = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def _validate_instruction_not_blank(self) -> ContinueJobRequest:
        if not self.instruction.strip():
            raise ValueError("Instruction must not be blank")
        return self


class ResolveApprovalRequest(CamelModel):
    resolution: ApprovalResolution


class UpdateSettingsRequest(CamelModel):
    """Structured settings update — only include fields to change."""

    max_concurrent_jobs: int | None = Field(None, ge=1, le=10)
    permission_mode: PermissionMode | None = None
    auto_push: bool | None = None
    cleanup_worktree: bool | None = None
    delete_branch_after_merge: bool | None = None
    artifact_retention_days: int | None = Field(None, ge=1, le=365)
    max_artifact_size_mb: int | None = Field(None, ge=1, le=10_000)
    auto_archive_days: int | None = Field(None, ge=1, le=365)
    verify: bool | None = None
    self_review: bool | None = None
    max_turns: int | None = Field(None, ge=1, le=10)
    verify_prompt: str | None = Field(None, max_length=5000)
    self_review_prompt: str | None = Field(None, max_length=5000)


class SettingsResponse(CamelModel):
    max_concurrent_jobs: int
    permission_mode: PermissionMode
    auto_push: bool
    cleanup_worktree: bool
    delete_branch_after_merge: bool
    artifact_retention_days: int
    max_artifact_size_mb: int
    auto_archive_days: int
    verify: bool
    self_review: bool
    max_turns: int
    verify_prompt: str
    self_review_prompt: str


class RegisterRepoRequest(CamelModel):
    source: str
    clone_to: str | None = None


class SuggestNamesRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=50_000)
    repo: str | None = None


class SuggestNamesResponse(CamelModel):
    title: str
    branch_name: str
    worktree_name: str


# --- Response Models ---


class CreateJobResponse(CamelModel):
    id: str
    state: JobState
    title: str | None = None
    branch: str | None = None
    worktree_path: str | None = None
    sdk: str = "copilot"
    created_at: datetime


class JobResponse(CamelModel):
    id: str
    repo: str
    prompt: str
    title: str | None = None
    state: JobState
    base_ref: str
    worktree_path: str | None
    branch: str | None
    permission_mode: PermissionMode | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    pr_url: str | None = None
    merge_status: str | None = None
    """Git merge operation outcome (``not_merged`` | ``merged`` | ``conflict``)."""
    resolution: Resolution | None = None
    """User-facing job disposition — see :class:`~backend.models.domain.Resolution`."""
    archived_at: datetime | None = None
    failure_reason: str | None = None
    progress_headline: str | None = None
    progress_summary: str | None = None
    model: str | None = None
    sdk: str = "copilot"
    worktree_name: str | None = None
    verify: bool | None = None
    self_review: bool | None = None
    max_turns: int | None = None
    verify_prompt: str | None = None
    self_review_prompt: str | None = None
    parent_job_id: str | None = None


class JobListResponse(CamelModel):
    items: list[JobResponse]
    cursor: str | None
    has_more: bool


class SendMessageResponse(CamelModel):
    seq: int
    timestamp: datetime


class SessionResumedPayload(CamelModel):
    job_id: str
    session_number: int
    timestamp: datetime


class ApprovalResponse(CamelModel):
    id: str
    job_id: str
    description: str
    proposed_action: str | None
    requested_at: datetime
    resolved_at: datetime | None
    resolution: ApprovalResolution | None
    # True when this approval was triggered by a hard-blocked operation (e.g.
    # git reset --hard) that cannot be auto-resolved by a trust grant.
    requires_explicit_approval: bool = False


class ArtifactResponse(CamelModel):
    id: str
    job_id: str
    name: str
    type: ArtifactType
    mime_type: str
    size_bytes: int
    phase: ExecutionPhase
    created_at: datetime


class ArtifactListResponse(CamelModel):
    items: list[ArtifactResponse]


class WorkspaceEntry(CamelModel):
    path: str
    type: WorkspaceEntryType
    size_bytes: int | None = None


class WorkspaceListResponse(CamelModel):
    items: list[WorkspaceEntry]
    cursor: str | None
    has_more: bool


class TranscribeResponse(CamelModel):
    text: str


class ModelInfoResponse(CamelModel):
    """Model information returned by the agent SDK."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str


class HealthResponse(CamelModel):
    status: HealthStatus
    version: str
    uptime_seconds: float
    active_jobs: int
    queued_jobs: int


class RegisterRepoResponse(CamelModel):
    path: str
    source: str
    cloned: bool


class RepoListResponse(CamelModel):
    items: list[str]


class RepoDetailResponse(CamelModel):
    path: str
    origin_url: str | None = None
    base_branch: str | None = None
    current_branch: str | None = None
    active_job_count: int = 0
    platform: str | None = None


# --- SSE Payload Models ---


class LogLinePayload(CamelModel):
    job_id: str
    seq: int
    timestamp: datetime
    level: LogLevel
    message: str
    context: dict[str, Any] | None = None
    session_number: int | None = None


class TranscriptPayload(CamelModel):
    job_id: str
    seq: int
    timestamp: datetime
    role: TranscriptRole
    content: str
    # Optional rich fields — only present for specific roles
    title: str | None = None  # annotation title on agent messages
    turn_id: str | None = None  # groups reasoning + tool_calls + message
    tool_name: str | None = None  # role=tool_call: tool identifier
    tool_args: str | None = None  # role=tool_call: JSON-serialized arguments
    tool_result: str | None = None  # role=tool_call: text output from tool
    tool_success: bool | None = None  # role=tool_call: whether execution succeeded
    tool_issue: str | None = None  # role=tool_call: short issue summary when attention is needed
    tool_intent: str | None = None  # role=tool_call: SDK-provided intent string
    tool_title: str | None = None  # role=tool_call: SDK-provided display title
    tool_display: str | None = None  # role=tool_call: deterministic per-tool label
    tool_duration_ms: int | None = None  # role=tool_call: execution time in milliseconds
    tool_group_summary: str | None = None  # AI-generated summary for the tool group turn


class ToolGroupSummaryPayload(CamelModel):
    """AI-generated one-line summary for a tool group in an agent turn."""

    job_id: str
    turn_id: str
    summary: str  # short label, e.g. "bash: ran test suite"
    timestamp: datetime


class DiffLineModel(CamelModel):
    type: DiffLineType
    content: str


class DiffHunkModel(CamelModel):
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[DiffLineModel]


class DiffFileModel(CamelModel):
    path: str
    status: DiffFileStatus
    additions: int
    deletions: int
    hunks: list[DiffHunkModel]


class JobStateChangedPayload(CamelModel):
    job_id: str
    previous_state: JobState | None
    new_state: JobState
    timestamp: datetime


class ApprovalRequestedPayload(CamelModel):
    job_id: str
    approval_id: str
    description: str
    proposed_action: str | None = None
    timestamp: datetime
    requires_explicit_approval: bool = False


class ApprovalResolvedPayload(CamelModel):
    job_id: str
    approval_id: str
    resolution: ApprovalResolution
    timestamp: datetime


class DiffUpdatePayload(CamelModel):
    job_id: str
    changed_files: list[DiffFileModel]


class SessionHeartbeatPayload(CamelModel):
    job_id: str
    session_id: str
    timestamp: datetime


class MergeCompletedPayload(CamelModel):
    job_id: str
    branch: str
    base_ref: str
    strategy: str  # ff_only | merge
    timestamp: datetime


class MergeConflictPayload(CamelModel):
    job_id: str
    branch: str
    base_ref: str
    conflict_files: list[str]
    fallback: str  # pr_created | none
    pr_url: str | None = None


# --- Platform Models ---


class PlatformStatusResponse(CamelModel):
    platform: str
    authenticated: bool
    user: str | None = None
    error: str | None = None


class PlatformStatusListResponse(CamelModel):
    items: list[PlatformStatusResponse]
    timestamp: datetime


class ResolveJobRequest(CamelModel):
    action: ResolutionAction


class ResolveJobResponse(CamelModel):
    resolution: Resolution | ResolutionAction
    pr_url: str | None = None
    conflict_files: list[str] | None = None
    error: str | None = None


class JobFailedPayload(CamelModel):
    job_id: str
    reason: str
    timestamp: datetime


class JobReviewPayload(CamelModel):
    """Emitted when the agent session exits cleanly and the job enters review."""

    job_id: str
    pr_url: str | None = None
    merge_status: str | None = None
    """Git merge operation outcome (``not_merged`` | ``merged`` | ``conflict``)."""
    resolution: str | None = None
    """User-facing job disposition — see :class:`~backend.models.domain.Resolution`."""
    model_downgraded: bool = False
    requested_model: str | None = None
    actual_model: str | None = None
    timestamp: datetime


class JobCompletedPayload(CamelModel):
    """Emitted when an operator resolves a review job to a final state."""

    job_id: str
    resolution: str | None = None
    pr_url: str | None = None
    timestamp: datetime


class JobResolvedPayload(CamelModel):
    job_id: str
    resolution: Resolution
    pr_url: str | None = None
    conflict_files: list[str] | None = None
    error: str | None = None
    timestamp: datetime


class ModelDowngradedPayload(CamelModel):
    job_id: str
    requested_model: str
    actual_model: str
    timestamp: datetime


class JobArchivedPayload(CamelModel):
    job_id: str
    timestamp: datetime


class JobTitleUpdatedPayload(CamelModel):
    job_id: str
    title: str | None = None
    branch: str | None = None
    timestamp: datetime


class ProgressHeadlinePayload(CamelModel):
    job_id: str
    headline: str
    headline_past: str
    summary: str
    timestamp: datetime
    replaces_count: int = 0


PlanStepStatus = Literal["pending", "active", "done", "skipped"]


class AgentPlanStep(CamelModel):
    label: str
    status: PlanStepStatus


class AgentPlanPayload(CamelModel):
    job_id: str
    steps: list[AgentPlanStep]
    timestamp: datetime


class TelemetryUpdatedPayload(CamelModel):
    job_id: str
    timestamp: datetime


class SnapshotPayload(CamelModel):
    jobs: list[JobResponse]
    pending_approvals: list[ApprovalResponse]


class JobSnapshotResponse(CamelModel):
    """Full state hydration for a single job — used after reconnect or page refresh."""

    job: JobResponse
    logs: list[LogLinePayload]
    transcript: list[TranscriptPayload]
    diff: list[DiffFileModel]
    approvals: list[ApprovalResponse]
    timeline: list[ProgressHeadlinePayload]


class SDKInfoResponse(CamelModel):
    id: str
    name: str
    enabled: bool
    status: Literal["ready", "not_installed", "not_configured"]
    authenticated: bool | None = None  # None = unknown / not applicable
    hint: str = ""  # actionable suggestion for the user


class SDKListResponse(CamelModel):
    default: str
    sdks: list[SDKInfoResponse]


# --- Terminal schemas (moved from backend/api/terminal.py) ---


class CreateTerminalSessionRequest(CamelModel):
    shell: str | None = None
    cwd: str | None = None
    job_id: str | None = None
    prompt_label: str | None = None


class CreateTerminalSessionResponse(CamelModel):
    id: str
    shell: str
    cwd: str
    job_id: str | None = None
    pid: int


class TerminalSessionInfo(CamelModel):
    id: str
    shell: str
    cwd: str
    job_id: str | None = None
    pid: int
    clients: int


class TerminalAskRequest(CamelModel):
    prompt: str
    context: str | None = None  # recent terminal output for context


class TerminalAskResponse(CamelModel):
    command: str
    explanation: str


# --- Typed response models for previously untyped dict endpoints ---


class TrustJobResponse(CamelModel):
    resolved: int


class CleanupWorktreesResponse(CamelModel):
    removed: int


class BrowseEntry(CamelModel):
    name: str
    path: str
    is_git_repo: bool = False


class BrowseDirectoryResponse(CamelModel):
    current: str
    parent: str | None = None
    items: list[BrowseEntry]


class WorkspaceFileResponse(CamelModel):
    path: str
    content: str


# ---------------------------------------------------------------------------
# Cost Analytics response models
# ---------------------------------------------------------------------------


class CostAttributionBucket(CamelModel):
    """A single bucket within a cost attribution dimension."""

    dimension: str
    bucket: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0


class TurnEconomics(CamelModel):
    """Turn economics summary for a single job."""

    total_turns: int = 0
    peak_turn_cost_usd: float = 0.0
    avg_turn_cost_usd: float = 0.0
    cost_first_half_usd: float = 0.0
    cost_second_half_usd: float = 0.0


class FileAccessStats(CamelModel):
    """File I/O statistics for a single job."""

    total_accesses: int = 0
    unique_files: int = 0
    total_reads: int = 0
    total_writes: int = 0
    reread_count: int = 0


class NormalizedModelMetrics(CamelModel):
    """Per-model metrics with normalization toggles."""

    model: str
    sdk: str
    job_count: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    cost_per_job: float = 0.0
    cost_per_minute: float = 0.0
    cost_per_turn: float = 0.0
    cost_per_tool_call: float = 0.0
    cost_per_diff_line: float = 0.0
    cost_per_mtok: float = 0.0
    cache_hit_rate: float = 0.0
