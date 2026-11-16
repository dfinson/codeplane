"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLAlchemy needs this at runtime for annotation eval

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# All DateTime columns use timezone=True so timestamps are stored
# and retrieved as timezone-aware UTC values, never naive.
TZDateTime = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class RecipeDefinitionRow(Base):
    __tablename__ = "recipe_definitions"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    latest_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (CheckConstraint("version >= 1 AND latest_revision >= 0", name="ck_recipe_definition_version"),)


class RecipeRevisionRow(Base):
    __tablename__ = "recipe_revisions"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    definition_id: Mapped[str] = mapped_column(String, ForeignKey("recipe_definitions.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False)
    blocks_json: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("definition_id", "revision", name="uq_recipe_revision_number"),
        CheckConstraint("revision >= 1", name="ck_recipe_revision_number"),
    )


class RecipeRunRow(Base):
    __tablename__ = "recipe_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    start_request_key: Mapped[str] = mapped_column(String, nullable=False)
    continuation_of_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("recipe_runs.id", name="fk_recipe_run_continuation")
    )
    instruction: Mapped[str | None] = mapped_column(Text)
    code_ref_json: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    revision_id: Mapped[str] = mapped_column(String, ForeignKey("recipe_revisions.id"), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    inputs_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    __table_args__ = (
        UniqueConstraint("job_id", "run_number", name="uq_recipe_job_run_number"),
        UniqueConstraint("start_request_key", name="uq_recipe_runs_start_request_key"),
        CheckConstraint("run_number >= 1", name="ck_recipe_run_number"),
        Index(
            "uq_recipe_job_unresolved_run", "job_id", unique=True,
            sqlite_where=text("resolved_at IS NULL"),
        ),
        CheckConstraint(
            "state IN ('blocked','ready','running','needs_decision','succeeded',"
            "'failed','skipped','canceled','interrupted')",
            name="ck_recipe_run_state",
        ),
    )


class RecipeStepInstanceRow(Base):
    __tablename__ = "recipe_step_instances"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("recipe_runs.id", ondelete="CASCADE"), nullable=False)
    step_path: Mapped[str] = mapped_column(String, nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    current_attempt_id: Mapped[str | None] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "step_path", name="uq_recipe_step_path"),
        CheckConstraint(
            "state IN ('blocked','ready','running','needs_decision','succeeded',"
            "'failed','skipped','canceled','interrupted')",
            name="ck_recipe_step_state",
        ),
    )


class RecipeAttemptRow(Base):
    """Dispatch identity and exact inputs are never overwritten by retry."""

    __tablename__ = "recipe_attempts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    step_instance_id: Mapped[str] = mapped_column(
        String, ForeignKey("recipe_step_instances.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    inputs_json: Mapped[str] = mapped_column(Text, nullable=False)
    runner_json: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_json: Mapped[str] = mapped_column(Text, nullable=False)
    dispatch_intent_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("step_instance_id", "number", name="uq_recipe_attempt_number"),
        CheckConstraint("number >= 1", name="ck_recipe_attempt_number"),
    )


class RecipeAttemptResultRow(Base):
    __tablename__ = "recipe_attempt_results"

    attempt_id: Mapped[str] = mapped_column(
        String, ForeignKey("recipe_attempts.id", ondelete="CASCADE"), primary_key=True
    )
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class RecipeAttemptExecutionRow(Base):
    """One attempt may retain several real agent executions, never fictional ones."""

    __tablename__ = "recipe_attempt_executions"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str] = mapped_column(
        String, ForeignKey("recipe_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_token: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class RecipeOutputReceiptRow(Base):
    __tablename__ = "recipe_output_receipts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        String, ForeignKey("recipe_attempts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (UniqueConstraint("attempt_id", "name", name="uq_recipe_attempt_output"),)


class RecipeGateBindingRow(Base):
    __tablename__ = "recipe_gate_bindings"

    attempt_id: Mapped[str] = mapped_column(
        String, ForeignKey("recipe_attempts.id", ondelete="CASCADE"), primary_key=True
    )
    approval_id: Mapped[str] = mapped_column(String, ForeignKey("approvals.id"), nullable=False, unique=True)
    input_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_action: Mapped[str] = mapped_column(Text, nullable=False)
    next_steps_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class RecipeWorkspaceLeaseRow(Base):
    __tablename__ = "recipe_workspace_leases"

    workspace: Mapped[str] = mapped_column(String, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        String, ForeignKey("recipe_attempts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    acquired_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class RecipeReviewRow(Base):
    __tablename__ = "recipe_reviews"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("recipe_runs.id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    code_ref_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (UniqueConstraint("run_id", "idempotency_key", name="uq_recipe_review_request"),)


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo: Mapped[str | None] = mapped_column(String, nullable=True)
    # A Job never exists without an owning Project — the atomic unit is a
    # Project (AD-5); repos and jobs do not exist independently of one.
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    base_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    branch: Mapped[str | None] = mapped_column(String, nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String, nullable=True)
    merge_status: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    worktree_name: Mapped[str | None] = mapped_column(String, nullable=True)
    permission_mode: Mapped[str] = mapped_column(String, nullable=False, default="full_auto")
    preset: Mapped[str] = mapped_column(String, nullable=False, server_default="supervised")
    session_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sdk_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    sdk: Mapped[str] = mapped_column(String, nullable=False, default="copilot")
    verify: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    self_review: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    max_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verify_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    self_review_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    enable_stall_detection: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    enable_plan_tracking: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    parent_job_id: Mapped[str | None] = mapped_column(String, ForeignKey("jobs.id"), nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="managed", server_default="managed")
    external_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    integration_binding_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("integration_bindings.id", name="fk_jobs_integration_binding"), nullable=True
    )
    observation_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    tail_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ingest_checkpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="standard", server_default="standard")
    story_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    trail_state_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    artifact_collection_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )
    artifact_collection_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_collection_session_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    artifact_collection_updated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("observation_only = 0 OR source = 'integration'", name="ck_jobs_observation_source"),
        CheckConstraint(
            "(source = 'integration' AND observation_only = 1) OR (repo IS NOT NULL AND base_ref IS NOT NULL)",
            name="ck_jobs_workspace_identity",
        ),
    )


class IntegrationBindingRow(Base):
    __tablename__ = "integration_bindings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    adapter_id: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    credential_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (
        CheckConstraint("mode IN ('observation','execution')", name="ck_integration_binding_mode"),
        CheckConstraint("version >= 1", name="ck_integration_binding_version"),
        Index("ix_integration_bindings_project", "project_id"),
        Index("ix_integration_bindings_job", "job_id"),
    )


class IntegrationSourceCheckpointRow(Base):
    __tablename__ = "integration_source_checkpoints"

    binding_id: Mapped[str] = mapped_column(String, ForeignKey("integration_bindings.id"), primary_key=True)
    source_key: Mapped[str] = mapped_column(String, primary_key=True)
    parser_revision: Mapped[str] = mapped_column(String, primary_key=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    prefix_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    buffer_json: Mapped[str] = mapped_column(Text, nullable=False)
    legacy_checkpoint_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (
        CheckConstraint("record_count >= 0 AND version >= 1", name="ck_integration_checkpoint_progress"),
        CheckConstraint("state IN ('accepted','reconciliation_required')", name="ck_integration_checkpoint_state"),
    )


class IntegrationIdentityRow(Base):
    __tablename__ = "integration_identities"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), nullable=False)
    adapter_id: Mapped[str] = mapped_column(String, nullable=False)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pinned_key_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    endpoint: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class ProjectRow(Base):
    """A Project — the sole persistence entity for repo membership (AD-5).

    ``repo_paths`` is stored as a JSON-encoded list of strings rather than a
    separate join table, matching the additive/thin scope of Story 2.1. A
    single-repo Project is not a special case — it is a row with one entry.
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    repo_paths: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")  # JSON list
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    # Serialized traceforge EventMetadata (turn/tool_display/motivation/duration_ms/
    # visibility/…). Persisted so DB-replay consumers (REST transcript, snapshot,
    # search) reconstruct the full TF event, since enrichment lives on metadata.
    event_metadata: Mapped[str] = mapped_column(Text, nullable=False, server_default="{}")  # JSON

    __table_args__ = (Index("idx_events_job_id", "job_id"),)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)
    # Hard-blocked operations (e.g. git reset --hard) set this to True so that
    # blanket trust grants cannot auto-resolve them.
    requires_explicit_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    # Operator notes attached when resolving (e.g. rejection feedback for plan mode)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Action policy metadata (populated by action classifier)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(12), nullable=True)
    reversible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contained: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    checkpoint_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_path: Mapped[str] = mapped_column(String, nullable=False)
    phase: Mapped[str] = mapped_column(String, nullable=False)
    step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class DiffSnapshotRow(Base):
    __tablename__ = "diff_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    diff_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON

    __table_args__ = (Index("idx_diff_snapshots_job_id", "job_id"),)


class StepRow(Base):
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    intent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    title: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    trigger: Mapped[str] = mapped_column(String(30), nullable=False)
    tool_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    end_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    files_read: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    files_written: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    # Transcript context at step close (migration 0020)
    preceding_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array

    __table_args__ = (Index("ix_steps_job_number", "job_id", "step_number"),)


class JobTelemetrySummaryRow(Base):
    """Denormalized per-job telemetry — upserted on every telemetry event.

    The composite PK ``(job_id, session_kind)`` allows a single job to have
    multiple telemetry rows — one per session kind (job, preflight, etc.).
    """

    __tablename__ = "job_telemetry_summary"

    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), primary_key=True)
    session_kind: Mapped[str] = mapped_column(
        String, nullable=False, default="job", server_default="job", primary_key=True
    )
    sdk: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False, default="")
    repo: Mapped[str] = mapped_column(String, nullable=False, default="")
    branch: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    premium_requests: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    llm_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_llm_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tool_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    compactions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_compacted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_wait_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operator_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_window_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_context_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    # Cost analytics columns (migration 0009)
    total_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    file_read_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    file_write_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unique_files_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    file_reread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    peak_turn_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    avg_turn_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    cost_first_half_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    cost_second_half_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    diff_lines_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    diff_lines_removed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    agent_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    subagent_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")


class JobTelemetrySpanRow(Base):
    """Individual LLM or tool call — append-only."""

    __tablename__ = "job_telemetry_spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    span_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)  # float stored as text
    duration_ms: Mapped[str] = mapped_column(Text, nullable=False)
    attrs_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    # Cost analytics columns (migration 0008)
    tool_category: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_target: Mapped[str | None] = mapped_column(String, nullable=True)
    turn_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_phase: Mapped[str | None] = mapped_column(String, nullable=True)
    is_retry: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    retries_span_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_args_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    # Transcript linkage (migration 0018)
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Motivation tracking (migration 0019)
    preceding_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-edit motivations with edit keys (migration 0021)
    edit_motivations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    session_kind: Mapped[str] = mapped_column(String, nullable=False, default="job", server_default="job")

    __table_args__ = (
        Index("idx_spans_job", "job_id"),
        Index("idx_spans_category", "tool_category"),
        Index("idx_spans_turn", "job_id", "turn_number"),
        Index("idx_spans_phase", "execution_phase"),
        Index("idx_spans_turn_id", "job_id", "turn_id"),
    )


class JobFileCostRow(Base):
    """Per-job file cost attribution (Item 14)."""

    __tablename__ = "job_file_cost"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    read_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    write_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (
        Index("idx_file_cost_job", "job_id"),
        Index("idx_file_cost_path", "file_path"),
    )


class JobFileAccessRow(Base):
    """Per-file read/write access log for cost analytics."""

    __tablename__ = "job_file_access_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    access_type: Mapped[str] = mapped_column(String, nullable=False)  # read / write
    turn_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (
        Index("idx_file_access_job", "job_id"),
        Index("idx_file_access_path", "file_path"),
    )


class CostAttributionRow(Base):
    """Per-job cost breakdown by dimension (phase, tool category, turn)."""

    __tablename__ = "job_cost_attribution"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    dimension: Mapped[str] = mapped_column(String, nullable=False)
    bucket: Mapped[str] = mapped_column(String, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (
        Index("idx_attr_job", "job_id"),
        Index("idx_attr_dimension", "dimension", "bucket"),
        Index("ix_cost_attribution_model", "model"),
    )


class LatencyAttributionRow(Base):
    """Per-job latency breakdown by dimension (category, activity, phase, turn)."""

    __tablename__ = "job_latency_attribution"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    dimension: Mapped[str] = mapped_column(String, nullable=False)
    bucket: Mapped[str] = mapped_column(String, nullable=False)
    wall_clock_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sum_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    span_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    p50_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    p95_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pct_of_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (
        Index("idx_latency_attr_job", "job_id"),
        Index("idx_latency_attr_dimension", "dimension", "bucket"),
    )


class CostObservationRow(Base):
    """Cross-job cost observation or anomaly."""

    __tablename__ = "cost_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    job_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_waste_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    __table_args__ = (
        Index("idx_obs_category", "category"),
        Index("idx_obs_severity", "severity"),
    )


class TrailNodeRow(Base):
    """Agent audit trail node — structured intent graph."""

    __tablename__ = "trail_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    anchor_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    deterministic_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phase: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    enrichment: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    # Intent (populated by enrichment, nullable)
    intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Anchors (populated deterministically)
    step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    span_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    files: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    start_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    end_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Transcript context (pass-through from step_completed)
    preceding_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON snapshot
    agent_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_names: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    tool_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diff_additions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diff_deletions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Plan/activity (populated by enrichment or native plan)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plan_item_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_item_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    activity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    activity_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Action policy classification (TraceForge governance verdict — surfaced
    # natively; the retired CodePlane tier / reversible / contained fields are gone)
    recommended_action: Mapped[str | None] = mapped_column(String(12), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_band: Mapped[str | None] = mapped_column(String(12), nullable=True)
    effect: Mapped[str | None] = mapped_column(String(12), nullable=True)
    checkpoint_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rollback_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Edges
    supersedes: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    # Write sub-node columns (kind="write", children of modify nodes)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_retry: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    write_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    edit_motivations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    tool_display: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Purpose classification (populated by enrichment or backfill)
    purpose: Mapped[str | None] = mapped_column(String(20), nullable=True)
    purpose_source: Mapped[str | None] = mapped_column(String(10), nullable=True)

    __table_args__ = (
        Index("ix_trail_nodes_job_id", "job_id"),
        Index("ix_trail_nodes_job_seq", "job_id", "seq"),
        Index("ix_trail_nodes_display_order", "job_id", "anchor_seq", "seq"),
        Index("ix_trail_nodes_parent", "parent_id"),
        Index("ix_trail_nodes_kind", "job_id", "kind"),
        Index("ix_trail_nodes_enrichment", "job_id", "enrichment"),
    )


# ---------------------------------------------------------------------------
# Action Policy tables
# ---------------------------------------------------------------------------


class PolicyConfigRow(Base):
    __tablename__ = "policy_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    preset: Mapped[str] = mapped_column(String(20), nullable=False, server_default="supervised")
    batch_window_seconds: Mapped[float] = mapped_column(Float, nullable=False, server_default="5.0")
    # Per-preset USD ceiling overrides (JSON: {preset: {warn_usd, ceiling_usd}}).
    # NULL/absent → the governance profile's baked default ceiling applies. The
    # ceiling is enforced natively by JobSpendCeilingAssessor, not by CP tiers.
    usd_ceilings_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class MCPServerConfigRow(Base):
    __tablename__ = "mcp_server_configs"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    args_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    env_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    contained: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    reversible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    tool_overrides_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# Tracker integration tables (CAP-6/CAP-7, AD-6)
# ---------------------------------------------------------------------------


class CredentialRow(Base):
    """A global, Project-independent integration credential (Story 3.1, AD-6).

    Registered once in Settings > Integrations and referenced by any number of
    ``TrackerLinkRow``s. ``encrypted_secret`` holds the PAT encrypted at rest
    via ``backend.services.credentials.encryption`` — never plaintext, never
    returned by any API response, never logged.
    """

    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class TrackerLinkRow(Base):
    """Many-to-many join between a Project and a Credential (AD-6).

    Only the referential-integrity anchor needed for Story 3.1's
    delete-blocked-while-referenced rule (AC2) exists here. ``project_id`` is
    a plain string, deliberately not a foreign key to a ``ProjectRow`` — that
    entity does not exist yet, and Credential registration (this story) must
    not depend on Project existing. Attach/create endpoints for this table
    are out of scope for this story (Story 3.2).
    """

    __tablename__ = "tracker_links"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    credential_id: Mapped[str] = mapped_column(String, ForeignKey("credentials.id"), nullable=False, index=True)
    external_ref: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class TaskLinkRow(Base):
    """Project-scoped correlation for saved task nodes and tracker assignments.

    Historical source-backed nodes retain their story identifiers and dependency
    graph. New manual assignments identify an existing tracker ticket and carry
    a prompt override, with story_node_id left null. Source identifiers are
    unique within a repository, so persisted dependency keys include both the
    repository path and identifier. Retirement of source discovery does not
    rewrite these identities, chain roots, or historical job links.
    """

    __tablename__ = "task_links"
    __table_args__ = (
        Index(
            "ix_task_links_project_repo_node",
            "project_id",
            "repo_path",
            "story_node_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), nullable=False, index=True)
    repo_path: Mapped[str] = mapped_column(String, nullable=False)
    story_node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    depends_on: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")  # JSON list
    chain_root_id: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
    state: Mapped[str] = mapped_column(String, nullable=False, default="waiting", server_default="waiting")
    job_id: Mapped[str | None] = mapped_column(String, ForeignKey("jobs.id"), nullable=True)
    tracker_link_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("tracker_links.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tracker_ticket_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    epic_id: Mapped[str | None] = mapped_column(String, nullable=True)
    output_routes: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class TrackerOperationRow(Base):
    """One explicit, at-most-once dispatch attempt with retained delivery evidence."""

    __tablename__ = "tracker_operations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('awaiting_approval','approved','dispatching','applied',"
            "'rejected','failed','uncertain','not_applied')",
            name="ck_tracker_operations_state",
        ),
        CheckConstraint("action IN ('comment','transition')", name="ck_tracker_operations_action"),
        CheckConstraint(
            "target_kind IN ('comment','github_issue_state','jira_workflow_transition','azure_devops_work_item_state')",
            name="ck_tracker_operations_target_kind",
        ),
        CheckConstraint("version >= 0", name="ck_tracker_operations_version"),
        Index("ix_tracker_operations_project_created", "project_ref", "created_at"),
        Index("ix_tracker_operations_job_created", "job_ref", "created_at"),
        Index("ix_tracker_operations_state_claim", "state", "claim_expires_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    project_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_link_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("task_links.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_ref: Mapped[str] = mapped_column(String, nullable=False)
    job_ref: Mapped[str] = mapped_column(String, nullable=False)
    task_link_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    tracker_link_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("tracker_links.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tracker_link_ref: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    external_ref: Mapped[str] = mapped_column(String, nullable=False)
    credential_id_ref: Mapped[str] = mapped_column(String, nullable=False)
    ticket_ref: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_kind: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True, unique=True, index=True
    )
    approval_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    approval_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    claim_token: Mapped[str | None] = mapped_column(String, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_of_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("tracker_operations.id"), nullable=True, index=True
    )
    provider_receipt: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconciliation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class TrackerSummaryRow(Base):
    """Latest normalized external ticket state for one TrackerLink (AD-7)."""

    __tablename__ = "tracker_summaries"

    tracker_link_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("tracker_links.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tickets_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    last_synced_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SidecarTemplateRow(Base):
    __tablename__ = "sidecar_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")


# ---------------------------------------------------------------------------
# Secondary Sessions (preflight, sidecars, monitors)
# ---------------------------------------------------------------------------


class SecondarySessionRow(Base):
    __tablename__ = "secondary_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # preflight, sidecar, monitor
    name: Mapped[str] = mapped_column(String, nullable=False)
    icon: Mapped[str] = mapped_column(String, nullable=False, server_default="bot")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="running")
    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, server_default="{}")

    __table_args__ = (Index("ix_secondary_sessions_job_id", "job_id"),)


class ChatRow(Base):
    """A persistent, purely conversational chat with no git footprint.

    ``project_id`` is intentionally nullable: a Chat may be started from
    global navigation with no Project context, and is settled later when
    a Job is launched from it or it is attached to a Task Recipe chain
    (see Stories 5.2/5.3). No column here or in ``ChatRepository``/
    ``ChatService`` may ever reference ``GitService`` (AD-12, NFR8).
    """

    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="open")
    task_link_id: Mapped[str | None] = mapped_column(String, ForeignKey("task_links.id"), nullable=True)

    __table_args__ = (
        Index("ix_chats_project_id", "project_id"),
        Index("ix_chats_task_link_id", "task_link_id"),
    )


class ChatMessageRow(Base):
    """A single append-only message in a Chat's transcript (Story 5.2)."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(36), ForeignKey("chats.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (Index("ix_chat_messages_chat_id", "chat_id"),)


class SecondarySessionEntryRow(Base):
    __tablename__ = "secondary_session_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("secondary_sessions.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # reasoning, tool_call, output, error
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tool_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_args: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_display: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_display_full: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tool_issue: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_visibility: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (Index("ix_ss_entries_session", "session_id"),)


class PushSubscriptionRow(Base):
    """Persisted Web Push subscription endpoint."""

    __tablename__ = "push_subscriptions"

    endpoint: Mapped[str] = mapped_column(String, primary_key=True)
    p256dh: Mapped[str] = mapped_column(String, nullable=False)
    auth_key: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
