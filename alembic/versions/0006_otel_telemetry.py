"""Replace job_metrics with OTEL telemetry tables.

Drop the old ``job_metrics`` table (JSON blob snapshots) and create two new
tables:

- ``job_telemetry_summary`` — denormalized one-row-per-job, upserted on every
  telemetry event, used for analytics and the per-job API.
- ``job_telemetry_spans`` — append-only per-call detail (LLM + tool), used for
  drill-down and cross-job tool analysis.

Revision ID: 0006_otel_telemetry
Revises: 0005_add_indexes_and_version
Create Date: 2026-03-23

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0006_otel_telemetry"
down_revision: Union[str, None] = "0005_add_indexes_and_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old job_metrics table
    op.drop_table("job_metrics")

    # Create job_telemetry_summary
    op.create_table(
        "job_telemetry_summary",
        sa.Column("job_id", sa.String, sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("sdk", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False, server_default=""),
        sa.Column("repo", sa.String, nullable=False, server_default=""),
        sa.Column("branch", sa.String, nullable=False, server_default=""),
        sa.Column("status", sa.String, nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("premium_requests", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("llm_call_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_llm_duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tool_call_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tool_failure_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tool_duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("compactions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_compacted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("approval_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("approval_wait_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("agent_messages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("operator_messages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("context_window_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("current_context_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quota_json", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_summary_completed", "job_telemetry_summary", ["completed_at"])
    op.create_index("idx_summary_sdk_model", "job_telemetry_summary", ["sdk", "model"])
    op.create_index("idx_summary_status", "job_telemetry_summary", ["status"])

    # Create job_telemetry_spans
    op.create_table(
        "job_telemetry_spans",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("span_type", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("started_at", sa.Float, nullable=False),
        sa.Column("duration_ms", sa.Float, nullable=False),
        sa.Column("attrs_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_spans_job", "job_telemetry_spans", ["job_id"])


def downgrade() -> None:
    op.drop_index("idx_spans_job", table_name="job_telemetry_spans")
    op.drop_table("job_telemetry_spans")
    op.drop_index("idx_summary_status", table_name="job_telemetry_summary")
    op.drop_index("idx_summary_sdk_model", table_name="job_telemetry_summary")
    op.drop_index("idx_summary_completed", table_name="job_telemetry_summary")
    op.drop_table("job_telemetry_summary")

    # Recreate old job_metrics table
    op.create_table(
        "job_metrics",
        sa.Column("job_id", sa.String, sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("snapshot_json", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
