"""Add parent_job_id to jobs table to track follow-up job lineage.

Revision ID: 0012_add_parent_job_id
Revises: 0011_merge_review_and_observations_heads
Create Date: 2026-03-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0012_add_parent_job_id"
down_revision: Union[str, None] = "0011_merge_review_and_observations_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("parent_job_id", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "parent_job_id")
