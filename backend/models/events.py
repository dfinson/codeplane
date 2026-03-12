"""Canonical internal event model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DomainEventKind(str, Enum):
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
    session_heartbeat = "SessionHeartbeat"


@dataclass
class DomainEvent:
    event_id: str
    job_id: str
    timestamp: datetime
    kind: DomainEventKind
    payload: dict  # type: ignore[type-arg]
