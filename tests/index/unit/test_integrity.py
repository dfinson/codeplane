"""Tests for index integrity verification and recovery."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import text

from codeplane.index._internal.db import Database, IndexRecovery, IntegrityChecker, IntegrityReport
from codeplane.index._internal.indexing import LexicalIndex
from codeplane.index.models import Context, DefFact, File, RefFact


class TestIntegrityChecker:
    """Tests for IntegrityChecker."""

    def test_healthy_index_passes(self, tmp_path: Path) -> None:
        """A healthy index should pass all checks."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        # Create valid data
        with db.session() as session:
            context = Context(
                name="test",
                language_family="python",
                root_path="src",
            )
            session.add(context)
            session.flush()

            file = File(path="src/main.py", language_family="python")
            session.add(file)
            session.flush()

            # Create a valid def
            def_fact = DefFact(
                def_uid="def_1",
                file_id=file.id,
                unit_id=context.id,
                kind="function",
                name="main",
                lexical_path="main",
                start_line=1,
                start_col=0,
                end_line=3,
                end_col=0,
            )
            session.add(def_fact)

            # Create a valid ref
            ref_fact = RefFact(
                file_id=file.id,
                unit_id=context.id,
                token_text="main",
                start_line=5,
                start_col=0,
                end_line=5,
                end_col=4,
                role="reference",
            )
            session.add(ref_fact)
            session.commit()

        # Create the file on disk
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def main(): pass")

        checker = IntegrityChecker(db, tmp_path)
        report = checker.verify()

        assert report.passed
        assert len(report.issues) == 0
        assert report.files_checked == 1

    def test_detects_orphan_refs(self, tmp_path: Path) -> None:
        """Should detect refs pointing to non-existent files."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        # First create a valid context
        with db.session() as session:
            context = Context(
                name="test",
                language_family="python",
                root_path="src",
            )
            session.add(context)
            session.commit()
            ctx_id = context.id

        # Use raw sqlite connection to bypass FK constraints
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO ref_facts (file_id, unit_id, token_text, start_line, start_col, end_line, end_col, role, ref_tier, certainty)
            VALUES (999, ?, 'orphan', 1, 0, 1, 6, 'reference', 'unknown', 'certain')
            """,
            (ctx_id,),
        )
        conn.commit()
        conn.close()

        checker = IntegrityChecker(db, tmp_path)
        report = checker.verify()

        assert not report.passed
        assert any(i.category == "fk_violation" and i.table == "ref_facts" for i in report.issues)

    def test_detects_orphan_defs(self, tmp_path: Path) -> None:
        """Should detect defs pointing to non-existent files."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        # First create a valid context
        with db.session() as session:
            context = Context(
                name="test",
                language_family="python",
                root_path="src",
            )
            session.add(context)
            session.commit()
            ctx_id = context.id

        # Use raw sqlite connection to bypass FK constraints
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO def_facts (def_uid, file_id, unit_id, kind, name, lexical_path, start_line, start_col, end_line, end_col)
            VALUES ('orphan_def', 999, ?, 'function', 'orphan', 'orphan', 1, 0, 1, 10)
            """,
            (ctx_id,),
        )
        conn.commit()
        conn.close()

        checker = IntegrityChecker(db, tmp_path)
        report = checker.verify()

        assert not report.passed
        assert any(i.category == "fk_violation" and i.table == "def_facts" for i in report.issues)

    def test_detects_missing_files(self, tmp_path: Path) -> None:
        """Should detect files in DB that don't exist on disk."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        # Create a file record without the actual file
        with db.session() as session:
            file = File(path="src/missing.py", language_family="python")
            session.add(file)
            session.commit()

        checker = IntegrityChecker(db, tmp_path)
        report = checker.verify()

        assert not report.passed
        assert any(i.category == "missing_file" for i in report.issues)

    def test_detects_tantivy_mismatch(self, tmp_path: Path) -> None:
        """Should detect when Tantivy and SQLite counts differ significantly."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"
        db = Database(db_path)
        db.create_all()

        # Create files in SQLite
        with db.session() as session:
            for i in range(20):
                file = File(path=f"src/file{i}.py", language_family="python")
                session.add(file)
            session.commit()

        # Create the files on disk
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        for i in range(20):
            (src_dir / f"file{i}.py").write_text(f"# file {i}")

        # Create Tantivy with fewer documents
        lexical = LexicalIndex(tantivy_path)
        lexical.add_file("src/file0.py", "# file 0", context_id=1)
        lexical.reload()  # Ensure searcher sees the new document

        # Verify tantivy has the document
        assert lexical.doc_count() == 1

        checker = IntegrityChecker(db, tmp_path, lexical)
        report = checker.verify()

        assert not report.passed
        assert any(i.category == "tantivy_mismatch" for i in report.issues)
        assert report.sqlite_file_count == 20
        assert report.tantivy_doc_count == 1


class TestIndexRecovery:
    """Tests for IndexRecovery."""

    def test_wipe_all_clears_sqlite(self, tmp_path: Path) -> None:
        """Wipe should clear all SQLite tables."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"
        db = Database(db_path)
        db.create_all()

        # Add some data
        with db.session() as session:
            context = Context(
                name="test",
                language_family="python",
                root_path="src",
            )
            session.add(context)
            file = File(path="src/main.py", language_family="python")
            session.add(file)
            session.commit()

        # Verify data exists
        with db.session() as session:
            count = session.execute(text("SELECT COUNT(*) FROM files")).scalar()
            assert count == 1

        # Wipe
        recovery = IndexRecovery(db, tantivy_path)
        recovery.wipe_all()

        # Verify data is gone but tables exist
        with db.session() as session:
            count = session.execute(text("SELECT COUNT(*) FROM files")).scalar()
            assert count == 0

    def test_wipe_all_removes_tantivy(self, tmp_path: Path) -> None:
        """Wipe should remove Tantivy index directory."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"
        db = Database(db_path)
        db.create_all()

        # Create Tantivy index (add_file commits automatically)
        lexical = LexicalIndex(tantivy_path)
        lexical.add_file("test.py", "content", context_id=1)
        assert tantivy_path.exists()

        # Wipe
        recovery = IndexRecovery(db, tantivy_path)
        recovery.wipe_all()

        assert not tantivy_path.exists()


class TestIntegrityReport:
    """Tests for IntegrityReport."""

    def test_starts_passing(self) -> None:
        """Report starts in passing state."""
        report = IntegrityReport(passed=True)
        assert report.passed
        assert len(report.issues) == 0

    def test_add_issue_marks_failed(self) -> None:
        """Adding an issue marks report as failed."""
        report = IntegrityReport(passed=True)
        from codeplane.index._internal.db import IntegrityIssue

        report.add_issue(
            IntegrityIssue(
                category="test",
                table="test_table",
                message="test issue",
                count=1,
            )
        )

        assert not report.passed
        assert len(report.issues) == 1
