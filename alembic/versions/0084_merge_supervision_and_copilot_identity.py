"""merge supervision and copilot identity

Revision ID: 0084
Revises: 0083, copilot_identity
Create Date: 2026-11-26 16:06:21.907690

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0084"
down_revision: str | Sequence[str] | None = ("0083", "copilot_identity")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
