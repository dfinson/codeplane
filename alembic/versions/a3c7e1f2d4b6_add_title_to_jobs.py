"""add_title_to_jobs

Revision ID: a3c7e1f2d4b6
Revises: 8b0b1c2ee2d1
Create Date: 2026-03-15 14:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c7e1f2d4b6"
down_revision: str | None = "8b0b1c2ee2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("title", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "title")
