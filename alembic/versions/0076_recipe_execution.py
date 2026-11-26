"""Pinned recipe definitions and Job-owned execution evidence.

Revision ID: 0076
Revises: 0075
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recipe_definitions",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id", sa.String(), nullable=False, unique=True),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("latest_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1 AND latest_revision >= 0", name="ck_recipe_definition_version"),
    )
    op.create_table(
        "recipe_revisions",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id", sa.String(), nullable=False, unique=True),
        sa.Column("definition_id", sa.String(), sa.ForeignKey("recipe_definitions.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("blocks_json", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("definition_id", "revision", name="uq_recipe_revision_number"),
        sa.CheckConstraint("revision >= 1", name="ck_recipe_revision_number"),
    )
    state_check = (
        "state IN ('blocked','ready','running','needs_decision','succeeded',"
        "'failed','skipped','canceled','interrupted')"
    )
    op.create_table(
        "recipe_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("revision_id", sa.String(), sa.ForeignKey("recipe_revisions.id"), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_checksum", sa.String(64), nullable=False),
        sa.Column("inputs_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(state_check, name="ck_recipe_run_state"),
    )
    op.create_table(
        "recipe_step_instances",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("recipe_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_path", sa.String(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("current_attempt_id", sa.String()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "step_path", name="uq_recipe_step_path"),
        sa.CheckConstraint(state_check, name="ck_recipe_step_state"),
    )
    op.create_table(
        "recipe_attempts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "step_instance_id",
            sa.String(),
            sa.ForeignKey("recipe_step_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("inputs_json", sa.Text(), nullable=False),
        sa.Column("runner_json", sa.Text(), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("workspace_json", sa.Text(), nullable=False),
        sa.Column("dispatch_intent_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("step_instance_id", "number", name="uq_recipe_attempt_number"),
        sa.CheckConstraint("number >= 1", name="ck_recipe_attempt_number"),
    )
    op.create_table(
        "recipe_attempt_results",
        sa.Column("attempt_id", sa.String(), sa.ForeignKey("recipe_attempts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recipe_attempt_executions",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("attempt_id", sa.String(), sa.ForeignKey("recipe_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_token", sa.String(), nullable=False, unique=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recipe_attempt_executions_attempt_id", "recipe_attempt_executions", ["attempt_id"])
    op.create_table(
        "recipe_output_receipts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("attempt_id", sa.String(), sa.ForeignKey("recipe_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("attempt_id", "name", name="uq_recipe_attempt_output"),
    )
    op.create_table(
        "recipe_gate_bindings",
        sa.Column("attempt_id", sa.String(), sa.ForeignKey("recipe_attempts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("approval_id", sa.String(), sa.ForeignKey("approvals.id"), nullable=False, unique=True),
        sa.Column("input_checksum", sa.String(64), nullable=False),
        sa.Column("proposed_action", sa.Text(), nullable=False),
        sa.Column("next_steps_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recipe_workspace_leases",
        sa.Column("workspace", sa.String(), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(),
            sa.ForeignKey("recipe_attempts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recipe_reviews",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id", sa.String(), nullable=False, unique=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("recipe_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("code_ref_json", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_recipe_review_request"),
    )


def downgrade() -> None:
    for table in (
        "recipe_reviews",
        "recipe_workspace_leases",
        "recipe_gate_bindings",
        "recipe_output_receipts",
        "recipe_attempt_executions",
        "recipe_attempt_results",
        "recipe_attempts",
        "recipe_step_instances",
        "recipe_runs",
        "recipe_revisions",
        "recipe_definitions",
    ):
        op.drop_table(table)
