"""Pydantic request/response schemas — single source of truth for the API contract."""

from __future__ import annotations

from datetime import UTC, datetime  # noqa: TC003 — Pydantic resolves annotations at runtime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


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


class StrategyKind(StrEnum):
    single_agent = "single_agent"


class PermissionMode(StrEnum):
    auto = "auto"
    supervised = "supervised"
    readonly = "readonly"


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    waiting_for_approval = "waiting_for_approval"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class ApprovalResolution(StrEnum):
    approved = "approved"
    rejected = "rejected"


class ArtifactType(StrEnum):
    diff_snapshot = "diff_snapshot"
    agent_summary = "agent_summary"
    custom = "custom"


class ExecutionPhase(StrEnum):
    environment_setup = "environment_setup"
    agent_reasoning = "agent_reasoning"
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
    operator = "operator"
    tool_call = "tool_call"
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


class CreateJobRequest(BaseModel):
    repo: str
    prompt: str
    base_ref: str | None = None
    branch: str | None = None
    strategy: StrategyKind | None = None
    permission_mode: PermissionMode | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)


class ResumeJobRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=50_000)


class ContinueJobRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=10_000)


class ResolveApprovalRequest(BaseModel):
    resolution: ApprovalResolution


class UpdateGlobalConfigRequest(BaseModel):
    config_yaml: str


class RegisterRepoRequest(BaseModel):
    source: str


# --- Response Models ---


class CreateJobResponse(CamelModel):
    id: str
    state: str
    branch: str | None = None
    worktree_path: str | None = None
    created_at: datetime


class JobResponse(CamelModel):
    id: str
    repo: str
    prompt: str
    state: str
    strategy: StrategyKind
    base_ref: str
    worktree_path: str | None
    branch: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    pr_url: str | None = None
    merge_status: str | None = None


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


class GlobalConfigResponse(BaseModel):
    config_yaml: str


class TranscribeResponse(BaseModel):
    text: str


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
    active_job_count: int = 0


# --- SSE Payload Models ---


class LogLinePayload(CamelModel):
    job_id: str
    seq: int
    timestamp: datetime
    level: LogLevel
    message: str
    context: dict | None = None  # type: ignore[type-arg]


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
    previous_state: str | None
    new_state: str
    timestamp: datetime


class ApprovalRequestedPayload(CamelModel):
    job_id: str
    approval_id: str
    description: str
    proposed_action: str | None = None
    timestamp: datetime


class ApprovalResolvedPayload(CamelModel):
    job_id: str
    approval_id: str
    resolution: str
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
    timestamp: datetime


class SnapshotPayload(CamelModel):
    jobs: list[JobResponse]
    pending_approvals: list[ApprovalResponse]
