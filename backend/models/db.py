"""SQLAlchemy ORM models."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
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


class JobTelemetrySummaryRow(Base):
    """Denormalized per-job telemetry — upserted on every telemetry event."""

    __tablename__ = "job_telemetry_summary"

    job_id = Column(String, ForeignKey("jobs.id"), primary_key=True)
    sdk = Column(String, nullable=False)
    model = Column(String, nullable=False, default="")
    repo = Column(String, nullable=False, default="")
    branch = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="running")
    created_at = Column(TZDateTime, nullable=False)
    completed_at = Column(TZDateTime, nullable=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cache_read_tokens = Column(Integer, nullable=False, default=0)
    cache_write_tokens = Column(Integer, nullable=False, default=0)
    total_cost_usd = Column(Float, nullable=False, default=0.0)
    premium_requests = Column(Float, nullable=False, default=0.0)
    llm_call_count = Column(Integer, nullable=False, default=0)
    total_llm_duration_ms = Column(Integer, nullable=False, default=0)
    tool_call_count = Column(Integer, nullable=False, default=0)
    tool_failure_count = Column(Integer, nullable=False, default=0)
    total_tool_duration_ms = Column(Integer, nullable=False, default=0)
    compactions = Column(Integer, nullable=False, default=0)
    tokens_compacted = Column(Integer, nullable=False, default=0)
    approval_count = Column(Integer, nullable=False, default=0)
    approval_wait_ms = Column(Integer, nullable=False, default=0)
    agent_messages = Column(Integer, nullable=False, default=0)
    operator_messages = Column(Integer, nullable=False, default=0)
    context_window_size = Column(Integer, nullable=False, default=0)
    current_context_tokens = Column(Integer, nullable=False, default=0)
    quota_json = Column(Text, nullable=True)
    updated_at = Column(TZDateTime, nullable=False)
    # Cost analytics columns (migration 0009)
    total_turns = Column(Integer, nullable=False, default=0, server_default="0")
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    retry_cost_usd = Column(Float, nullable=False, default=0.0, server_default="0.0")
    file_read_count = Column(Integer, nullable=False, default=0, server_default="0")
    file_write_count = Column(Integer, nullable=False, default=0, server_default="0")
    unique_files_read = Column(Integer, nullable=False, default=0, server_default="0")
    file_reread_count = Column(Integer, nullable=False, default=0, server_default="0")
    peak_turn_cost_usd = Column(Float, nullable=False, default=0.0, server_default="0.0")
    avg_turn_cost_usd = Column(Float, nullable=False, default=0.0, server_default="0.0")
    cost_first_half_usd = Column(Float, nullable=False, default=0.0, server_default="0.0")
    cost_second_half_usd = Column(Float, nullable=False, default=0.0, server_default="0.0")
    diff_lines_added = Column(Integer, nullable=False, default=0, server_default="0")
    diff_lines_removed = Column(Integer, nullable=False, default=0, server_default="0")


class JobTelemetrySpanRow(Base):
    """Individual LLM or tool call — append-only."""

    __tablename__ = "job_telemetry_spans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    span_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    started_at = Column(Text, nullable=False)  # float stored as text
    duration_ms = Column(Text, nullable=False)
    attrs_json = Column(Text, nullable=False)
    created_at = Column(TZDateTime, nullable=False)
    # Cost analytics columns (migration 0008)
    tool_category = Column(String, nullable=True)
    tool_target = Column(String, nullable=True)
    turn_number = Column(Integer, nullable=True)
    execution_phase = Column(String, nullable=True)
    is_retry = Column(Boolean, nullable=True, default=False)
    retries_span_id = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cache_read_tokens = Column(Integer, nullable=True)
    cache_write_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    tool_args_json = Column(Text, nullable=True)
    result_size_bytes = Column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_spans_job", "job_id"),
        Index("idx_spans_category", "tool_category"),
        Index("idx_spans_turn", "job_id", "turn_number"),
        Index("idx_spans_phase", "execution_phase"),
    )
