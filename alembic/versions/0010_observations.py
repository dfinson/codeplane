"""Add cross-job statistical observations table.

Stores computed observations and anomalies discovered by the
statistical analysis service — e.g. "file X was reread 47 times
across 12 jobs", "tool Y has 60% failure rate", "cost/turn
doubles after turn 15".

Revision ID: 0010_observations
Revises: 0009_cost_attribution
Create Date: 2026-06-15

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0010_observations"
down_revision: Union[str, None] = "0009_cost_attribution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cost_observations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("severity", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
        sa.Column("evidence_json", sa.Text, nullable=False),
        sa.Column("job_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_waste_usd", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dismissed", sa.Boolean, nullable=False, server_default="0"),
    )
    op.create_index("idx_obs_category", "cost_observations", ["category"])
    op.create_index("idx_obs_severity", "cost_observations", ["severity"])


def downgrade() -> None:
    op.drop_index("idx_obs_severity", table_name="cost_observations")
    op.drop_index("idx_obs_category", table_name="cost_observations")
    op.drop_table("cost_observations")
