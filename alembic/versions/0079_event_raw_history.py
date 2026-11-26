"""Retain producer-supplied raw canonical events without reconstructing history.

Revision ID: 0079
Revises: 0078
"""

import sqlalchemy as sa

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("raw_event", sa.Text(), nullable=True))


def downgrade() -> None:
    retained = op.get_bind().execute(sa.text("SELECT 1 FROM events WHERE raw_event IS NOT NULL LIMIT 1")).first()
    if retained is not None:
        raise RuntimeError("Raw event history is present. Roll back admission configuration, not retained events.")
    op.drop_column("events", "raw_event")
