"""Persist native source identity and validated replay progress."""

import sqlalchemy as sa

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("ingest_checkpoint", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "ingest_checkpoint")
