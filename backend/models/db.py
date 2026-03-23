"""SQLAlchemy ORM models."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase

from backend.models.domain import PermissionMode

# All DateTime columns use timezone=True so timestamps are stored
# and retrieved as timezone-aware UTC values, never naive.
TZDateTime = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    repo = Column(String, nullable=False)
    prompt = Column(Text, nullable=False)
    state = Column(String, nullable=False)
    base_ref = Column(String, nullable=False)
    branch = Column(String, nullable=True)
    worktree_path = Column(String, nullable=True)
    session_id = Column(String, nullable=True)
    pr_url = Column(String, nullable=True)
    merge_status = Column(String, nullable=True)
    resolution = Column(String, nullable=True)
    archived_at = Column(TZDateTime, nullable=True)
    title = Column(String, nullable=True)
    worktree_name = Column(String, nullable=True)
    permission_mode = Column(String, nullable=False, default=PermissionMode.auto)
    session_count = Column(Integer, nullable=False, default=1)
    sdk_session_id = Column(String, nullable=True)
    model = Column(String, nullable=True)
    failure_reason = Column(String, nullable=True)
    sdk = Column(String, nullable=False, default="copilot")
    verify = Column(Boolean, nullable=True)
    self_review = Column(Boolean, nullable=True)
    max_turns = Column(Integer, nullable=True)
    verify_prompt = Column(Text, nullable=True)
    self_review_prompt = Column(Text, nullable=True)
    created_at = Column(TZDateTime, nullable=False)
    updated_at = Column(TZDateTime, nullable=False)
    completed_at = Column(TZDateTime, nullable=True)
    version = Column(Integer, nullable=False, default=1, server_default="1")


class EventRow(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, nullable=False, unique=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    kind = Column(String, nullable=False)
    timestamp = Column(TZDateTime, nullable=False)
    payload = Column(Text, nullable=False)  # JSON

    __table_args__ = (Index("idx_events_job_id", "job_id"),)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    description = Column(Text, nullable=False)
    proposed_action = Column(Text, nullable=True)
    requested_at = Column(TZDateTime, nullable=False)
    resolved_at = Column(TZDateTime, nullable=True)
    resolution = Column(String, nullable=True)
    # Hard-blocked operations (e.g. git reset --hard) set this to True so that
    # blanket trust grants cannot auto-resolve them.
    requires_explicit_approval = Column(Boolean, nullable=False, server_default="0")


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    disk_path = Column(String, nullable=False)
    phase = Column(String, nullable=False)
    created_at = Column(TZDateTime, nullable=False)


class DiffSnapshotRow(Base):
    __tablename__ = "diff_snapshots"

    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    snapshot_at = Column(TZDateTime, nullable=False)
    diff_json = Column(Text, nullable=False)  # JSON

    __table_args__ = (Index("idx_diff_snapshots_job_id", "job_id"),)


class JobMetricsRow(Base):
    """Persisted telemetry snapshot for a job.

    Written at the end of every session so that cumulative metrics survive
    daemon restarts and are available when a job is resumed.  Only one row
    per job — upserted on each session end.
    """

    __tablename__ = "job_metrics"

    job_id = Column(String, ForeignKey("jobs.id"), primary_key=True)
    snapshot_json = Column(Text, nullable=False)  # JSON — see JobTelemetry.to_snapshot()
    updated_at = Column(TZDateTime, nullable=False)
