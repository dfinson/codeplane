"""Verify clean bootstrap without structural storage or retirement migrations."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from alembic import command
from backend import config as backend_config
from backend.models.db import Base
from backend.persistence.database import run_migrations

if TYPE_CHECKING:
    from pathlib import Path

    from alembic.config import Config

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


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_config, "_codeplane_dir", tmp_path / "home")
    monkeypatch.setenv("CODEPLANE_HOME", str(tmp_path / "home"))


def test_fresh_bootstrap_never_creates_structural_storage(tmp_path: Path) -> None:
    statements: list[str] = []

    def observe_sql(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        if statement.lstrip().upper().startswith(("CREATE TABLE", "ALTER TABLE")):
            statements.append(statement)

    db_path = tmp_path / "fresh.db"
    event.listen(Engine, "before_cursor_execute", observe_sql)
    try:
        run_migrations(db_path)
    finally:
        event.remove(Engine, "before_cursor_execute", observe_sql)

    assert statements
    with sqlite3.connect(db_path) as db:
        for table, retired in _RETIRED_COLUMNS.items():
            columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
            assert columns
            assert retired.isdisjoint(columns)
            assert retired.isdisjoint(Base.metadata.tables[table].columns.keys())
            for name in retired:
                assert all(name not in statement for statement in statements)
        assert "story_text" in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        assert db.execute("SELECT COUNT(*) FROM secondary_sessions").fetchone()[0] == 0


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
