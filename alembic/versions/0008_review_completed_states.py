"""Split 'succeeded' state into 'review' and 'completed'.

Jobs that have succeeded with resolution unresolved/conflict are moved to
the new 'review' active state.  Jobs with a final resolution (merged,
pr_created, discarded) are moved to 'completed'.

Revision ID: 0008_review_completed_states
Revises: 0007_add_requires_explicit_approval
Create Date: 2026-03-25

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_review_completed_states"
down_revision: Union[str, None] = "0007_add_requires_explicit_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Unresolved / conflict succeeded jobs → review (active state)
    op.execute(
        sa.text(
            "UPDATE jobs SET state = 'review' "
            "WHERE state = 'succeeded' "
            "AND (resolution IS NULL OR resolution IN ('unresolved', 'conflict'))"
        )
    )
    # Resolved succeeded jobs (merged, pr_created, discarded) → completed (terminal)
    op.execute(
        sa.text(
            "UPDATE jobs SET state = 'completed' "
            "WHERE state = 'succeeded' "
            "AND resolution IN ('merged', 'pr_created', 'discarded')"
        )
    )
    # Catch any remaining 'succeeded' rows (shouldn't exist, but safety net)
    op.execute(
        sa.text(
            "UPDATE jobs SET state = 'review' "
            "WHERE state = 'succeeded'"
        )
    )


def downgrade() -> None:
    # Reverse: review → succeeded, completed → succeeded
    op.execute(
        sa.text(
            "UPDATE jobs SET state = 'succeeded' "
            "WHERE state IN ('review', 'completed')"
        )
    )
