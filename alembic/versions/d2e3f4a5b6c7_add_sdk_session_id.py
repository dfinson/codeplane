"""Add sdk_session_id to jobs table.

Revision ID: d2e3f4a5b6c7
Revises: b9f3a2c1d4e5
Create Date: 2026-03-15 00:00:00.000000

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "b9f3a2c1d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("sdk_session_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "sdk_session_id")
