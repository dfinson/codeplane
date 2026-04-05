"""Canonical internal event model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from datetime import datetime


class DomainEventKind(StrEnum):
    job_created = "JobCreated"
    workspace_prepared = "WorkspacePrepared"
    agent_session_started = "AgentSessionStarted"
    log_line_emitted = "LogLineEmitted"
    transcript_updated = "TranscriptUpdated"
    diff_updated = "DiffUpdated"
    approval_requested = "ApprovalRequested"
    approval_resolved = "ApprovalResolved"
    job_review = "JobReview"
    job_completed = "JobCompleted"
    job_failed = "JobFailed"
    job_canceled = "JobCanceled"
    job_state_changed = "JobStateChanged"
    session_heartbeat = "SessionHeartbeat"
    merge_completed = "MergeCompleted"
    merge_conflict = "MergeConflict"
    session_resumed = "SessionResumed"
    job_resolved = "JobResolved"
    job_archived = "JobArchived"
    job_title_updated = "JobTitleUpdated"
    progress_headline = "ProgressHeadline"
    model_downgraded = "ModelDowngraded"
    tool_group_summary = "ToolGroupSummary"
    agent_plan_updated = "AgentPlanUpdated"
    execution_phase_changed = "ExecutionPhaseChanged"
    telemetry_updated = "TelemetryUpdated"
    step_started = "StepStarted"
    step_completed = "StepCompleted"
    step_title_generated = "StepTitleGenerated"
    step_group_updated = "StepGroupUpdated"


# ---------------------------------------------------------------------------
# Typed event payloads
#
# These TypedDicts describe the *most common* payload shapes emitted by the
# service layer and consumed by SSE builders / API endpoints.  They are not
# enforced at publish time (payloads are still plain dicts internally) but
# give type-checkers enough information to validate consumer code.
# ---------------------------------------------------------------------------


class LogLinePayloadDict(TypedDict, total=False):
    seq: int
    timestamp: str
    level: str
    message: str
    context: dict[str, Any] | None


class TranscriptPayloadDict(TypedDict, total=False):
    seq: int
    timestamp: str
    role: str
    content: str
    title: str | None
    turn_id: str | None
    tool_name: str | None
    tool_args: str | None
    tool_result: str | None
    tool_success: bool | None
    tool_issue: str | None
    tool_intent: str | None
    tool_title: str | None
    tool_display: str | None
    tool_duration_ms: int | None
    step_id: str | None
    step_number: int | None


class DiffPayloadDict(TypedDict, total=False):
    changed_files: list[dict[str, Any]]


class ApprovalRequestedPayloadDict(TypedDict, total=False):
    approval_id: str
    description: str
    proposed_action: str | None
    timestamp: str


class ApprovalResolvedPayloadDict(TypedDict, total=False):
    approval_id: str
    resolution: str
    timestamp: str


class JobStatePayloadDict(TypedDict, total=False):
    state: str
    new_state: str
    previous_state: str | None


class JobReviewPayloadDict(TypedDict, total=False):
    pr_url: str | None
    merge_status: str | None  # Git merge operation outcome
    resolution: str | None  # Overall job disposition
    model_downgraded: bool
    requested_model: str | None
    actual_model: str | None


class JobCompletedPayloadDict(TypedDict, total=False):
    resolution: str | None
    merge_status: str | None
    pr_url: str | None


class JobFailedPayloadDict(TypedDict, total=False):
    reason: str


class SessionHeartbeatPayloadDict(TypedDict, total=False):
    session_id: str
    timestamp: str


class MergeCompletedPayloadDict(TypedDict, total=False):
    branch: str
    base_ref: str
    strategy: str
    timestamp: str


class MergeConflictPayloadDict(TypedDict, total=False):
    branch: str
    base_ref: str
    conflict_files: list[str]
    fallback: str
    pr_url: str | None
    timestamp: str


class SessionResumedPayloadDict(TypedDict, total=False):
    session_number: int
    timestamp: str


class JobResolvedPayloadDict(TypedDict, total=False):
    resolution: str  # Overall job disposition (not the git merge outcome)
    pr_url: str | None
    conflict_files: list[str] | None
    error: str | None


class JobTitleUpdatedPayloadDict(TypedDict, total=False):
    title: str | None
    branch: str | None


class ProgressHeadlinePayloadDict(TypedDict, total=False):
    headline: str
    headline_past: str
    summary: str
    replaces_count: int


class ModelDowngradedPayloadDict(TypedDict, total=False):
    requested_model: str
    actual_model: str


class ToolGroupSummaryPayloadDict(TypedDict, total=False):
    turn_id: str
    summary: str


class AgentPlanStepDict(TypedDict, total=False):
    label: str
    status: str


class AgentPlanUpdatedPayloadDict(TypedDict, total=False):
    steps: list[AgentPlanStepDict]


class ExecutionPhasePayloadDict(TypedDict, total=False):
    phase: str


class TelemetryUpdatedPayloadDict(TypedDict, total=False):
    job_id: str


class StepStartedPayloadDict(TypedDict, total=False):
    step_id: str
    step_number: int
    turn_id: str | None
    intent: str
    trigger: str


class StepCompletedPayloadDict(TypedDict, total=False):
    step_id: str
    status: str
    tool_count: int
    duration_ms: int
    has_summary: bool
    agent_message: str | None
    files_read: list[str]
    files_written: list[str]
    start_sha: str | None
    end_sha: str | None


class StepTitlePayloadDict(TypedDict, total=False):
    step_id: str
    title: str


# Union of all known payload shapes.  Used as the DomainEvent.payload type so
# consumers get useful type information.  The ``dict[str, Any]`` fallback
# covers event kinds whose payload is not yet formally typed.
EventPayload = (
    LogLinePayloadDict
    | TranscriptPayloadDict
    | DiffPayloadDict
    | ApprovalRequestedPayloadDict
    | ApprovalResolvedPayloadDict
    | JobStatePayloadDict
    | JobReviewPayloadDict
    | JobCompletedPayloadDict
    | JobFailedPayloadDict
    | SessionHeartbeatPayloadDict
    | MergeCompletedPayloadDict
    | MergeConflictPayloadDict
    | SessionResumedPayloadDict
    | JobResolvedPayloadDict
    | JobTitleUpdatedPayloadDict
    | ProgressHeadlinePayloadDict
    | ModelDowngradedPayloadDict
    | ToolGroupSummaryPayloadDict
    | AgentPlanUpdatedPayloadDict
    | ExecutionPhasePayloadDict
    | TelemetryUpdatedPayloadDict
    | StepStartedPayloadDict
    | StepCompletedPayloadDict
    | StepTitlePayloadDict
    | dict[str, Any]
)


@dataclass
class DomainEvent:
    event_id: str
    job_id: str
    timestamp: datetime
    kind: DomainEventKind
    payload: dict[str, Any]
    db_id: int | None = None  # autoincrement ID from EventRow; set after persistence

    @staticmethod
    def make_event_id() -> str:
        """Generate a unique event ID."""
        return f"evt-{uuid.uuid4().hex[:12]}"
