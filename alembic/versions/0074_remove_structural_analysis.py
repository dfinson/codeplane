"""Remove structural analysis storage while preserving Scout and job evidence.

Revision ID: 0074
Revises: 0073
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

_JOB_COLUMNS = (
    "review_story_json",
    "review_story_hash",
    "structural_coupling_delta",
    "structural_cycle_count",
    "structural_changes_touch_tests",
    "structural_change_count",
    "structural_merge_confidence",
)


def upgrade() -> None:
    connection = op.get_bind()
    if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
        raise RuntimeError("Structural retirement requires a database with valid foreign keys")

    rows = connection.execute(
        sa.text("SELECT id, kind, payload FROM events WHERE kind IN ('diff.updated', 'job.setup_progress')")
    ).fetchall()
    for event_id, kind, raw_payload in rows:
        try:
            payload = json.loads(raw_payload)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Invalid event payload during structural retirement: {event_id}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Expected event object during structural retirement: {event_id}")
        if kind == "job.setup_progress":
            if payload.get("step") == "worktree_indexed":
                connection.execute(sa.text("DELETE FROM events WHERE id = :id"), {"id": event_id})
            continue
        files = payload.get("changed_files")
        if not isinstance(files, list) or any(not isinstance(file, dict) for file in files):
            raise ValueError(f"Expected changed_files objects during structural retirement: {event_id}")
        changed = False
        for file in files:
            if "symbols" in file:
                del file["symbols"]
                changed = True
        if changed:
            connection.execute(
                sa.text("UPDATE events SET payload = :payload WHERE id = :id"),
                {"id": event_id, "payload": json.dumps(payload, ensure_ascii=False)},
            )

    connection.execute(
        sa.text("DELETE FROM events WHERE kind IN ('repo.index_progress', 'repo.index_complete', 'structural.warning')")
    )
    inspector = sa.inspect(connection)
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    for column in _JOB_COLUMNS:
        if column in job_columns:
            op.drop_column("jobs", column)
    if "semantic_targets" in {column["name"] for column in inspector.get_columns("trail_nodes")}:
        op.drop_column("trail_nodes", "semantic_targets")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
        raise RuntimeError("Structural retirement violated database foreign keys")


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0074 removes structural results irreversibly. Restore a pre-upgrade backup to downgrade."
    )
