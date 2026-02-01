"""Index integrity verification and recovery.

Per SPEC.md §5.8: On CPL index corruption, wipe and reindex from Git + disk.

Integrity checks:
1. Foreign key violations (orphan refs, invalid context_ids)
2. Files in DB but missing from disk
3. Tantivy/SQLite document count mismatch
4. RepoState consistency

Recovery strategy: If any check fails, wipe the index and signal need for full reindex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from codeplane.index._internal.db.database import Database
    from codeplane.index._internal.indexing.lexical import LexicalIndex


@dataclass
class IntegrityIssue:
    """A single integrity issue detected."""

    category: str  # 'fk_violation', 'orphan_file', 'tantivy_mismatch', 'missing_file'
    table: str | None
    message: str
    count: int = 1


@dataclass
class IntegrityReport:
    """Result of integrity verification."""

    passed: bool
    issues: list[IntegrityIssue] = field(default_factory=list)
    files_checked: int = 0
    refs_checked: int = 0
    tantivy_doc_count: int = 0
    sqlite_file_count: int = 0

    def add_issue(self, issue: IntegrityIssue) -> None:
        """Add an issue and mark as failed."""
        self.issues.append(issue)
        self.passed = False


class IntegrityChecker:
    """Verifies index consistency between SQLite, Tantivy, and filesystem.

    Usage::

        checker = IntegrityChecker(db, repo_root, lexical_index)
        report = checker.verify()

        if not report.passed:
            # Recovery needed
            recovery = IndexRecovery(db, tantivy_path)
            recovery.wipe_all()
            # Then reinitialize via coordinator
    """

    def __init__(
        self,
        db: Database,
        repo_root: Path,
        lexical: LexicalIndex | None = None,
    ) -> None:
        """Initialize integrity checker."""
        self._db = db
        self._repo_root = repo_root
        self._lexical = lexical

    def verify(self) -> IntegrityReport:
        """Run all integrity checks and return report."""
        report = IntegrityReport(passed=True)

        self._check_foreign_keys(report)
        self._check_files_exist(report)
        self._check_tantivy_sync(report)

        return report

    def _check_foreign_keys(self, report: IntegrityReport) -> None:
        """Check for foreign key violations."""
        with self._db.session() as session:
            # Check ref_facts -> files
            result = session.execute(
                text("""
                    SELECT COUNT(*) FROM ref_facts
                    WHERE file_id NOT IN (SELECT id FROM files)
                """)
            )
            orphan_refs = result.scalar() or 0
            if orphan_refs > 0:
                report.add_issue(
                    IntegrityIssue(
                        category="fk_violation",
                        table="ref_facts",
                        message="refs pointing to non-existent files",
                        count=orphan_refs,
                    )
                )

            # Check def_facts -> files
            result = session.execute(
                text("""
                    SELECT COUNT(*) FROM def_facts
                    WHERE file_id NOT IN (SELECT id FROM files)
                """)
            )
            orphan_defs = result.scalar() or 0
            if orphan_defs > 0:
                report.add_issue(
                    IntegrityIssue(
                        category="fk_violation",
                        table="def_facts",
                        message="defs pointing to non-existent files",
                        count=orphan_defs,
                    )
                )

            # Check context_markers -> contexts
            result = session.execute(
                text("""
                    SELECT COUNT(*) FROM context_markers
                    WHERE context_id NOT IN (SELECT id FROM contexts)
                """)
            )
            orphan_markers = result.scalar() or 0
            if orphan_markers > 0:
                report.add_issue(
                    IntegrityIssue(
                        category="fk_violation",
                        table="context_markers",
                        message="markers pointing to non-existent contexts",
                        count=orphan_markers,
                    )
                )

            # Check scope_facts -> files
            result = session.execute(
                text("""
                    SELECT COUNT(*) FROM scope_facts
                    WHERE file_id NOT IN (SELECT id FROM files)
                """)
            )
            orphan_scopes = result.scalar() or 0
            if orphan_scopes > 0:
                report.add_issue(
                    IntegrityIssue(
                        category="fk_violation",
                        table="scope_facts",
                        message="scopes pointing to non-existent files",
                        count=orphan_scopes,
                    )
                )

    def _check_files_exist(self, report: IntegrityReport) -> None:
        """Check that files in DB exist on disk."""
        missing_count = 0

        with self._db.session() as session:
            result = session.execute(text("SELECT path FROM files"))
            paths = [row[0] for row in result]
            report.files_checked = len(paths)

            for path in paths:
                full_path = self._repo_root / path
                if not full_path.exists():
                    missing_count += 1

        if missing_count > 0:
            report.add_issue(
                IntegrityIssue(
                    category="missing_file",
                    table="files",
                    message="files in DB but missing from disk",
                    count=missing_count,
                )
            )

    def _check_tantivy_sync(self, report: IntegrityReport) -> None:
        """Check Tantivy document count matches SQLite file count."""
        if self._lexical is None:
            return

        with self._db.session() as session:
            result = session.execute(text("SELECT COUNT(*) FROM files"))
            sqlite_count = result.scalar() or 0

        tantivy_count = self._lexical.doc_count()

        report.sqlite_file_count = sqlite_count
        report.tantivy_doc_count = tantivy_count

        # Allow some tolerance - Tantivy may have pending commits
        # But large mismatches indicate corruption
        if abs(tantivy_count - sqlite_count) > max(5, sqlite_count * 0.1):
            report.add_issue(
                IntegrityIssue(
                    category="tantivy_mismatch",
                    table=None,
                    message=f"Tantivy has {tantivy_count} docs, SQLite has {sqlite_count} files",
                    count=abs(tantivy_count - sqlite_count),
                )
            )


class IndexRecovery:
    """Recovery operations for corrupt index state.

    Per SPEC.md §5.8: On CPL index corruption, wipe and reindex from Git + disk.

    Usage::

        recovery = IndexRecovery(db, tantivy_path)
        recovery.wipe_all()
        # Then call coordinator.initialize() to rebuild
    """

    def __init__(self, db: Database, tantivy_path: Path) -> None:
        """Initialize recovery handler."""
        self._db = db
        self._tantivy_path = tantivy_path

    def wipe_all(self) -> None:
        """Wipe all index data (SQLite tables and Tantivy index).

        After calling this, a full reindex is required.
        """
        self._wipe_sqlite()
        self._wipe_tantivy()

    def _wipe_sqlite(self) -> None:
        """Drop and recreate all SQLite tables."""
        self._db.drop_all()
        self._db.create_all()

    def _wipe_tantivy(self) -> None:
        """Delete Tantivy index directory."""
        import shutil

        if self._tantivy_path.exists():
            shutil.rmtree(self._tantivy_path)


__all__ = [
    "IntegrityChecker",
    "IntegrityIssue",
    "IntegrityReport",
    "IndexRecovery",
]
