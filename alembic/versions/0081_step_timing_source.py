"""Separate source bounds from transport timestamps without rewriting old timing.

Revision ID: 0081
Revises: 0080
"""

import sqlalchemy as sa

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "steps", sa.Column("timing_source", sa.String(), nullable=False, server_default="legacy_unrecorded"),
    )
    with op.batch_alter_table("steps") as batch:
        batch.alter_column("started_at", existing_type=sa.DateTime(timezone=True), nullable=True)
        batch.create_check_constraint(
            "ck_steps_timing_source", "timing_source IN ('legacy_unrecorded','live','source')",
        )
        batch.create_check_constraint("ck_steps_live_start", "timing_source != 'live' OR started_at IS NOT NULL")


def downgrade() -> None:
    retained = op.get_bind().execute(sa.text(
        "SELECT 1 FROM steps WHERE started_at IS NULL OR timing_source != 'legacy_unrecorded' LIMIT 1"
    )).first()
    if retained is not None:
        raise RuntimeError("Source timing provenance exists. Roll back admission without erasing step history.")
    with op.batch_alter_table("steps") as batch:
        batch.drop_constraint("ck_steps_live_start", type_="check")
        batch.drop_constraint("ck_steps_timing_source", type_="check")
        batch.alter_column("started_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.drop_column("timing_source")
