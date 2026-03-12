"""Pydantic request/response schemas — single source of truth for the API contract."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic resolves annotations at runtime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base model that serializes field names to camelCase."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --- Enums ---


class StrategyKind(StrEnum):
    single_agent = "single_agent"


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


class SendMessageRequest(BaseModel):
    content: str


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


class JobListResponse(CamelModel):
    items: list[JobResponse]
    cursor: str | None
    has_more: bool


class SendMessageResponse(CamelModel):
    seq: int
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


class DiffUpdatePayload(CamelModel):
    job_id: str
    changed_files: list[DiffFileModel]


class SessionHeartbeatPayload(CamelModel):
    job_id: str
    session_id: str
    timestamp: datetime


class SnapshotPayload(CamelModel):
    jobs: list[JobResponse]
    pending_approvals: list[ApprovalResponse]
