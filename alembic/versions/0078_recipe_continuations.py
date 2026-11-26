"""Retain multiple pinned runs per Job, with one unresolved execution.

Revision ID: 0078
Revises: 0077
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipe_runs", sa.Column("run_number", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("recipe_runs", sa.Column("start_request_key", sa.String(), nullable=True))
    op.add_column("recipe_runs", sa.Column("continuation_of_id", sa.String(), nullable=True))
    op.add_column("recipe_runs", sa.Column("instruction", sa.Text(), nullable=True))
    op.add_column("recipe_runs", sa.Column("code_ref_json", sa.Text(), nullable=True))
    op.add_column("recipe_runs", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column("recipe_runs", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE recipe_runs SET start_request_key = 'retained:' || id"))
    # Only a recorded completion without an outstanding execution/lease is
    # evidence of resolution. Missing historical facts remain unrecorded.
    op.execute(
        sa.text(
            "UPDATE recipe_runs SET resolved_at = completed_at "
            "WHERE completed_at IS NOT NULL AND state IN ('succeeded','failed','skipped','canceled') "
            "AND NOT EXISTS (SELECT 1 FROM recipe_step_instances s WHERE s.run_id = recipe_runs.id "
            "AND s.state IN ('running','needs_decision','interrupted')) "
            "AND NOT EXISTS (SELECT 1 FROM recipe_workspace_leases l "
            "JOIN recipe_attempts a ON a.id = l.attempt_id "
            "JOIN recipe_step_instances s ON s.id = a.step_instance_id WHERE s.run_id = recipe_runs.id)"
        )
    )
    with op.batch_alter_table(
        "recipe_runs",
        recreate="always",
        naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
    ) as batch:
        batch.drop_constraint("uq_recipe_runs_job_id", type_="unique")
        batch.alter_column("start_request_key", existing_type=sa.String(), nullable=False)
        batch.create_unique_constraint("uq_recipe_runs_start_request_key", ["start_request_key"])
        batch.create_unique_constraint("uq_recipe_job_run_number", ["job_id", "run_number"])
        batch.create_check_constraint("ck_recipe_run_number", "run_number >= 1")
        batch.create_foreign_key("fk_recipe_run_continuation", "recipe_runs", ["continuation_of_id"], ["id"])
    op.create_index(
        "uq_recipe_job_unresolved_run",
        "recipe_runs",
        ["job_id"],
        unique=True,
        sqlite_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.execute(
            sa.text(
                "SELECT 1 FROM recipe_runs WHERE continuation_of_id IS NOT NULL OR run_number > 1 "
                "OR instruction IS NOT NULL OR code_ref_json IS NOT NULL OR reason IS NOT NULL LIMIT 1"
            )
        ).first()
        is not None
    ):
        raise RuntimeError("Recipe continuation history exists. Roll back admission configuration, not retained runs.")
    op.drop_index("uq_recipe_job_unresolved_run", table_name="recipe_runs")
    with op.batch_alter_table("recipe_runs", recreate="always") as batch:
        batch.drop_constraint("fk_recipe_run_continuation", type_="foreignkey")
        batch.drop_constraint("uq_recipe_job_run_number", type_="unique")
        batch.drop_constraint("uq_recipe_runs_start_request_key", type_="unique")
        batch.drop_constraint("ck_recipe_run_number", type_="check")
        batch.create_unique_constraint("uq_recipe_runs_job_id", ["job_id"])
        for column in (
            "resolved_at",
            "reason",
            "code_ref_json",
            "instruction",
            "continuation_of_id",
            "start_request_key",
            "run_number",
        ):
            batch.drop_column(column)
