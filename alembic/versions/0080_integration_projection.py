"""Keep one bounded projection checkpoint per registered source session.

Revision ID: 0080
Revises: 0079
"""

import sqlalchemy as sa

from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_projection_checkpoints",
        sa.Column("binding_id", sa.String(), sa.ForeignKey("integration_bindings.id"), primary_key=True),
        sa.Column("parser_revision", sa.String(), nullable=False),
        sa.Column("parser_sha256", sa.String(64), nullable=False),
        sa.Column("canonical_sha256", sa.String(64), nullable=False),
        sa.Column("buffer_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_integration_projection_version"),
        sa.CheckConstraint("state IN ('accepted','reconciliation_required')", name="ck_integration_projection_state"),
        sa.CheckConstraint("length(CAST(buffer_json AS BLOB)) <= 8388608", name="ck_integration_projection_bound"),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM integration_projection_checkpoints LIMIT 1")).first() is not None:
        raise RuntimeError(
            "Integration projection history is present. Reconcile admission without erasing checkpoints."
        )
    op.drop_table("integration_projection_checkpoints")
