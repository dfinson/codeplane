"""Rename permission_mode values: auto→full_auto, read_only→observe_only, approval_required→review_and_approve.

Revision ID: 0014_rename_permission_modes
Revises: 0013_merge_parent_job_and_error_kind
Create Date: 2026-03-30

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014_rename_permission_modes"
down_revision: str = "0013_merge_parent_job_and_error_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RENAME_MAP = {
    "auto": "full_auto",
    "read_only": "observe_only",
    "approval_required": "review_and_approve",
}


def upgrade() -> None:
    for old, new in _RENAME_MAP.items():
        op.execute(f"UPDATE jobs SET permission_mode = '{new}' WHERE permission_mode = '{old}'")


def downgrade() -> None:
    for old, new in _RENAME_MAP.items():
        op.execute(f"UPDATE jobs SET permission_mode = '{old}' WHERE permission_mode = '{new}'")
