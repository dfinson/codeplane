"""Add model and cache token columns to job_cost_attribution.

Supports per-model edit efficiency tracking (Item 6) and token-type
disaggregation in attribution buckets (Item 8).

Revision ID: 0034
Revises: 0033
Create Date: 2026-05-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_cost_attribution",
        sa.Column("model", sa.String(), nullable=True),
    )
    op.add_column(
        "job_cost_attribution",
        sa.Column("cache_read_tokens", sa.BigInteger(), server_default="0"),
    )
    op.add_column(
        "job_cost_attribution",
        sa.Column("cache_write_tokens", sa.BigInteger(), server_default="0"),
    )
    op.create_index(
        "ix_cost_attribution_model",
        "job_cost_attribution",
        ["model"],
    )


def downgrade() -> None:
    op.drop_index("ix_cost_attribution_model")
    op.drop_column("job_cost_attribution", "cache_write_tokens")
    op.drop_column("job_cost_attribution", "cache_read_tokens")
    op.drop_column("job_cost_attribution", "model")
