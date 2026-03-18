"""Canonical internal event model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

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
    job_succeeded = "JobSucceeded"
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


@dataclass
class DomainEvent:
    event_id: str
    job_id: str
    timestamp: datetime
    kind: DomainEventKind
    payload: dict  # type: ignore[type-arg]
    db_id: int | None = None  # autoincrement ID from EventRow; set after persistence
