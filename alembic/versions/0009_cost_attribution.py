"""Add cost attribution table and summary turn columns.

Creates ``job_cost_attribution`` for per-job cost breakdown
(phase, tool category, turn economics).  Extends
``job_telemetry_summary`` with turn/retry/phase columns.

Revision ID: 0009_cost_attribution
Revises: 0008_cost_analytics_spans
Create Date: 2026-06-15

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0009_cost_attribution"
down_revision: Union[str, None] = "0008_cost_analytics_spans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Extend job_telemetry_summary with turn/retry/phase counters ---
    with op.batch_alter_table("job_telemetry_summary") as batch_op:
        batch_op.add_column(
            sa.Column("total_turns", sa.Integer, nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("retry_count", sa.Integer, nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("retry_cost_usd", sa.Float, nullable=False, server_default="0.0")
        )
        batch_op.add_column(
            sa.Column(
                "file_read_count", sa.Integer, nullable=False, server_default="0"
            )
        )
        batch_op.add_column(
            sa.Column(
                "file_write_count", sa.Integer, nullable=False, server_default="0"
            )
        )
        batch_op.add_column(
            sa.Column(
                "unique_files_read",
                sa.Integer,
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "file_reread_count",
                sa.Integer,
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "peak_turn_cost_usd",
                sa.Float,
                nullable=False,
                server_default="0.0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "avg_turn_cost_usd",
                sa.Float,
                nullable=False,
                server_default="0.0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "cost_first_half_usd",
                sa.Float,
                nullable=False,
                server_default="0.0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "cost_second_half_usd",
                sa.Float,
                nullable=False,
                server_default="0.0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "diff_lines_added",
                sa.Integer,
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "diff_lines_removed",
                sa.Integer,
                nullable=False,
                server_default="0",
            )
        )

    # --- Per-job cost attribution breakdown ---
    op.create_table(
        "job_cost_attribution",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "job_id", sa.String, sa.ForeignKey("jobs.id"), nullable=False
        ),
        sa.Column("dimension", sa.String, nullable=False),
        sa.Column("bucket", sa.String, nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0.0"),
        sa.Column(
            "input_tokens", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "output_tokens", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "call_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "idx_attr_job", "job_cost_attribution", ["job_id"]
    )
    op.create_index(
        "idx_attr_dimension", "job_cost_attribution", ["dimension", "bucket"]
    )


def downgrade() -> None:
    op.drop_index("idx_attr_dimension", table_name="job_cost_attribution")
    op.drop_index("idx_attr_job", table_name="job_cost_attribution")
    op.drop_table("job_cost_attribution")

    with op.batch_alter_table("job_telemetry_summary") as batch_op:
        batch_op.drop_column("diff_lines_removed")
        batch_op.drop_column("diff_lines_added")
        batch_op.drop_column("cost_second_half_usd")
        batch_op.drop_column("cost_first_half_usd")
        batch_op.drop_column("avg_turn_cost_usd")
        batch_op.drop_column("peak_turn_cost_usd")
        batch_op.drop_column("file_reread_count")
        batch_op.drop_column("unique_files_read")
        batch_op.drop_column("file_write_count")
        batch_op.drop_column("file_read_count")
        batch_op.drop_column("retry_cost_usd")
        batch_op.drop_column("retry_count")
        batch_op.drop_column("total_turns")
