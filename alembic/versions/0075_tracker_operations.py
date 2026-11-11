"""Durable tracker intents, approval associations, and delivery evidence.

Revision ID: 0075
Revises: 0074
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracker_operations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source_key", sa.String(), nullable=False, unique=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("task_link_id", sa.String(), sa.ForeignKey("task_links.id", ondelete="SET NULL")),
        sa.Column("project_ref", sa.String(), nullable=False),
        sa.Column("job_ref", sa.String(), nullable=False),
        sa.Column("task_link_ref", sa.String()),
        sa.Column("tracker_link_id", sa.String(), sa.ForeignKey("tracker_links.id", ondelete="SET NULL")),
        sa.Column("tracker_link_ref", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("external_ref", sa.String(), nullable=False),
        sa.Column("credential_id_ref", sa.String(), nullable=False),
        sa.Column("ticket_ref", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_kind", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text()),
        sa.Column("approval_id", sa.String(), sa.ForeignKey("approvals.id", ondelete="SET NULL")),
        sa.Column("approval_ref", sa.String()),
        sa.Column("approval_snapshot", sa.Text()),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("claim_token", sa.String()),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_of_id", sa.String(), sa.ForeignKey("tracker_operations.id")),
        sa.Column("provider_receipt", sa.Text()),
        sa.Column("error_code", sa.String()),
        sa.Column("error_detail", sa.Text()),
        sa.Column("reconciliation", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('awaiting_approval','approved','dispatching','applied',"
            "'rejected','failed','uncertain','not_applied')",
            name="ck_tracker_operations_state",
        ),
        sa.CheckConstraint("action IN ('comment','transition')", name="ck_tracker_operations_action"),
        sa.CheckConstraint(
            "target_kind IN ('comment','github_issue_state','jira_workflow_transition','azure_devops_work_item_state')",
            name="ck_tracker_operations_target_kind",
        ),
        sa.CheckConstraint("version >= 0", name="ck_tracker_operations_version"),
    )
    op.create_index("ix_tracker_operations_project_created", "tracker_operations", ["project_ref", "created_at"])
    op.create_index("ix_tracker_operations_job_created", "tracker_operations", ["job_ref", "created_at"])
    op.create_index("ix_tracker_operations_state_claim", "tracker_operations", ["state", "claim_expires_at"])
    for column in ("project_id", "job_id", "task_link_id", "tracker_link_id", "approval_id", "retry_of_id"):
        op.create_index(
            f"ix_tracker_operations_{column}", "tracker_operations", [column], unique=column == "approval_id"
        )


def downgrade() -> None:
    op.drop_table("tracker_operations")
