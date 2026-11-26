"""Persist revocable read-only share credentials."""

import sqlalchemy as sa

from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_share_tokens_job_id", "share_tokens", ["job_id"])
    op.create_index("ix_share_tokens_expires_at", "share_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_table("share_tokens")
