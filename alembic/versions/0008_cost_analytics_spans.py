"""Add cost analytics columns to telemetry spans.

Extends ``job_telemetry_spans`` with columns needed for cost-driver
analysis: tool category, target, turn context, phase, retry info,
and token counts.  Creates ``job_file_access_log`` for tracking
file read/write patterns across jobs.

Revision ID: 0008_cost_analytics_spans
Revises: 0007_add_requires_explicit_approval
Create Date: 2026-06-15

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008_cost_analytics_spans"
down_revision: Union[str, None] = "0007_add_requires_explicit_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enrich existing span columns ---
    with op.batch_alter_table("job_telemetry_spans") as batch_op:
        batch_op.add_column(
            sa.Column("tool_category", sa.String, nullable=True)
        )
        batch_op.add_column(
            sa.Column("tool_target", sa.String, nullable=True)
        )
        batch_op.add_column(
            sa.Column("turn_number", sa.Integer, nullable=True)
        )
        batch_op.add_column(
            sa.Column("execution_phase", sa.String, nullable=True)
        )
        batch_op.add_column(
            sa.Column("is_retry", sa.Boolean, nullable=True, server_default="0")
        )
        batch_op.add_column(
            sa.Column("retries_span_id", sa.Integer, nullable=True)
        )
        batch_op.add_column(
            sa.Column("input_tokens", sa.Integer, nullable=True)
        )
        batch_op.add_column(
            sa.Column("output_tokens", sa.Integer, nullable=True)
        )
        batch_op.add_column(
            sa.Column("cache_read_tokens", sa.Integer, nullable=True)
        )
        batch_op.add_column(
            sa.Column("cache_write_tokens", sa.Integer, nullable=True)
        )
        batch_op.add_column(
            sa.Column("cost_usd", sa.Float, nullable=True)
        )
        batch_op.add_column(
            sa.Column("tool_args_json", sa.Text, nullable=True)
        )
        batch_op.add_column(
            sa.Column("result_size_bytes", sa.Integer, nullable=True)
        )

    op.create_index(
        "idx_spans_category", "job_telemetry_spans", ["tool_category"]
    )
    op.create_index(
        "idx_spans_turn", "job_telemetry_spans", ["job_id", "turn_number"]
    )
    op.create_index(
        "idx_spans_phase", "job_telemetry_spans", ["execution_phase"]
    )

    # --- File access log ---
    op.create_table(
        "job_file_access_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "job_id", sa.String, sa.ForeignKey("jobs.id"), nullable=False
        ),
        sa.Column("file_path", sa.String, nullable=False),
        sa.Column("access_type", sa.String, nullable=False),  # read / write
        sa.Column("turn_number", sa.Integer, nullable=True),
        sa.Column("span_id", sa.Integer, nullable=True),
        sa.Column("byte_count", sa.Integer, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "idx_file_access_job", "job_file_access_log", ["job_id"]
    )
    op.create_index(
        "idx_file_access_path", "job_file_access_log", ["file_path"]
    )


def downgrade() -> None:
    op.drop_index("idx_file_access_path", table_name="job_file_access_log")
    op.drop_index("idx_file_access_job", table_name="job_file_access_log")
    op.drop_table("job_file_access_log")

    op.drop_index("idx_spans_phase", table_name="job_telemetry_spans")
    op.drop_index("idx_spans_turn", table_name="job_telemetry_spans")
    op.drop_index("idx_spans_category", table_name="job_telemetry_spans")

    with op.batch_alter_table("job_telemetry_spans") as batch_op:
        batch_op.drop_column("result_size_bytes")
        batch_op.drop_column("tool_args_json")
        batch_op.drop_column("cost_usd")
        batch_op.drop_column("cache_write_tokens")
        batch_op.drop_column("cache_read_tokens")
        batch_op.drop_column("output_tokens")
        batch_op.drop_column("input_tokens")
        batch_op.drop_column("retries_span_id")
        batch_op.drop_column("is_retry")
        batch_op.drop_column("execution_phase")
        batch_op.drop_column("turn_number")
        batch_op.drop_column("tool_target")
        batch_op.drop_column("tool_category")
