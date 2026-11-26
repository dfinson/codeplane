"""Persist immutable Copilot inference credential references and display labels."""

import sqlalchemy as sa

from alembic import op

revision = "copilot_identity"
down_revision = "peer_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("copilot_credential_id", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("copilot_credential_label", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "copilot_credential_label")
    op.drop_column("jobs", "copilot_credential_id")
