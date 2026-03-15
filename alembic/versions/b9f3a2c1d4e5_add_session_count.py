"""add session_count to jobs

Revision ID: b9f3a2c1d4e5
Revises: eef13d8f8935
Create Date: 2026-03-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9f3a2c1d4e5"
down_revision: str | Sequence[str] | None = "eef13d8f8935"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("session_count", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("jobs", "session_count")
