"""Pin peer receipts to selected recipe attempts and execution generations."""

import sqlalchemy as sa

from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("peer_messages", sa.Column("recipient_attempt_id", sa.String(), nullable=True))
    op.add_column("peer_messages", sa.Column("recipient_execution_token", sa.String(), nullable=True))
    op.add_column("peer_messages", sa.Column("sender_attempt_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("peer_messages", "sender_attempt_id")
    op.drop_column("peer_messages", "recipient_execution_token")
    op.drop_column("peer_messages", "recipient_attempt_id")
