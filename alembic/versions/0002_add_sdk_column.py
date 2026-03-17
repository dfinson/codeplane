"""Add sdk column to jobs table.

Revision ID: 7eec20be902c
Revises: 0001_initial
Create Date: 2025-07-14

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7eec20be902c"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("sdk", sa.String(), nullable=False, server_default="copilot"))


def downgrade() -> None:
    op.drop_column("jobs", "sdk")
