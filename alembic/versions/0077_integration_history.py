"""Neutral integration bindings and workspace-free observation history.

Revision ID: 0077
Revises: 0076
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_bindings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("adapter_id", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("credential_hash", sa.String(64), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('observation','execution')", name="ck_integration_binding_mode"),
        sa.CheckConstraint("version >= 1", name="ck_integration_binding_version"),
    )
    op.create_index("ix_integration_bindings_project", "integration_bindings", ["project_id"])
    op.create_index("ix_integration_bindings_job", "integration_bindings", ["job_id"])
    op.create_table(
        "integration_source_checkpoints",
        sa.Column("binding_id", sa.String(), sa.ForeignKey("integration_bindings.id"), primary_key=True),
        sa.Column("source_key", sa.String(), primary_key=True),
        sa.Column("parser_revision", sa.String(), primary_key=True),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("prefix_sha256", sa.String(64), nullable=False),
        sa.Column("canonical_sha256", sa.String(64), nullable=False),
        sa.Column("parser_sha256", sa.String(64), nullable=False),
        sa.Column("buffer_json", sa.Text(), nullable=False),
        sa.Column("legacy_checkpoint_json", sa.Text(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("record_count >= 0 AND version >= 1", name="ck_integration_checkpoint_progress"),
        sa.CheckConstraint("state IN ('accepted','reconciliation_required')", name="ck_integration_checkpoint_state"),
    )
    op.create_table(
        "integration_identities",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("adapter_id", sa.String(), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("pinned_key_fingerprint", sa.String(), nullable=False),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.add_column(sa.Column("integration_binding_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("observation_only", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key(
            "fk_jobs_integration_binding", "integration_bindings", ["integration_binding_id"], ["id"]
        )
        batch.alter_column("repo", existing_type=sa.String(), nullable=True)
        batch.alter_column("base_ref", existing_type=sa.String(), nullable=True)
        batch.create_check_constraint(
            "ck_jobs_observation_source", "observation_only = 0 OR source = 'integration'"
        )
        batch.create_check_constraint(
            "ck_jobs_workspace_identity",
            "(source = 'integration' AND observation_only = 1) OR (repo IS NOT NULL AND base_ref IS NOT NULL)",
        )


def downgrade() -> None:
    connection = op.get_bind()
    has_history = any(
        connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None
        for table in ("integration_bindings", "integration_source_checkpoints", "integration_identities")
    )
    has_observations = connection.execute(
        sa.text("SELECT 1 FROM jobs WHERE observation_only = 1 OR integration_binding_id IS NOT NULL LIMIT 1")
    ).first() is not None
    if has_history or has_observations:
        raise RuntimeError(
            "Integration history is present. Roll back admission configuration, not its history/approval schema."
        )
    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.drop_constraint("ck_jobs_workspace_identity", type_="check")
        batch.drop_constraint("ck_jobs_observation_source", type_="check")
        batch.drop_constraint("fk_jobs_integration_binding", type_="foreignkey")
        batch.drop_column("observation_only")
        batch.drop_column("integration_binding_id")
        batch.alter_column("repo", existing_type=sa.String(), nullable=False)
        batch.alter_column("base_ref", existing_type=sa.String(), nullable=False)
    op.drop_table("integration_identities")
    op.drop_table("integration_source_checkpoints")
    op.drop_index("ix_integration_bindings_job", table_name="integration_bindings")
    op.drop_index("ix_integration_bindings_project", table_name="integration_bindings")
    op.drop_table("integration_bindings")
