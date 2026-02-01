"""Unit tests for database layer (db.py, indexes.py).

Tests cover:
- Engine creation with correct pragmas (WAL, busy_timeout, foreign_keys)
- Table creation via create_all()
- Session context manager
- BulkWriter basic operations
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import select

from codeplane.index._internal.db import Database
from codeplane.index.models import Context, DefFact, File, ProbeStatus


class TestDatabaseEngine:
    """Tests for Database engine configuration."""

    def test_engine_created_with_wal_mode(self, temp_dir: Path) -> None:
        """Engine should use WAL journal mode."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.session() as session:
            from sqlalchemy import text

            result = session.execute(text("PRAGMA journal_mode"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == "wal"

    def test_engine_created_with_busy_timeout(self, temp_dir: Path) -> None:
        """Engine should have 30 second busy timeout."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.session() as session:
            from sqlalchemy import text

            result = session.execute(text("PRAGMA busy_timeout"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 30000

    def test_engine_created_with_foreign_keys_enabled(self, temp_dir: Path) -> None:
        """Engine should have foreign keys enabled."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.session() as session:
            from sqlalchemy import text

            result = session.execute(text("PRAGMA foreign_keys"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 1


class TestDatabaseTables:
    """Tests for table creation."""

    def test_create_all_creates_tables(self, temp_dir: Path) -> None:
        """create_all() should create all expected tables."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.session() as session:
            from sqlalchemy import text

            result = session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = {row[0] for row in result}

        expected_tables = {
            "files",
            "contexts",
            "context_markers",
            "def_facts",
            "ref_facts",
            "scope_facts",
            "local_bind_facts",
            "import_facts",
            "export_surfaces",
            "export_entries",
            "export_thunks",
            "anchor_groups",
            "dynamic_access_sites",
            "repo_state",
            "epochs",
        }
        assert expected_tables.issubset(tables)


class TestSession:
    """Tests for session context manager."""

    def test_session_commit(self, temp_dir: Path) -> None:
        """Session should commit when explicitly requested."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.session() as session:
            file = File(path="test.py", content_hash="abc123")
            session.add(file)
            session.commit()

        with db.session() as session:
            result = session.exec(select(File).where(File.path == "test.py")).first()
            assert result is not None
            assert result.content_hash == "abc123"

    def test_session_rollback_on_error(self, temp_dir: Path) -> None:
        """Session should rollback on exception."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        class TestError(Exception):
            pass

        with pytest.raises(TestError), db.session() as session:
            file = File(path="rollback.py", content_hash="xyz789")
            session.add(file)
            raise TestError("Test error")

        with db.session() as session:
            result = session.exec(select(File).where(File.path == "rollback.py")).first()
            assert result is None


class TestBulkWriter:
    """Tests for BulkWriter operations."""

    def test_insert_many_files(self, temp_dir: Path) -> None:
        """insert_many should insert multiple records."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.bulk_writer() as writer:
            writer.insert_many(
                File,
                [
                    {"path": "a.py", "content_hash": "hash_a"},
                    {"path": "b.py", "content_hash": "hash_b"},
                ],
            )

        with db.session() as session:
            files = list(session.exec(select(File)))
            assert len(files) == 2
            paths = {f.path for f in files}
            assert paths == {"a.py", "b.py"}

    def test_insert_many_def_facts(self, temp_dir: Path) -> None:
        """insert_many should work with DefFact table."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        # Create file and context first
        with db.session() as session:
            file = File(path="test.py", content_hash="abc")
            session.add(file)
            session.commit()
            file_id = file.id

            ctx = Context(
                name="test",
                language_family="python",
                root_path=".",
                probe_status=ProbeStatus.VALID.value,
            )
            session.add(ctx)
            session.commit()
            ctx_id = ctx.id

        with db.bulk_writer() as writer:
            writer.insert_many(
                DefFact,
                [
                    {
                        "def_uid": "uid_foo",
                        "file_id": file_id,
                        "unit_id": ctx_id,
                        "kind": "function",
                        "name": "foo",
                        "lexical_path": "foo",
                        "start_line": 1,
                        "start_col": 0,
                        "end_line": 5,
                        "end_col": 0,
                    },
                ],
            )

        with db.session() as session:
            defs = list(session.exec(select(DefFact)))
            assert len(defs) == 1
            assert defs[0].name == "foo"
            assert defs[0].def_uid == "uid_foo"
