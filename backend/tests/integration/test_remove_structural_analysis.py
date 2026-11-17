"""Exercise structural retirement through the real, isolated Alembic chain."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic.config import Config
from alembic.util import CommandError
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from backend import config as backend_config
from backend.models.db import Base
from backend.persistence.database import run_migrations
from backend.persistence.event_repo import EventRepository

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_RETIRED_COLUMNS = {
    "jobs": {
        "review_story_json",
        "review_story_hash",
        "structural_coupling_delta",
        "structural_cycle_count",
        "structural_changes_touch_tests",
        "structural_change_count",
        "structural_merge_confidence",
    },
    "trail_nodes": {"semantic_targets"},
}
_REMOVED_EVENTS = {101, 103, 105, 107}
_BRIEF = "Read the historical coderecon result; inspect src\\worker.py before editing."


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_config, "_codeplane_dir", tmp_path / "home")
    monkeypatch.setenv("CODEPLANE_HOME", str(tmp_path / "home"))


def _alembic(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _snapshot(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        schema = [tuple(row) for row in db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")]
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        rows = {name: [dict(row) for row in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')] for name in tables}
        return {"schema": schema, "rows": rows}


def _assert_no_structural_storage(db_path: Path) -> None:
    with sqlite3.connect(db_path) as db:
        remaining = {
            f"{table}.{row[1]}"
            for table, retired in _RETIRED_COLUMNS.items()
            for row in db.execute(f'PRAGMA table_info("{table}")')
            if row[1] in retired
        }
    assert not remaining, f"Structural storage remains: {sorted(remaining)}"


def test_fresh_schema_has_no_structural_storage(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    command.upgrade(_alembic(db_path), "head")
    _assert_no_structural_storage(db_path)
    for table, retired in _RETIRED_COLUMNS.items():
        assert retired.isdisjoint(Base.metadata.tables[table].columns.keys())


@pytest.mark.parametrize("packaged", [False, True])
def test_migration_runner_selects_installed_assets_before_adjacent_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, packaged: bool
) -> None:
    from backend.persistence import database

    backend_dir = tmp_path / "backend"
    source_scripts = tmp_path / "alembic"
    source_scripts.mkdir()
    packaged_scripts = backend_dir / "_migrations"
    if packaged:
        packaged_scripts.mkdir(parents=True)
    monkeypatch.setattr(database, "__file__", str(backend_dir / "persistence" / "database.py"))
    calls = []

    def upgrade(cfg: Config, revision: str) -> None:
        calls.append((cfg.get_main_option("script_location"), revision))

    monkeypatch.setattr(command, "upgrade", upgrade)
    run_migrations(tmp_path / "installed.db")
    expected = packaged_scripts if packaged else source_scripts
    assert calls == [(str(expected), "head")]


def test_unknown_revision_is_not_stamped(tmp_path: Path) -> None:
    db_path = tmp_path / "unknown.db"
    command.upgrade(_alembic(db_path), "head")
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE alembic_version SET version_num = 'unknown-external-revision'")
    before = _snapshot(db_path)
    failure = None
    try:
        run_migrations(db_path)
    except (CommandError, RuntimeError, ValueError) as exc:
        failure = exc
    assert _snapshot(db_path) == before, "An unknown revision must never be stamped or otherwise changed"
    assert failure is not None, "An unknown revision must stop startup rather than silently continue"


def _seed_predecessor(db_path: Path) -> None:
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")

            def insert(table: str, **values: Any) -> None:
                conn.execute(Base.metadata.tables[table].insert().values(**values))

            insert(
                "projects",
                id="project",
                name="Preserved",
                repo_paths='["fixture-repo"]',
                created_at=now,
                updated_at=now,
            )
            for job_id, parent in (("parent", None), ("job", "parent")):
                insert(
                    "jobs",
                    id=job_id,
                    project_id="project",
                    repo="fixture-repo",
                    prompt="Preserve evidence",
                    state="review",
                    base_ref="main",
                    parent_job_id=parent,
                    model="primary-model",
                    story_text="Narrative mentions structural analysis without owning its data.",
                    created_at=now,
                    updated_at=now,
                )
            conn.exec_driver_sql(
                "UPDATE jobs SET review_story_json = ?, review_story_hash = ?, structural_coupling_delta = ?, "
                "structural_cycle_count = ?, structural_changes_touch_tests = ?, structural_change_count = ?, "
                "structural_merge_confidence = ?",
                ('{"verdict":"historic"}', "a" * 64, 1.5, 2, 1, 3, "high"),
            )
            insert(
                "steps",
                id="step",
                job_id="job",
                step_number=1,
                trigger="operator",
                intent="Inspect worker",
                agent_message="Keep this transcript",
                started_at=now,
            )
            insert(
                "trail_nodes",
                id="trail",
                job_id="job",
                seq=9,
                anchor_seq=4,
                kind="write",
                timestamp=now,
                step_id="step",
                rationale="Keep safety checks",
                write_summary="Preserve cancellation",
                edit_motivations='[{"reason":"Keep user control"}]',
                preceding_context='["original context"]',
            )
            conn.exec_driver_sql("UPDATE trail_nodes SET semantic_targets = ?", ('[{"symbol":"Worker.run"}]',))
            insert(
                "approvals",
                id="approval",
                job_id="job",
                description="Approve launch",
                proposed_action='{"tool":"edit"}',
                requested_at=now,
                notes="Operator evidence",
            )
            insert(
                "artifacts",
                id="artifact",
                job_id="job",
                name="report",
                type="file",
                mime_type="text/plain",
                size_bytes=42,
                disk_path="fixture-artifact.txt",
                phase="review",
                step_id="step",
                created_at=now,
            )
            for task_id, depends_on in (("task-root", "[]"), ("task-child", '["task-root"]')):
                insert(
                    "task_links",
                    id=task_id,
                    project_id="project",
                    repo_path="fixture-repo",
                    depends_on=depends_on,
                    chain_root_id="task-root",
                    job_id="job",
                    prompt_override="Keep override",
                    output_routes='[{"destination":"tracker"}]',
                    created_at=now,
                    updated_at=now,
                )
            insert(
                "chats",
                id="chat",
                project_id="project",
                task_link_id="task-child",
                title="Task discussion",
                created_at=now,
                last_message_at=now,
            )
            insert(
                "chat_messages", id="message", chat_id="chat", role="user", content="Keep requirements", created_at=now
            )
            for session_id, kind in (("scout", "preflight"), ("sidecar", "sidecar")):
                insert(
                    "secondary_sessions",
                    id=session_id,
                    job_id="job",
                    kind=kind,
                    name=session_id,
                    status="completed",
                    started_at=now,
                    completed_at=now,
                    output=_BRIEF,
                    input_tokens=123,
                    output_tokens=45,
                    cost_usd=0.012,
                    metadata_json='{"model":"utility-model"}',
                )
                insert(
                    "secondary_session_entries",
                    session_id=session_id,
                    seq=1,
                    timestamp=now,
                    kind="tool_call",
                    content="Historical exploration",
                    tool_name="coderecon.search",
                    tool_args='{"query":"Worker"}',
                    tool_result='{"symbols":["Worker"]}',
                    tool_success=True,
                )
                insert(
                    "job_telemetry_summary",
                    job_id="job",
                    session_kind=kind,
                    sdk="copilot",
                    model="utility-model",
                    repo="fixture-repo",
                    input_tokens=123,
                    output_tokens=45,
                    total_cost_usd=0.012,
                    premium_requests=0.25,
                    created_at=now,
                    updated_at=now,
                )
                insert(
                    "job_telemetry_spans",
                    job_id="job",
                    session_kind=kind,
                    span_type="llm",
                    name="scout-call",
                    started_at="1.0",
                    duration_ms="2.0",
                    attrs_json='{"model":"utility-model"}',
                    created_at=now,
                )
            insert("job_file_cost", job_id="job", file_path="src/worker.py", cost_usd=0.012, created_at=now)
            insert(
                "job_cost_attribution",
                job_id="job",
                dimension="session_kind",
                bucket="preflight",
                cost_usd=0.012,
                input_tokens=123,
                output_tokens=45,
                created_at=now,
            )
            events = [
                (101, "repo.index_progress", {"progress": 10}),
                (102, "secondary_session.started", {"session_id": "scout", "kind": "preflight"}),
                (103, "repo.index_complete", {"repo": "fixture-repo"}),
                (104, "secondary_session.entry", {"session_id": "scout", "content": "coderecon.search"}),
                (105, "structural.warning", {"warning": "cycle"}),
                (106, "secondary_session.completed", {"session_id": "scout", "output": _BRIEF}),
                (107, "job.setup_progress", {"step": "worktree_indexed"}),
                (108, "job.setup_progress", {"step": "exploring_codebase", "message": "Scout running"}),
                (109, "context.handoff", {"source": "preflight", "content": _BRIEF}),
                (
                    110,
                    "diff.updated",
                    {
                        "symbols": ["top-level-value-must-stay"],
                        "churn": {"total": 7},
                        "changed_files": [
                            {
                                "path": "src/worker.py",
                                "status": "modified",
                                "additions": 4,
                                "deletions": 3,
                                "hunks": [{"text": "+keep", "symbols": ["nested-value-must-stay"]}],
                                "symbols": [{"name": "Worker.run"}],
                            },
                            {"path": "README.md", "status": "added", "hunks": []},
                        ],
                    },
                ),
                (111, "telemetry.usage", {"session_kind": "preflight", "input_tokens": 123, "cost": 0.012}),
                (112, "message.assistant", {"content": "Historical coderecon analysis", "symbols": ["keep"]}),
                (113, "repo.index_progress.extra", {"step": "worktree_indexed"}),
                (114, "structural.warning.extra", {"symbols": ["keep"]}),
                (115, "job.setup_progress", {"step": "worktree_indexed_extra"}),
                (116, "diff.updated", {"changed_files": [{"path": "untouched.py", "hunks": []}]}),
            ]
            for cursor, kind, payload in events:
                insert(
                    "events",
                    id=cursor,
                    event_id=f"event-{cursor}",
                    job_id="job",
                    kind=kind,
                    timestamp=now,
                    payload=json.dumps(payload, indent=2),
                    event_metadata=json.dumps(
                        {"turn_id": "turn-1", "motivation": {"intent": "Preserve exact metadata"}}
                    ),
                )
    finally:
        engine.dispose()


@pytest.fixture
def predecessor_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "predecessor.db"
    # Do not substitute a synthetic revision or stamp a schema built from today's ORM.
    command.upgrade(_alembic(db_path), "0073")
    _seed_predecessor(db_path)
    return db_path


def test_upgrade_preserves_scout_and_unrelated_evidence(predecessor_db: Path) -> None:
    before = _snapshot(predecessor_db)["rows"]
    command.upgrade(_alembic(predecessor_db), "0074")
    _assert_no_structural_storage(predecessor_db)
    after = _snapshot(predecessor_db)["rows"]
    expected = before
    expected["alembic_version"] = [{"version_num": "0074"}]
    for table, retired in _RETIRED_COLUMNS.items():
        expected[table] = [{key: value for key, value in row.items() if key not in retired} for row in before[table]]
    expected["events"] = [row for row in before["events"] if row["id"] not in _REMOVED_EVENTS]
    for row in expected["events"]:
        if row["id"] == 110:
            payload = json.loads(row["payload"])
            for changed_file in payload["changed_files"]:
                changed_file.pop("symbols", None)
            row["payload"] = payload
    for row in after["events"]:
        if row["id"] == 110:
            row["payload"] = json.loads(row["payload"])
    assert after == expected
    with sqlite3.connect(predecessor_db) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


async def test_replay_advances_over_deleted_ids_without_resequencing(predecessor_db: Path) -> None:
    command.upgrade(_alembic(predecessor_db), "0074")
    engine = create_async_engine(f"sqlite+aiosqlite:///{predecessor_db}")
    try:
        async with async_sessionmaker(engine)() as session:
            repo = EventRepository(session)
            first = await repo.list_after(101, job_id="job", limit=2)
            second = await repo.list_after(first[-1].storage_cursor, job_id="job", limit=2)
        assert [stored.storage_cursor for stored in first + second] == [102, 104, 106, 108]
        assert [stored.event.id for stored in first + second] == ["event-102", "event-104", "event-106", "event-108"]
        assert second[0].event.payload["output"] == _BRIEF
        assert second[1].event.payload["step"] == "exploring_codebase"
    finally:
        await engine.dispose()


def test_failure_after_column_drop_rolls_back_schema_data_and_version(predecessor_db: Path) -> None:
    before = _snapshot(predecessor_db)
    dropped: list[str] = []

    def fail_after_drop(
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if "DROP COLUMN" in statement.upper():
            dropped.append(statement)
            raise RuntimeError("injected failure after native column drop")

    event.listen(Engine, "after_cursor_execute", fail_after_drop)
    try:
        with pytest.raises(RuntimeError, match="injected failure after native column drop"):
            command.upgrade(_alembic(predecessor_db), "0074")
    finally:
        event.remove(Engine, "after_cursor_execute", fail_after_drop)
    assert dropped, "The failure must occur after an actual native column drop"
    assert _snapshot(predecessor_db) == before


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("diff.updated", "{"),
        ("diff.updated", "[]"),
        ("diff.updated", '{"changed_files":{}}'),
        ("diff.updated", '{"changed_files":[false]}'),
        ("job.setup_progress", "{"),
        ("job.setup_progress", "[]"),
    ],
)
def test_invalid_target_json_aborts_without_partial_retirement(
    predecessor_db: Path,
    kind: str,
    payload: str,
) -> None:
    with sqlite3.connect(predecessor_db) as db:
        db.execute("UPDATE events SET kind = ?, payload = ? WHERE id = 110", (kind, payload))
    before = _snapshot(predecessor_db)
    with pytest.raises((ValueError, RuntimeError)):
        command.upgrade(_alembic(predecessor_db), "0074")
    assert _snapshot(predecessor_db) == before


def test_upgrade_is_repeatable_and_irreversible_downgrade_refuses(predecessor_db: Path) -> None:
    command.upgrade(_alembic(predecessor_db), "0074")
    before = _snapshot(predecessor_db)
    command.upgrade(_alembic(predecessor_db), "0074")
    assert _snapshot(predecessor_db) == before
    with pytest.raises((RuntimeError, ValueError, CommandError), match="(?i)irreversible|backup|cannot.*restore"):
        command.downgrade(_alembic(predecessor_db), "0073")
    assert _snapshot(predecessor_db) == before


def test_foreign_key_violation_aborts_without_partial_retirement(predecessor_db: Path) -> None:
    with sqlite3.connect(predecessor_db) as db:
        db.execute("UPDATE jobs SET project_id = 'missing-project' WHERE id = 'job'")
        assert db.execute("PRAGMA foreign_key_check").fetchall()
    before = _snapshot(predecessor_db)
    with pytest.raises((RuntimeError, ValueError), match="(?i)foreign.key"):
        command.upgrade(_alembic(predecessor_db), "0074")
    assert _snapshot(predecessor_db) == before
