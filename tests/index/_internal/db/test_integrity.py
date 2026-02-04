"""Tests for index integrity verification and recovery."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from codeplane.index._internal.db.integrity import (
    IndexRecovery,
    IntegrityChecker,
    IntegrityIssue,
    IntegrityReport,
)


class TestIntegrityIssue:
    """Tests for IntegrityIssue dataclass."""

    def test_integrity_issue_construction(self) -> None:
        """IntegrityIssue holds issue details."""
        issue = IntegrityIssue(
            category="fk_violation",
            table="ref_facts",
            message="orphan references",
            count=5,
        )
        assert issue.category == "fk_violation"
        assert issue.table == "ref_facts"
        assert issue.message == "orphan references"
        assert issue.count == 5

    def test_integrity_issue_default_count(self) -> None:
        """IntegrityIssue defaults count to 1."""
        issue = IntegrityIssue(
            category="missing_file",
            table="files",
            message="file not found",
        )
        assert issue.count == 1

    def test_integrity_issue_optional_table(self) -> None:
        """IntegrityIssue allows None table."""
        issue = IntegrityIssue(
            category="tantivy_mismatch",
            table=None,
            message="count mismatch",
        )
        assert issue.table is None


class TestIntegrityReport:
    """Tests for IntegrityReport dataclass."""

    def test_integrity_report_defaults(self) -> None:
        """IntegrityReport starts with passed=True and empty issues."""
        report = IntegrityReport(passed=True)
        assert report.passed is True
        assert report.issues == []
        assert report.files_checked == 0
        assert report.refs_checked == 0
        assert report.tantivy_doc_count == 0
        assert report.sqlite_file_count == 0

    def test_add_issue_marks_failed(self) -> None:
        """add_issue appends issue and sets passed=False."""
        report = IntegrityReport(passed=True)
        issue = IntegrityIssue(
            category="test",
            table="test_table",
            message="test message",
        )
        report.add_issue(issue)
        assert report.passed is False
        assert len(report.issues) == 1
        assert report.issues[0] is issue

    def test_add_multiple_issues(self) -> None:
        """Multiple issues can be added."""
        report = IntegrityReport(passed=True)
        for i in range(3):
            report.add_issue(IntegrityIssue(category=f"cat_{i}", table=None, message=f"msg_{i}"))
        assert len(report.issues) == 3
        assert report.passed is False


class TestIntegrityChecker:
    """Tests for IntegrityChecker."""

    def test_init_stores_dependencies(self) -> None:
        """IntegrityChecker stores db, repo_root, and lexical."""
        mock_db = MagicMock()
        repo_root = Path("/test/repo")
        mock_lexical = MagicMock()

        checker = IntegrityChecker(mock_db, repo_root, mock_lexical)
        assert checker._db is mock_db
        assert checker._repo_root == repo_root
        assert checker._lexical is mock_lexical

    def test_init_lexical_optional(self) -> None:
        """IntegrityChecker works without lexical index."""
        mock_db = MagicMock()
        checker = IntegrityChecker(mock_db, Path("/test"))
        assert checker._lexical is None

    def test_verify_returns_report(self) -> None:
        """verify() returns IntegrityReport."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_execute = MagicMock()
        mock_execute.scalar.return_value = 0
        mock_session.execute.return_value = mock_execute
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmp:
            checker = IntegrityChecker(mock_db, Path(tmp))
            report = checker.verify()
            assert isinstance(report, IntegrityReport)

    def test_check_foreign_keys_detects_orphan_refs(self) -> None:
        """_check_foreign_keys detects orphan ref_facts."""
        mock_db = MagicMock()
        mock_session = MagicMock()

        # First call for ref_facts returns 5 orphans
        # Rest return 0
        call_count = [0]

        def scalar_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return 5  # orphan refs
            return 0

        mock_result = MagicMock()
        mock_result.scalar.side_effect = scalar_side_effect
        mock_session.execute.return_value = mock_result

        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        report = IntegrityReport(passed=True)
        checker = IntegrityChecker(mock_db, Path("/test"))
        checker._check_foreign_keys(report)

        assert report.passed is False
        assert len(report.issues) == 1
        assert report.issues[0].category == "fk_violation"
        assert report.issues[0].table == "ref_facts"
        assert report.issues[0].count == 5

    def test_check_foreign_keys_detects_orphan_defs(self) -> None:
        """_check_foreign_keys detects orphan def_facts."""
        mock_db = MagicMock()
        mock_session = MagicMock()

        call_count = [0]

        def scalar_side_effect():
            call_count[0] += 1
            if call_count[0] == 2:
                return 3  # orphan defs
            return 0

        mock_result = MagicMock()
        mock_result.scalar.side_effect = scalar_side_effect
        mock_session.execute.return_value = mock_result

        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        report = IntegrityReport(passed=True)
        checker = IntegrityChecker(mock_db, Path("/test"))
        checker._check_foreign_keys(report)

        assert len(report.issues) == 1
        assert report.issues[0].table == "def_facts"
        assert report.issues[0].count == 3

    def test_check_files_exist_detects_missing(self) -> None:
        """_check_files_exist detects files missing from disk."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = lambda _self: iter([("missing.py",), ("also_missing.py",)])
        mock_session.execute.return_value = mock_result

        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmp:
            report = IntegrityReport(passed=True)
            checker = IntegrityChecker(mock_db, Path(tmp))
            checker._check_files_exist(report)

            assert report.passed is False
            assert report.files_checked == 2
            assert len(report.issues) == 1
            assert report.issues[0].category == "missing_file"
            assert report.issues[0].count == 2

    def test_check_files_exist_passes_when_all_exist(self) -> None:
        """_check_files_exist passes when all files exist."""
        mock_db = MagicMock()
        mock_session = MagicMock()

        with tempfile.TemporaryDirectory() as tmp:
            # Create test file
            test_file = Path(tmp) / "exists.py"
            test_file.write_text("# exists")

            mock_result = MagicMock()
            mock_result.__iter__ = lambda _self: iter([("exists.py",)])
            mock_session.execute.return_value = mock_result

            mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

            report = IntegrityReport(passed=True)
            checker = IntegrityChecker(mock_db, Path(tmp))
            checker._check_files_exist(report)

            assert report.passed is True
            assert report.files_checked == 1
            assert len(report.issues) == 0

    def test_check_tantivy_sync_skips_without_lexical(self) -> None:
        """_check_tantivy_sync does nothing without lexical index."""
        mock_db = MagicMock()
        report = IntegrityReport(passed=True)
        checker = IntegrityChecker(mock_db, Path("/test"), lexical=None)
        checker._check_tantivy_sync(report)

        assert report.passed is True
        assert len(report.issues) == 0

    def test_check_tantivy_sync_detects_mismatch(self) -> None:
        """_check_tantivy_sync detects large doc count mismatch."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 100  # SQLite count
        mock_session.execute.return_value = mock_result

        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        mock_lexical = MagicMock()
        mock_lexical.doc_count.return_value = 50  # Tantivy count - big mismatch

        report = IntegrityReport(passed=True)
        checker = IntegrityChecker(mock_db, Path("/test"), lexical=mock_lexical)
        checker._check_tantivy_sync(report)

        assert report.passed is False
        assert report.sqlite_file_count == 100
        assert report.tantivy_doc_count == 50
        assert len(report.issues) == 1
        assert report.issues[0].category == "tantivy_mismatch"

    def test_check_tantivy_sync_tolerates_small_difference(self) -> None:
        """_check_tantivy_sync allows small differences."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 100  # SQLite count
        mock_session.execute.return_value = mock_result

        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        mock_lexical = MagicMock()
        mock_lexical.doc_count.return_value = 98  # Small difference ok

        report = IntegrityReport(passed=True)
        checker = IntegrityChecker(mock_db, Path("/test"), lexical=mock_lexical)
        checker._check_tantivy_sync(report)

        assert report.passed is True
        assert len(report.issues) == 0


class TestIndexRecovery:
    """Tests for IndexRecovery."""

    def test_init_stores_dependencies(self) -> None:
        """IndexRecovery stores db and tantivy_path."""
        mock_db = MagicMock()
        tantivy_path = Path("/test/tantivy")
        recovery = IndexRecovery(mock_db, tantivy_path)
        assert recovery._db is mock_db
        assert recovery._tantivy_path == tantivy_path

    def test_wipe_all_calls_both_wipes(self) -> None:
        """wipe_all wipes both SQLite and Tantivy."""
        mock_db = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            tantivy_path = Path(tmp) / "tantivy"
            tantivy_path.mkdir()
            (tantivy_path / "index.file").write_text("data")

            recovery = IndexRecovery(mock_db, tantivy_path)
            recovery.wipe_all()

            mock_db.drop_all.assert_called_once()
            mock_db.create_all.assert_called_once()
            assert not tantivy_path.exists()

    def test_wipe_sqlite_drops_and_recreates(self) -> None:
        """_wipe_sqlite drops and recreates tables."""
        mock_db = MagicMock()
        recovery = IndexRecovery(mock_db, Path("/test"))
        recovery._wipe_sqlite()

        mock_db.drop_all.assert_called_once()
        mock_db.create_all.assert_called_once()

    def test_wipe_tantivy_removes_directory(self) -> None:
        """_wipe_tantivy removes tantivy directory."""
        mock_db = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            tantivy_path = Path(tmp) / "tantivy"
            tantivy_path.mkdir()
            (tantivy_path / "test.idx").write_text("index data")

            recovery = IndexRecovery(mock_db, tantivy_path)
            recovery._wipe_tantivy()

            assert not tantivy_path.exists()

    def test_wipe_tantivy_handles_missing_directory(self) -> None:
        """_wipe_tantivy handles non-existent directory gracefully."""
        mock_db = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            tantivy_path = Path(tmp) / "nonexistent"
            recovery = IndexRecovery(mock_db, tantivy_path)
            # Should not raise
            recovery._wipe_tantivy()
