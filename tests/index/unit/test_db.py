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


class TestAdditionalIndexes:
    """Tests for additional index creation/deletion."""

    def test_create_additional_indexes(self, temp_dir: Path) -> None:
        """create_additional_indexes should create composite indexes."""
        from sqlalchemy import text

        from codeplane.index._internal.db import create_additional_indexes

        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()
        create_additional_indexes(db.engine)

        with db.session() as session:
            result = session.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
            indexes = {row[0] for row in result}

        expected = {
            "idx_def_facts_file_name",
            "idx_ref_facts_file_target",
            "idx_ref_facts_target_tier",
            "idx_scope_facts_file",
            "idx_import_facts_file",
            "idx_local_bind_facts_scope",
            "idx_export_surfaces_unit",
            "idx_contexts_family_status",
            "idx_anchor_groups_unit",
        }
        assert expected.issubset(indexes)

    def test_drop_additional_indexes(self, temp_dir: Path) -> None:
        """drop_additional_indexes should remove composite indexes."""
        from sqlalchemy import text

        from codeplane.index._internal.db import create_additional_indexes
        from codeplane.index._internal.db.indexes import drop_additional_indexes

        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()
        create_additional_indexes(db.engine)

        # Verify indexes exist
        with db.session() as session:
            result = session.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
            indexes = {row[0] for row in result}
        assert "idx_def_facts_file_name" in indexes

        # Drop indexes
        drop_additional_indexes(db.engine)

        # Verify indexes removed
        with db.session() as session:
            result = session.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
            indexes = {row[0] for row in result}

        expected_dropped = {
            "idx_def_facts_file_name",
            "idx_ref_facts_file_target",
            "idx_ref_facts_target_tier",
        }
        assert expected_dropped.isdisjoint(indexes)

    def test_create_indexes_is_idempotent(self, temp_dir: Path) -> None:
        """create_additional_indexes can be called multiple times."""
        from codeplane.index._internal.db import create_additional_indexes

        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        # Call twice - should not fail
        create_additional_indexes(db.engine)
        create_additional_indexes(db.engine)  # Should not raise

    def test_drop_indexes_is_idempotent(self, temp_dir: Path) -> None:
        """drop_additional_indexes can be called when no indexes exist."""
        from codeplane.index._internal.db.indexes import drop_additional_indexes

        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        # Call without creating indexes - should not fail
        drop_additional_indexes(db.engine)  # Should not raise


class TestBulkWriterAdvanced:
    """Advanced tests for BulkWriter operations."""

    def test_bulk_writer_rollback_on_error(self, temp_dir: Path) -> None:
        """BulkWriter should rollback on exception."""
        from sqlmodel import select

        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        class TestError(Exception):
            pass

        with pytest.raises(TestError), db.bulk_writer() as writer:
            writer.insert_many(
                File,
                [{"path": "a.py", "content_hash": "hash_a"}],
            )
            raise TestError("Test error")

        # File should not exist due to rollback
        with db.session() as session:
            result = session.exec(select(File).where(File.path == "a.py")).first()
            assert result is None

    def test_insert_many_empty_list(self, temp_dir: Path) -> None:
        """insert_many with empty list should be no-op."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.bulk_writer() as writer:
            writer.insert_many(File, [])  # Should not raise

    def test_insert_many_returning_ids(self, temp_dir: Path) -> None:
        """insert_many_returning_ids should return id mapping."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.bulk_writer() as writer:
            id_map = writer.insert_many_returning_ids(
                File,
                [
                    {"path": "x.py", "content_hash": "hash_x"},
                    {"path": "y.py", "content_hash": "hash_y"},
                ],
                key_columns=["path"],
            )

        # Should have 2 entries mapping path -> id
        assert len(id_map) == 2
        assert ("x.py",) in id_map
        assert ("y.py",) in id_map

    def test_insert_many_returning_ids_empty(self, temp_dir: Path) -> None:
        """insert_many_returning_ids with empty list returns empty dict."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.bulk_writer() as writer:
            id_map = writer.insert_many_returning_ids(File, [], key_columns=["path"])

        assert id_map == {}

    def test_delete_where(self, temp_dir: Path) -> None:
        """delete_where should remove matching records."""
        from sqlmodel import select

        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        # Insert files
        with db.bulk_writer() as writer:
            writer.insert_many(
                File,
                [
                    {"path": "a.py", "content_hash": "hash_a"},
                    {"path": "b.py", "content_hash": "hash_b"},
                    {"path": "c.py", "content_hash": "hash_c"},
                ],
            )

        # Delete one file
        with db.bulk_writer() as writer:
            count = writer.delete_where(File, "path = :path", {"path": "b.py"})

        assert count == 1

        # Verify
        with db.session() as session:
            files = list(session.exec(select(File)))
            paths = {f.path for f in files}
            assert "a.py" in paths
            assert "b.py" not in paths
            assert "c.py" in paths

    def test_upsert_many_insert(self, temp_dir: Path) -> None:
        """upsert_many should insert new records."""
        from sqlmodel import select

        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.bulk_writer() as writer:
            count = writer.upsert_many(
                File,
                [
                    {"path": "new.py", "content_hash": "hash_new"},
                ],
                conflict_columns=["path"],
                update_columns=["content_hash"],
            )

        assert count == 1

        with db.session() as session:
            f = session.exec(select(File).where(File.path == "new.py")).first()
            assert f is not None
            assert f.content_hash == "hash_new"

    def test_upsert_many_update(self, temp_dir: Path) -> None:
        """upsert_many should update existing records on conflict."""
        from sqlmodel import select

        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        # Insert original
        with db.bulk_writer() as writer:
            writer.insert_many(File, [{"path": "exist.py", "content_hash": "old_hash"}])

        # Upsert with updated hash
        with db.bulk_writer() as writer:
            writer.upsert_many(
                File,
                [{"path": "exist.py", "content_hash": "new_hash"}],
                conflict_columns=["path"],
                update_columns=["content_hash"],
            )

        with db.session() as session:
            f = session.exec(select(File).where(File.path == "exist.py")).first()
            assert f is not None
            assert f.content_hash == "new_hash"

    def test_upsert_many_empty(self, temp_dir: Path) -> None:
        """upsert_many with empty list returns 0."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        with db.bulk_writer() as writer:
            count = writer.upsert_many(
                File,
                [],
                conflict_columns=["path"],
                update_columns=["content_hash"],
            )

        assert count == 0

    def test_execute_raw(self, temp_dir: Path) -> None:
        """execute_raw should run raw SQL."""
        db_path = temp_dir / "test.db"
        db = Database(db_path)
        db.create_all()

        # Insert using raw SQL
        db.execute_raw(
            "INSERT INTO files (path, content_hash) VALUES (:p, :h)",
            {"p": "raw.py", "h": "hash_raw"},
        )

        # Verify
        with db.session() as session:
            from sqlmodel import select

            f = session.exec(select(File).where(File.path == "raw.py")).first()
            assert f is not None
