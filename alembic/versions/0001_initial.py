"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-03-17

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("base_ref", sa.String(), nullable=False),
        sa.Column("branch", sa.String(), nullable=True),
        sa.Column("worktree_path", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("pr_url", sa.String(), nullable=True),
        sa.Column("merge_status", sa.String(), nullable=True),
        sa.Column("resolution", sa.String(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("worktree_name", sa.String(), nullable=True),
        sa.Column("permission_mode", sa.String(), nullable=False, server_default="auto"),
        sa.Column("session_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sdk_session_id", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(), nullable=False, unique=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_index("idx_events_job_id", "events", ["job_id"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("proposed_action", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(), nullable=True),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("disk_path", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "diff_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("diff_json", sa.Text(), nullable=False),
    )
    op.create_index("idx_diff_snapshots_job_id", "diff_snapshots", ["job_id"])


def downgrade() -> None:
    op.drop_table("diff_snapshots")
    op.drop_table("artifacts")
    op.drop_table("approvals")
    op.drop_table("events")
    op.drop_table("jobs")
