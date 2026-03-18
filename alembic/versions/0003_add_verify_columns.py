"""Add verify/self-review columns to jobs table.

Revision ID: a3f1c8d70001
Revises: 7eec20be902c
Create Date: 2026-03-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f1c8d70001"
down_revision: Union[str, None] = "7eec20be902c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("verify", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("self_review", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("max_turns", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("verify_prompt", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("self_review_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "self_review_prompt")
    op.drop_column("jobs", "verify_prompt")
    op.drop_column("jobs", "max_turns")
    op.drop_column("jobs", "self_review")
    op.drop_column("jobs", "verify")
