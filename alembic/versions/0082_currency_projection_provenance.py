"""Mark source-qualified derived currency without rewriting historical amounts."""

import sqlalchemy as sa

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, field in (
        ("job_telemetry_summary", "derived_currency_qualified"),
        ("job_cost_attribution", "currency_qualified"),
        ("job_file_cost", "currency_qualified"),
    ):
        op.add_column(table, sa.Column(field, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for table, field in (
        ("job_file_cost", "currency_qualified"),
        ("job_cost_attribution", "currency_qualified"),
        ("job_telemetry_summary", "derived_currency_qualified"),
    ):
        op.drop_column(table, field)
