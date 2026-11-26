"""Pydantic request/response schemas — single source of truth for the API contract.

Base model, enums, and telemetry schemas live in ``backend.models.schemas``
sub-modules for navigability. Everything is re-exported here so existing
``from backend.models.api_schemas import X`` imports continue to work.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic resolves annotations at runtime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from backend.models.domain import (  # noqa: TC001 — Pydantic resolves annotations at runtime
    ApprovalResolution,
    GitMergeOutcome,
    JobMode,
    JobState,
    Preset,
    Resolution,
)

# Domain-grouped sub-modules — canonical definitions for base types and telemetry.
# Re-exported here for backward compatibility.
from backend.models.schemas.base import *  # noqa: E402,F401,F403
from backend.models.schemas.external_gate import *  # noqa: E402,F401,F403
from backend.models.schemas.integrations import *  # noqa: E402,F401,F403
from backend.models.schemas.metrics import *  # noqa: E402,F401,F403
from backend.models.schemas.recipes import *  # noqa: E402,F401,F403
from backend.models.schemas.reviews import *  # noqa: E402,F401,F403
from backend.models.schemas.telemetry import *  # noqa: E402,F401,F403
from backend.models.schemas.tracker_operations import (
    ReconcileTrackerOperationRequest as ReconcileTrackerOperationRequest,
)
from backend.models.schemas.tracker_operations import (
    RetryTrackerOperationRequest as RetryTrackerOperationRequest,
)
from backend.models.schemas.tracker_operations import (
    TrackerApprovalSnapshot as TrackerApprovalSnapshot,
)
from backend.models.schemas.tracker_operations import (
    TrackerOperationListResponse as TrackerOperationListResponse,
)
from backend.models.schemas.tracker_operations import (
    TrackerOperationResponse as TrackerOperationResponse,  # noqa: TC001 -- Pydantic resolves this at runtime.
)
from backend.models.schemas.tracker_operations import (
    TrackerOperationState as TrackerOperationState,
)
from backend.models.schemas.tracker_operations import (
    TrackerProviderReceipt as TrackerProviderReceipt,
)
from backend.models.schemas.tracker_operations import (
    TrackerReconciliation as TrackerReconciliation,
)
from backend.models.schemas.tracker_operations import (
    TrackerTargetKind as TrackerTargetKind,
)
from backend.models.session_event import EventMetadata  # noqa: TC001 - Pydantic resolves annotations at runtime.
from backend.models.usage import UsageAmount  # noqa: TC001 - Pydantic resolves annotations at runtime.

if TYPE_CHECKING:
    from backend.models.domain import Job


# --- Request Models ---


class CreateJobRequest(CamelModel):
    repo: str
    prompt: str
    project_id: str | None = None
    base_ref: str | None = None
    branch: str | None = None
    title: str | None = None
    description: str | None = None
    worktree_name: str | None = None
    preset: Preset | None = None
    model: str | None = None
    sdk: str | None = None
    verify: bool | None = None
    self_review: bool | None = None
    max_turns: int | None = Field(None, ge=1, le=10)
    verify_prompt: str | None = Field(None, max_length=5000)
    self_review_prompt: str | None = Field(None, max_length=5000)
    enable_stall_detection: bool | None = None
    enable_plan_tracking: bool | None = None
    session_token: str | None = Field(None, max_length=64)
    mode: JobMode | None = None
    recipe: RecipeSelection | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_mode_and_sdk(cls, values: Any) -> Any:
        mode = values.get("mode")
        if mode == JobMode.plan_implementing:
            raise ValueError("Cannot create a job in plan_implementing mode — use 'plan' or 'standard'")
        sdk = values.get("sdk")
        if sdk is not None:
            from backend.models.domain import AgentSDK

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
    expected_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    start_request_key: str | None = Field(default=None, min_length=1, max_length=128)
    recipe: RecipeSelection | None = None

    @model_validator(mode="after")
    def _require_recipe_target(self) -> ResumeJobRequest:
        if (self.start_request_key is not None or self.recipe is not None) and self.expected_run_id is None:
            raise ValueError("Recipe continuation requires the expected run identity.")
        return self


class ResolveGateRequest(CamelModel):
    """Operator resolution for a sidecar gate verdict."""

    action: str = Field(description="'approve' or 'reject'", pattern=r"^(approve|reject)$")
    message: str | None = Field(default=None, max_length=10_000, description="Optional follow-up message to the agent")


class ContinueJobRequest(CamelModel):
    instruction: str = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def _validate_instruction_not_blank(self) -> ContinueJobRequest:
        if not self.instruction.strip():
            raise ValueError("Instruction must not be blank")
        return self


class ResolveApprovalRequest(CamelModel):
    resolution: ApprovalResolution
    notes: str | None = Field(None, max_length=10_000)


class ResolveBatchRequest(CamelModel):
    """Resolve a pending action policy batch."""

    batch_id: str
    resolution: str  # approved / rejected / partial
    approved_ids: list[str] | None = None
    trust_grant_id: str | None = None


class ResolveBatchResponse(CamelModel):
    resolved: bool


class UpdateSettingsRequest(CamelModel):
    """Structured settings update — only include fields to change."""

    model_config = ConfigDict(extra="forbid")

    max_concurrent_jobs: int | None = Field(None, ge=1, le=10)
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
    cli_sidecars: list[str] | None = Field(
        None,
        description="Sidecar names for CLI sessions; null leaves current value unchanged",
    )


class SettingsResponse(CamelModel):
    max_concurrent_jobs: int
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
    cli_sidecars: list[str] | None


class RegisterRepoRequest(CamelModel):
    source: str
    clone_to: str | None = None
    mode: Literal["register", "clone"] | None = None


class CreateRepoRequest(CamelModel):
    path: str
    name: str | None = None


class CreateRepoResponse(CamelModel):
    path: str
    name: str


class CreateProjectRequest(CamelModel):
    name: str = Field(min_length=1, max_length=200)
    repo_paths: list[str] = Field(min_length=1)


class UpdateProjectRequest(CamelModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    repo_paths: list[str] | None = Field(None, min_length=1)
    confirm_repo_removal: bool = False


class CreateManualTaskLinkRequest(CamelModel):
    """Create a TaskLink directly from an existing tracker ticket (Story 4.3)."""

    repo_path: str = Field(min_length=1)
    tracker_link_id: str = Field(min_length=1)
    tracker_ticket_ref: str = Field(min_length=1)
    prompt_override: str = Field(min_length=1)
    output_routes: list[Literal["tracker_write"]] = Field(default_factory=list)

    @field_validator("repo_path", "tracker_link_id", "tracker_ticket_ref", "prompt_override")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value must not be blank")
        return stripped


class SuggestNamesRequest(CamelModel):
    prompt: str = Field(min_length=1, max_length=50_000)
    repo: str | None = None


class SuggestNamesResponse(CamelModel):
    title: str
    description: str
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
    repo: str | None
    prompt: str
    project_id: str | None = None
    title: str | None = None
    description: str | None = None
    state: JobState
    base_ref: str | None
    worktree_path: str | None
    branch: str | None
    preset: Preset | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    pr_url: str | None = None
    merge_status: GitMergeOutcome | None = None
    """Git merge operation outcome — see :class:`~backend.models.domain.GitMergeOutcome`."""
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
    enable_stall_detection: bool | None = None
    enable_plan_tracking: bool | None = None
    parent_job_id: str | None = None
    source: str = "managed"
    external_session_id: str | None = None
    integration_binding_id: str | None = None
    observation_only: bool = False
    mode: JobMode = JobMode.standard
    total_cost_usd: float | None = None
    cost_usage: list[UsageAmount] | None = None
    """Source-typed cost totals. The legacy total_cost_usd field alone proves no currency."""
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    @field_validator("project_id", mode="before")
    @classmethod
    def _normalize_project_id(cls, value: Any) -> str | None:
        """Coerce UUID-like ids and discard mock values that aren't real project IDs."""
        from uuid import UUID

        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, UUID):
            return str(value)
        return None

    @classmethod
    def from_domain(cls, job: Job, **overrides: Any) -> JobResponse:
        """Build a JobResponse from a domain Job, with optional field overrides."""
        return cls(
            id=job.id,
            repo=job.repo,
            prompt=job.prompt,
            project_id=job.project_id,
            title=job.title,
            description=job.description,
            state=job.state,
            base_ref=job.base_ref,
            worktree_path=job.worktree_path,
            branch=job.branch,
            preset=job.preset,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
            pr_url=job.pr_url,
            merge_status=job.merge_status,
            resolution=job.resolution,
            archived_at=job.archived_at,
            failure_reason=job.failure_reason,
            model=job.model,
            sdk=job.sdk,
            worktree_name=job.worktree_name,
            verify=job.verify,
            self_review=job.self_review,
            max_turns=job.max_turns,
            verify_prompt=job.verify_prompt,
            self_review_prompt=job.self_review_prompt,
            enable_stall_detection=job.enable_stall_detection,
            enable_plan_tracking=job.enable_plan_tracking,
            parent_job_id=job.parent_job_id,
            source=job.source,
            external_session_id=job.external_session_id,
            integration_binding_id=job.integration_binding_id,
            observation_only=job.observation_only,
            mode=JobMode(job.mode) if isinstance(job.mode, str) else JobMode.standard,
            **overrides,
        )


class RecipeResumeResponse(JobResponse):
    recipe_run: RecipeRunResponse


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
    notes: str | None = None


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
    collection_status: Literal["pending", "collecting", "completed", "failed", "not_applicable"] = "pending"
    collection_error: str | None = None
    collection_updated_at: datetime | None = None


class ModelListResponse(CamelModel):
    items: list[ModelInfoResponse]


class LogListResponse(CamelModel):
    items: list[LogLinePayload]


class DiffListResponse(CamelModel):
    items: list[DiffFileModel]


class TranscriptListResponse(CamelModel):
    items: list[TranscriptPayload]


class StepListResponse(CamelModel):
    items: list[PlanStepPayload]


class TimelineListResponse(CamelModel):
    items: list[ProgressHeadlinePayload]


class ApprovalListResponse(CamelModel):
    items: list[ApprovalResponse]


class JobPendingDecision(CamelModel):
    approval: ApprovalResponse
    recipe_run_id: str | None
    step_instance_id: str | None
    attempt_id: str | None
    human_gate: bool


class JobPendingDecisionPage(CamelModel):
    items: list[JobPendingDecision]
    next_cursor: str | None


class TranscriptSearchListResponse(CamelModel):
    items: list[TranscriptSearchResult]


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
    registered: bool


class RepoListResponse(CamelModel):
    items: list[str]


class RepoDetailResponse(CamelModel):
    path: str
    origin_url: str | None = None
    base_branch: str | None = None
    current_branch: str | None = None
    active_job_count: int = 0
    platform: str | None = None


class RepoJobSummary(CamelModel):
    """Minimal job info for repo dashboard."""

    id: str
    title: str | None = None
    state: str
    created_at: datetime
    completed_at: datetime | None = None
    total_cost_usd: float | None = None
    model: str | None = None


class RepoCostSummary(CamelModel):
    """Aggregated cost for a repo over a time period."""

    total_cost_usd: float | None = None
    total_jobs: int = 0
    total_tokens: int = 0


class ProjectResponse(CamelModel):
    """A Project — the sole entity that owns repo-path membership (AD-5)."""

    id: str
    name: str
    repo_paths: list[str]
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(CamelModel):
    items: list[ProjectResponse]


class ProjectSummaryResponse(CamelModel):
    """Batch Overview summary for one Project (Story 2.2 / CAP-2)."""

    id: str
    name: str
    repo_paths: list[str]
    active_job_count: int = 0
    awaiting_input_count: int = 0
    failed_count: int = 0
    last_activity_at: datetime | None = None


class ProjectListSummaryResponse(CamelModel):
    items: list[ProjectSummaryResponse]


class TaskLinkResponse(CamelModel):
    """A TaskLink — a thin, Project-scoped correlation row (Story 4.2, AD-9)."""

    id: str
    project_id: str
    repo_path: str
    story_node_id: str | None
    depends_on: list[str]
    chain_root_id: str
    state: Literal["waiting", "ready", "starting", "running", "completed", "failed"]
    job_id: str | None
    tracker_link_id: str | None
    tracker_ticket_ref: str | None
    prompt_override: str | None
    epic_id: str | None
    output_routes: list[str]
    created_at: datetime
    updated_at: datetime


class TaskLinkListResponse(CamelModel):
    items: list[TaskLinkResponse]


class TaskDependencyStatus(CamelModel):
    key: str
    task_link_id: str | None
    job_id: str | None
    job_state: str | None
    resolution: str | None
    satisfied: bool


class TaskLinkInspectionResponse(CamelModel):
    task: TaskLinkResponse
    prompt: str
    prompt_source: Literal["stored_override", "historical_reference"]
    dependencies: list[TaskDependencyStatus]
    can_start: bool
    disabled_reason: str | None
    pending_approval_ids: list[str]
    requires_chain_approval: bool
    sdk: str
    model: str | None
    preset: Preset
    mode: JobMode
    recipe_revision_id: str | None


ProjectBoardColumn = Literal["ready_blocked", "in_progress", "needs_decision", "failed"]


class ProjectBoardTotals(CamelModel):
    task_count: int
    linked_job_count: int
    standalone_job_count: int
    pending_approval_count: int
    review_job_count: int
    delivery_attention_count: int
    work_counts: dict[ProjectBoardColumn, int]


class ProjectRecipeStep(CamelModel):
    id: str
    title: str
    state: str
    reason: str | None


class ProjectRecipeSummary(CamelModel):
    run_id: str
    revision: int
    name: str
    state: str
    reason: str | None
    steps: list[ProjectRecipeStep]
    remaining_step_count: int


class ProjectTaskSummary(CamelModel):
    id: str
    repo_path: str
    story_node_id: str | None
    tracker_ticket_ref: str | None
    state: str


class ProjectBoardItem(CamelModel):
    id: str
    column: ProjectBoardColumn
    job: JobResponse | None
    tasks: list[ProjectTaskSummary]
    task_count: int
    pending_approval_count: int
    delivery_attention_count: int
    recipe: ProjectRecipeSummary | None
    has_failure: bool


class ProjectBoardPage(CamelModel):
    items: list[ProjectBoardItem]
    next_cursor: str | None


class ProjectBoardResponse(CamelModel):
    project: ProjectResponse
    totals: ProjectBoardTotals
    columns: dict[ProjectBoardColumn, ProjectBoardPage]


class ProjectApprovalPage(CamelModel):
    items: list[ApprovalResponse]
    next_cursor: str | None


class ProjectDeliveryPage(CamelModel):
    items: list[TrackerOperationResponse]
    next_cursor: str | None


class RepoSummaryResponse(CamelModel):
    """Aggregated overview for a single repo dashboard."""

    path: str
    origin_url: str | None = None
    base_branch: str | None = None
    current_branch: str | None = None
    platform: str | None = None
    recent_jobs: list[RepoJobSummary] = []
    active_job_count: int = 0
    cost: RepoCostSummary = RepoCostSummary()


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
    event_id: str
    sequence: int | None = None
    timestamp: datetime
    kind: str  # dotted TF event kind (e.g. "message.assistant", "tool.call.completed")
    content: str
    admission_status: str | None = None
    execution_status: str | None = None
    operator_content: str | None = None
    # Optional rich fields — only present for specific kinds
    title: str | None = None  # annotation title on agent messages
    turn_id: str | None = None  # groups reasoning + tool_calls + message
    tool_call_id: str | None = None  # pairs tool.call.started/completed
    metadata: EventMetadata | None = None
    pairing_status: Literal["unresolved"] | None = None
    pairing_reason: str | None = None
    tool_name: str | None = None  # tool.call.*: tool identifier
    arguments: str | None = None  # tool.call.*: JSON-serialized arguments
    result: str | None = None  # tool.call.completed: text output from tool
    success: bool | None = None  # tool.call.completed: whether execution succeeded
    tool_issue: str | None = None  # tool.call.*: short issue summary when attention is needed
    tool_intent: str | None = None  # tool.call.*: intent string (from metadata.motivation)
    tool_title: str | None = None  # tool.call.*: SDK-provided display title
    tool_display: str | None = None  # tool.call.*: deterministic per-tool label (char-capped)
    tool_display_full: str | None = None  # tool.call.*: same label, no char truncation (CSS-based)
    tool_duration_ms: int | None = None  # tool.call.*: execution time in ms (from metadata.duration_ms)
    tool_group_summary: str | None = None  # AI-generated summary for the tool group turn
    tool_visibility: str | None = None  # "hidden" | "collapsed" | "visible"
    step_id: str | None = None
    step_number: int | None = None
    # Sidecar fields — present when kind is a "sidecar.*" event
    sidecar_name: str | None = None
    sidecar_icon: str | None = None
    sidecar_description: str | None = None
    sidecar_template_id: str | None = None


class ToolGroupSummaryPayload(CamelModel):
    """AI-generated one-line summary for a tool group in an agent turn."""

    job_id: str
    turn_id: str
    summary: str  # short label, e.g. "bash: ran test suite"
    timestamp: datetime


class SidecarTranscriptPayload(CamelModel):
    """Sidecar output injected into the transcript feed."""

    job_id: str
    seq: int
    timestamp: datetime
    name: str | None = None
    icon: str | None = None
    description: str | None = None
    template_id: str | None = None
    content: str = ""


class PreflightToolCallPayload(CamelModel):
    """A single tool invocation captured during the preflight session."""

    tool_name: str
    tool_args: str | None = None
    result_preview: str = ""
    duration_ms: float | None = None


class PreflightReportPayload(CamelModel):
    """Structured report from the preflight scout session."""

    job_id: str
    timestamp: datetime
    elapsed_ms: float = 0.0
    tool_calls: list[PreflightToolCallPayload] = []
    brief_length: int = 0


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
    truncated: bool = False
    raw_size: int | None = None
    write_count: int | None = None
    retry_count: int | None = None


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
    last_activity_at: datetime | None = None
    active_tool_name: str | None = None
    active_tool_since: datetime | None = None


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
    timestamp: datetime


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
    merge_status: GitMergeOutcome | None = None
    """Git merge operation outcome — see :class:`~backend.models.domain.GitMergeOutcome`."""
    resolution: Resolution | None = None
    """User-facing job disposition — see :class:`~backend.models.domain.Resolution`."""
    model_downgraded: bool = False
    requested_model: str | None = None
    actual_model: str | None = None
    timestamp: datetime


class JobCompletedPayload(CamelModel):
    """Emitted when an operator resolves a review job to a final state."""

    job_id: str
    resolution: Resolution | None = None
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
    description: str | None = None
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
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class StepEntriesReassignedPayload(CamelModel):
    job_id: str
    turn_id: str
    old_step_id: str
    new_step_id: str


class StallDetectedPayload(CamelModel):
    job_id: str = ""
    tool_name: str = ""
    elapsed: str = ""
    reason: str = ""


class ContextHandoffPayload(CamelModel):
    job_id: str = ""
    source: str = ""  # "preflight" | "resume" | "resume_native" | "followup"
    source_session_id: str | None = None
    summary: str = ""
    content: str | None = None
    timestamp: datetime = datetime.min


class SnapshotPayload(CamelModel):
    jobs: list[JobResponse]
    pending_approvals: list[ApprovalResponse]


class SecondarySessionEntryResponse(CamelModel):
    seq: int
    kind: str
    content: str
    tool_name: str | None = None
    tool_args: str | None = None
    duration_ms: float | None = None
    tool_result: str | None = None
    tool_display: str | None = None
    tool_display_full: str | None = None
    tool_success: bool | None = None
    tool_issue: str | None = None
    tool_visibility: str | None = None


class SecondarySessionResponse(CamelModel):
    id: str
    kind: str
    name: str
    icon: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    output: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    entries: list[SecondarySessionEntryResponse] = []


class VerificationResultResponse(CamelModel):
    phase: Literal["verify", "self_review"]
    attempt: int
    status: Literal["completed", "failed", "interrupted"]
    session_number: int
    sdk_session_id: str | None = None
    started_at: datetime
    completed_at: datetime
    error: str | None = None


class JobSnapshotResponse(CamelModel):
    """Full state hydration for a single job — used after reconnect or page refresh."""

    job: JobResponse
    logs: list[LogLinePayload]
    transcript: list[TranscriptPayload]
    diff: list[DiffFileModel]
    approvals: list[ApprovalResponse]
    timeline: list[ProgressHeadlinePayload]
    steps: list[PlanStepPayload] = []
    turn_summaries: list[TurnSummaryPayload] = []
    secondary_sessions: list[SecondarySessionResponse] = []
    context_handoffs: list[ContextHandoffPayload] = []
    verification_results: list[VerificationResultResponse] = []


class SDKInfoResponse(CamelModel):
    id: str
    name: str
    enabled: bool
    status: Literal["ready", "not_installed", "not_configured", "disabled_by_policy"]
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
    pid: int | None = None
    clients: int
    observer: bool = False


class TerminalSessionListResponse(CamelModel):
    items: list[TerminalSessionInfo]


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
# Cost Analytics / Telemetry — canonical definitions in schemas.telemetry
# (imported via star-import at module top)
# ---------------------------------------------------------------------------


class StepPayload(CamelModel):
    """Step data for REST API and SSE."""

    step_id: str
    step_number: int
    job_id: str
    turn_id: str | None = None
    intent: str
    title: str | None = None
    status: str
    trigger: str
    tool_count: int = 0
    agent_message: str | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None
    timing_source: Literal["legacy_unrecorded", "live", "source"] = "legacy_unrecorded"
    completed_at: datetime | None = None
    files_read: list[str] | None = None
    files_written: list[str] | None = None
    start_sha: str | None = None
    end_sha: str | None = None
    artifact_count: int = 0


class StepTitlePayload(CamelModel):
    """SSE payload for step title generation."""

    step_id: str
    title: str


class StepGroupPayload(CamelModel):
    """SSE payload for step grouping updates."""

    job_id: str
    group_id: str
    headline: str
    headline_past: str
    step_ids: list[str]


class PlanStepPayload(CamelModel):
    """SSE payload for unified plan-step updates."""

    job_id: str
    plan_step_id: str
    label: str
    summary: str | None = None
    status: str
    order: int = 0
    tool_count: int = 0
    files_written: list[str] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    start_sha: str | None = None
    end_sha: str | None = None


class TurnSummaryPayload(CamelModel):
    """SSE payload for activity timeline turn summaries."""

    job_id: str
    turn_id: str
    title: str
    kind: str | None = None
    version: int | None = None
    activity_id: str
    activity_label: str
    activity_status: str = "active"  # active | done
    is_new_activity: bool = False
    plan_item_id: str | None = None
    replaces_turn_id: str | None = None  # set when merge replaces a previous step's turnId


class HunkMotivation(CamelModel):
    """Per-hunk motivation annotation."""

    edit_key: str
    title: str
    why: str


class FileMotivation(CamelModel):
    """Per-file motivation annotation."""

    title: str
    why: str
    unmatched_edits: list[HunkMotivation] = []


class StepDiffPayload(CamelModel):
    """Response for step-scoped Git diff."""

    step_id: str
    diff: str
    files_changed: int
    changed_files: list[DiffFileModel] = []
    step_context: str | None = None
    file_motivations: dict[str, FileMotivation] = {}
    hunk_motivations: dict[str, HunkMotivation] = {}


class JobMotivationsResponse(CamelModel):
    """All motivation annotations for a job, keyed by file path."""

    job_id: str
    file_motivations: dict[str, FileMotivation] = {}
    hunk_motivations: dict[str, HunkMotivation] = {}


class TranscriptSearchResult(CamelModel):
    """A transcript event matching a search query."""

    seq: int
    kind: str
    content: str
    tool_name: str | None = None
    step_id: str | None = None
    step_number: int | None = None
    timestamp: datetime


class RestoreRequest(CamelModel):
    sha: str


class StoryBlock(CamelModel):
    """A single block in a structured code-review story.

    Block types:
    - ``narrative``: prose text connecting references and beats
    - ``reference``: validated code change with file/snippet data
    - ``beat``: cognitive turning point (decision, backtrack, insight, verify)
    - ``heading``: section title for story structure
    """

    type: str  # "narrative" | "reference" | "beat" | "heading"
    # Narrative / beat fields
    text: str | None = None
    # Beat fields
    beat_kind: str | None = None  # decide | backtrack | insight | verify
    # Reference fields (coerce int→str for legacy cached payloads)
    span_id: str | None = None

    @field_validator("span_id", mode="before")
    @classmethod
    def _coerce_span_id(cls, v: object) -> str | None:
        if v is None:
            return None
        return str(v)

    step_number: int | None = None
    step_title: str | None = None
    file: str | None = None
    why: str | None = None
    turn_id: str | None = None
    edit_count: int | None = None
    snippet: str | None = None
    action: str | None = None  # "created" | "modified" | "read"


class StoryResponse(CamelModel):
    """Structured code-review story with validated change references
    and agent cognitive beats."""

    job_id: str
    blocks: list[StoryBlock] = []
    cached: bool = False
    beat_count: int = 0
    has_decisions: bool = False
    has_backtracks: bool = False
    pending: bool = False  # True when story is being generated in background


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# TestCoModification, ReviewSignals, ReviewComplexity, and JobTelemetryReport
# are now canonical in backend.models.schemas.telemetry (star-imported above).


# ---------------------------------------------------------------------------
# Trail (agent audit trail)
# ---------------------------------------------------------------------------


class TrailNodeResponse(CamelModel):
    """A single trail node in the agent audit trail."""

    id: str
    seq: int
    anchor_seq: int
    kind: str
    deterministic_kind: str | None = None
    phase: str | None = None
    timestamp: datetime
    enrichment: str
    intent: str | None = None
    rationale: str | None = None
    outcome: str | None = None
    step_id: str | None = None
    span_ids: list[int] = []
    turn_id: str | None = None
    files: list[str] = []
    start_sha: str | None = None
    end_sha: str | None = None
    supersedes: str | None = None
    tags: list[str] = []
    # Action policy fields — TraceForge governance verdict, surfaced natively
    recommended_action: str | None = None
    reason_code: str | None = None
    risk_score: int | None = None
    risk_band: str | None = None
    effect: str | None = None
    checkpoint_ref: str | None = None
    children: list[TrailNodeResponse] = []


TrailNodeResponse.model_rebuild()


class TrailResponse(CamelModel):
    """Trail endpoint response — flat or nested."""

    job_id: str
    nodes: list[TrailNodeResponse] = []
    total_nodes: int = 0
    enriched_nodes: int = 0
    complete: bool = False


class TrailKeyDecision(CamelModel):
    """A key decision from the trail summary."""

    decision: str
    rationale: str | None = None


class TrailBacktrack(CamelModel):
    """A backtrack from the trail summary."""

    original: str
    replacement: str
    reason: str | None = None


class TrailSummaryResponse(CamelModel):
    """Lightweight trail summary for job list cards / PR descriptions."""

    job_id: str
    goals: list[str] = []
    approach: str | None = None
    key_decisions: list[TrailKeyDecision] = []
    backtracks: list[TrailBacktrack] = []
    files_explored: int = 0
    files_modified: int = 0
    verifications_passed: int = 0
    verifications_failed: int = 0
    enrichment_complete: bool = False


# ---------------------------------------------------------------------------
# Notification schemas
# ---------------------------------------------------------------------------


class VapidKeyResponse(CamelModel):
    public_key: str


class SubscriptionRequest(CamelModel):
    endpoint: str
    keys: dict[str, str]


class UnsubscribeRequest(CamelModel):
    endpoint: str


# ---------------------------------------------------------------------------
# Share schemas
# ---------------------------------------------------------------------------


class ShareTokenResponse(CamelModel):
    token: str
    job_id: str
    url: str


class CreateShareRequest(CamelModel):
    job_id: str | None = None  # allow body-less POST where job_id is in path


# ---------------------------------------------------------------------------
# Sidecar template schemas
# ---------------------------------------------------------------------------


class GenerateSidecarRequest(CamelModel):
    description: str = Field(min_length=1, max_length=5000)


class GenerateSidecarResponse(CamelModel):
    definition: dict[str, Any]


class CreateSidecarTemplateRequest(CamelModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=500)
    definition_json: str = Field(min_length=2, max_length=50_000)


class UpdateSidecarTemplateRequest(CamelModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, min_length=1, max_length=500)
    definition_json: str | None = Field(None, min_length=2, max_length=50_000)
    enabled: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateSidecarTemplateRequest:
        if all(v is None for v in (self.name, self.description, self.definition_json, self.enabled)):
            raise ValueError("At least one field must be provided")
        return self


class SidecarTemplateResponse(CamelModel):
    id: str
    name: str
    description: str
    definition_json: str
    created_at: datetime
    last_used_at: datetime | None
    enabled: bool = True


class SidecarTemplateListResponse(CamelModel):
    items: list[SidecarTemplateResponse]


class CreateChatRequest(CamelModel):
    """Create a new Chat. ``project_id`` is always user-overridable at creation."""

    title: str = Field(min_length=1, max_length=200)
    project_id: str | None = None


class ChatResponse(CamelModel):
    id: str
    project_id: str | None
    title: str
    created_at: datetime
    last_message_at: datetime
    status: str
    task_link_id: str | None = None


class ChatListResponse(CamelModel):
    items: list[ChatResponse]


class AddChatMessageRequest(CamelModel):
    """Append a message to a Chat's transcript."""

    role: Literal["user"] = "user"
    content: str = Field(min_length=1, max_length=50_000)


class ChatMessageResponse(CamelModel):
    id: str
    chat_id: str
    role: str
    content: str
    created_at: datetime


class ChatTurnResponse(CamelModel):
    """One user message plus its assistant completion or explicit failure."""

    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse | None = None
    state: Literal["assistant", "error"]
    error: str | None = None


class LaunchJobFromChatRequest(CamelModel):
    """Launch a Job from a Chat, seeded from its transcript (CAP-12/AD-12, Story 5.2)."""

    repo: str = Field(min_length=1)
    base_ref: str | None = None
    branch: str | None = None
    model: str | None = None
    sdk: str | None = None


class AttachChatToChainRequest(CamelModel):
    """Attach a Chat to a Task Recipe chain via its entry TaskLink (Story 5.3)."""

    task_link_id: str = Field(min_length=1)


class ChatChainStatusResponse(CamelModel):
    """Read-only narration snapshot of a Chat's attached chain (Story 5.3)."""

    task_link_id: str
    story_node_id: str | None
    repo_path: str
    job_id: str | None
    job_state: str | None


# ---------------------------------------------------------------------------
# Utility / operational responses
# ---------------------------------------------------------------------------


class WarmSessionResponse(CamelModel):
    session_token: str


class RestoreResponse(CamelModel):
    restored: bool
    sha: str


# SidecarSession*Metrics classes are now canonical in
# backend.models.schemas.telemetry (star-imported above).
