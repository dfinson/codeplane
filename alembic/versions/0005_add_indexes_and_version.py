"""Add missing indexes and job version column for optimistic locking.

Indexes added:
- jobs.state  (filtered queries in recovery, list, retention)
- approvals(job_id, resolved_at) (pending approval lookups)
- events(job_id, kind) (event list queries by kind)

Column added:
- jobs.version (INTEGER NOT NULL DEFAULT 1) for optimistic locking

Revision ID: 0005_add_indexes_and_version
Revises: 0004_add_job_metrics
Create Date: 2026-03-23

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_add_indexes_and_version"
down_revision: Union[str, None] = "0004_add_job_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Indexes for common query patterns
    op.create_index("idx_jobs_state", "jobs", ["state"])
    op.create_index("idx_approvals_job_resolved", "approvals", ["job_id", "resolved_at"])
    op.create_index("idx_events_job_kind", "events", ["job_id", "kind"])

    # Optimistic locking version column
    op.add_column("jobs", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("jobs", "version")
    op.drop_index("idx_events_job_kind", table_name="events")
    op.drop_index("idx_approvals_job_resolved", table_name="approvals")
    op.drop_index("idx_jobs_state", table_name="jobs")
