"""Add review_story_json column for approval artifact persistence.

Stores the full review story JSON at approval time (§11.11).

Revision ID: 0033
Revises: 0032
Create Date: 2026-05-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("review_story_json", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("review_story_hash", sa.String(64), nullable=True))
    # Structural analytics columns (§7.5)
    op.add_column("jobs", sa.Column("structural_coupling_delta", sa.Float(), nullable=True))
    op.add_column("jobs", sa.Column("structural_cycle_count", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("structural_changes_touch_tests", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("structural_change_count", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("structural_merge_confidence", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "structural_merge_confidence")
    op.drop_column("jobs", "structural_change_count")
    op.drop_column("jobs", "structural_changes_touch_tests")
    op.drop_column("jobs", "structural_cycle_count")
    op.drop_column("jobs", "structural_coupling_delta")
    op.drop_column("jobs", "review_story_hash")
    op.drop_column("jobs", "review_story_json")
