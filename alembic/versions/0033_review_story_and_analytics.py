"""Historical structural columns, retired by migration 0074.

Keep revision 0033 addressable for databases created before retirement.

Revision ID: 0033
Revises: 0032
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("review_story_json", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("review_story_hash", sa.String(64), nullable=True))
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
