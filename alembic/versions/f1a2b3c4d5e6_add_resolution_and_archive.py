"""add_resolution_and_archive

Revision ID: f1a2b3c4d5e6
Revises: b30bc0b5e8f0
Create Date: 2026-03-15 16:18:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "b30bc0b5e8f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add resolution, archived_at, and completion_strategy columns."""
    op.add_column("jobs", sa.Column("resolution", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("jobs", sa.Column("completion_strategy", sa.String(), nullable=True))

    # Migrate existing data: populate resolution from merge_status for succeeded jobs
    op.execute("UPDATE jobs SET resolution = 'merged' WHERE state = 'succeeded' AND merge_status = 'merged'")
    op.execute("UPDATE jobs SET resolution = 'pr_created' WHERE state = 'succeeded' AND merge_status = 'pr_created'")
    op.execute("UPDATE jobs SET resolution = 'conflict' WHERE state = 'succeeded' AND merge_status = 'conflict'")
    # Succeeded jobs without a resolution get 'unresolved'
    op.execute("UPDATE jobs SET resolution = 'unresolved' WHERE state = 'succeeded' AND resolution IS NULL")


def downgrade() -> None:
    """Remove resolution, archived_at, and completion_strategy columns."""
    op.drop_column("jobs", "completion_strategy")
    op.drop_column("jobs", "archived_at")
    op.drop_column("jobs", "resolution")
