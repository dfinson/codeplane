"""Add requires_explicit_approval column to approvals table.

Hard-blocked operations (e.g. ``git reset --hard``) must never be
auto-resolved by a blanket trust grant.  This column flags those approvals
so the operator is always required to click Approve explicitly.

Revision ID: 0007_add_requires_explicit_approval
Revises: 0006_otel_telemetry
Create Date: 2026-03-22

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_add_requires_explicit_approval"
down_revision: Union[str, None] = "0006_otel_telemetry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column(
            "requires_explicit_approval",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("approvals", "requires_explicit_approval")
