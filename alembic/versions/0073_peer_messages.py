"""Persist attributed peer messages without changing job lifecycle."""

import sqlalchemy as sa

from alembic import op

revision = "peer_messages"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "peer_messages",
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("sender_job_id", sa.String(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_job_id", sa.String(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_session_id", sa.String(), nullable=True),
        sa.Column("recipient_session_id", sa.String(), nullable=True),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reply_to", sa.String(128), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_peer_messages_sender_job_id", "peer_messages", ["sender_job_id"])
    op.create_index("ix_peer_messages_recipient_job_id", "peer_messages", ["recipient_job_id"])


def downgrade() -> None:
    op.drop_table("peer_messages")
