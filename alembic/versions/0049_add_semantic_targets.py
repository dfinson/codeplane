"""Add semantic_targets column to trail_nodes.

Stores Structural change data (symbols, kinds, impact) as
JSON on write sub-nodes.  Populated by the trail enricher using
semantic_diff between parent modify node SHAs.

Revision ID: 0049
Revises: 0048
Create Date: 2026-05-17

"""

import sqlalchemy as sa

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trail_nodes", sa.Column("semantic_targets", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("trail_nodes", "semantic_targets")
