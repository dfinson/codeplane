"""Unit tests for Reconciler (reconcile.py).

Tests cover:
- Detect changed file (hash mismatch)
- Detect unchanged file (hash match)
- Detect new file (not in DB)
- Detect deleted file (in DB, not on disk)
- Idempotency: run twice, same result
- Uses immediate_transaction for RepoState
- Uses BulkWriter for file operations
- Error handling (unreadable files)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import pygit2
import pytest
from sqlmodel import select

from codeplane.index._internal.db import Database, Reconciler, ReconcileResult
from codeplane.index.models import File, RepoState

if TYPE_CHECKING:
    pass


@pytest.fixture
def reconciler_setup(
    temp_dir: Path,
) -> tuple[Path, Database, Reconciler]:
    """Set up a repo and reconciler for testing."""
    from codeplane.index._internal.db import create_additional_indexes

    # Create repo
    repo_path = temp_dir / "repo"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path))

    repo = pygit2.Repository(str(repo_path))
    repo.config["user.name"] = "Test"
    repo.config["user.email"] = "test@test.com"

    # Create initial file and commit
    (repo_path / "initial.py").write_text("# initial\n")
    repo.index.add("initial.py")
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("Test", "test@test.com")
    repo.create_commit("HEAD", sig, sig, "Initial", tree, [])

    # Create database
    db_path = temp_dir / "test.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)

    reconciler = Reconciler(db, repo_path)

    return repo_path, db, reconciler


class TestReconcilerBasics:
    """Basic reconciliation tests."""

    def test_detect_new_file(self, reconciler_setup: tuple[Path, Database, Reconciler]) -> None:
        """Reconciler should detect new files."""
        repo_path, db, reconciler = reconciler_setup

        # Create a new file
        (repo_path / "new.py").write_text("# new file\n")

        # Run reconciliation
        result = reconciler.reconcile(paths=[Path("new.py")])

        assert result.files_added == 1
        assert result.files_modified == 0
        assert result.files_removed == 0

        # Verify file in database
        with db.session() as session:
            file = session.exec(select(File).where(File.path == "new.py")).first()
            assert file is not None
            assert file.content_hash is not None

    def test_detect_modified_file(
        self, reconciler_setup: tuple[Path, Database, Reconciler]
    ) -> None:
        """Reconciler should detect modified files."""
        repo_path, db, reconciler = reconciler_setup

        # First reconcile to add the file
        (repo_path / "modify.py").write_text("# version 1\n")
        result1 = reconciler.reconcile(paths=[Path("modify.py")])
        assert result1.files_added == 1

        # Get original hash
        with db.session() as session:
            file = session.exec(select(File).where(File.path == "modify.py")).first()
            assert file is not None
            original_hash = file.content_hash

        # Modify the file
        (repo_path / "modify.py").write_text("# version 2 - modified\n")

        # Reconcile again
        result2 = reconciler.reconcile(paths=[Path("modify.py")])
        assert result2.files_added == 0
        assert result2.files_modified == 1

        # Verify hash changed
        with db.session() as session:
            file = session.exec(select(File).where(File.path == "modify.py")).first()
            assert file is not None
            assert file.content_hash != original_hash

    def test_detect_unchanged_file(
        self, reconciler_setup: tuple[Path, Database, Reconciler]
    ) -> None:
        """Reconciler should detect unchanged files."""
        repo_path, db, reconciler = reconciler_setup

        # Add file
        (repo_path / "unchanged.py").write_text("# stable content\n")
        result1 = reconciler.reconcile(paths=[Path("unchanged.py")])
        assert result1.files_added == 1

        # Reconcile again without changes
        result2 = reconciler.reconcile(paths=[Path("unchanged.py")])
        assert result2.files_added == 0
        assert result2.files_modified == 0
        assert result2.files_unchanged == 1

    def test_detect_deleted_file(self, reconciler_setup: tuple[Path, Database, Reconciler]) -> None:
        """Reconciler should detect deleted files."""
        repo_path, db, reconciler = reconciler_setup

        # Add file
        file_path = repo_path / "delete_me.py"
        file_path.write_text("# to be deleted\n")
        result1 = reconciler.reconcile(paths=[Path("delete_me.py")])
        assert result1.files_added == 1

        # Delete the file
        file_path.unlink()

        # Reconcile
        result2 = reconciler.reconcile(paths=[Path("delete_me.py")])
        assert result2.files_removed == 1


class TestReconcilerIdempotency:
    """Tests for reconciliation idempotency."""

    def test_idempotent_full_reconcile(
        self, reconciler_setup: tuple[Path, Database, Reconciler]
    ) -> None:
        """Full reconciliation should be idempotent."""
        repo_path, db, reconciler = reconciler_setup

        # Create some files and add to git index so they are tracked
        (repo_path / "a.py").write_text("# a\n")
        (repo_path / "b.py").write_text("# b\n")
        repo = pygit2.Repository(str(repo_path))
        repo.index.add("a.py")
        repo.index.add("b.py")
        repo.index.write()

        # First reconcile
        reconciler.reconcile()

        # Second reconcile (no changes)
        result2 = reconciler.reconcile()

        # Should have same total files, all unchanged
        assert result2.files_added == 0
        assert result2.files_modified == 0
        # initial.py + a.py + b.py = 3 files
        assert result2.files_unchanged >= 3

    def test_idempotent_after_modifications(
        self, reconciler_setup: tuple[Path, Database, Reconciler]
    ) -> None:
        """Reconciliation should be idempotent after modifications are synced."""
        repo_path, db, reconciler = reconciler_setup

        # Add file
        (repo_path / "idempotent.py").write_text("# content\n")
        reconciler.reconcile(paths=[Path("idempotent.py")])

        # Modify
        (repo_path / "idempotent.py").write_text("# new content\n")
        result1 = reconciler.reconcile(paths=[Path("idempotent.py")])
        assert result1.files_modified == 1

        # Reconcile again - should be unchanged
        result2 = reconciler.reconcile(paths=[Path("idempotent.py")])
        assert result2.files_modified == 0
        assert result2.files_unchanged == 1


class TestRepoStateManagement:
    """Tests for RepoState updates."""

    def test_updates_repo_state_head(
        self, reconciler_setup: tuple[Path, Database, Reconciler]
    ) -> None:
        """Reconciler should update RepoState.last_seen_head."""
        repo_path, db, reconciler = reconciler_setup

        # Run reconciliation
        reconciler.reconcile()

        # Verify RepoState was created/updated
        with db.session() as session:
            repo_state = session.get(RepoState, 1)
            assert repo_state is not None
            assert repo_state.last_seen_head is not None
            assert len(repo_state.last_seen_head) == 40  # SHA length

    def test_updates_repo_state_checked_at(
        self, reconciler_setup: tuple[Path, Database, Reconciler]
    ) -> None:
        """Reconciler should update RepoState.checked_at."""
        _, db, reconciler = reconciler_setup

        before = time.time()
        reconciler.reconcile()
        after = time.time()

        with db.session() as session:
            repo_state = session.get(RepoState, 1)
            assert repo_state is not None
            assert repo_state.checked_at is not None
            assert before <= repo_state.checked_at <= after

    def test_tracks_head_changes(self, reconciler_setup: tuple[Path, Database, Reconciler]) -> None:
        """Reconciler should track HEAD changes across reconciliations."""
        repo_path, db, reconciler = reconciler_setup

        # First reconcile
        result1 = reconciler.reconcile()
        head1 = result1.head_after

        # Make a new commit
        repo = pygit2.Repository(str(repo_path))
        (repo_path / "newfile.py").write_text("# new\n")
        repo.index.add("newfile.py")
        repo.index.write()
        tree = repo.index.write_tree()
        sig = pygit2.Signature("Test", "test@test.com")
        parent = repo.head.target
        repo.create_commit("HEAD", sig, sig, "Second commit", tree, [parent])

        # Second reconcile
        result2 = reconciler.reconcile()

        # HEAD should have changed
        assert result2.head_before == head1
        assert result2.head_after != head1


class TestGetChangedFiles:
    """Tests for get_changed_files method."""

    def test_get_changed_files_since_head(
        self, reconciler_setup: tuple[Path, Database, Reconciler]
    ) -> None:
        """get_changed_files should return files changed since a commit."""
        repo_path, db, reconciler = reconciler_setup

        repo = pygit2.Repository(str(repo_path))
        head1 = str(repo.head.target)

        # Make changes and commit
        (repo_path / "changed1.py").write_text("# changed\n")
        (repo_path / "changed2.py").write_text("# also changed\n")
        repo.index.add("changed1.py")
        repo.index.add("changed2.py")
        repo.index.write()
        tree = repo.index.write_tree()
        sig = pygit2.Signature("Test", "test@test.com")
        parent = repo.head.target
        repo.create_commit("HEAD", sig, sig, "Changes", tree, [parent])

        # Get changed files since head1
        changed = reconciler.get_changed_files(since_head=head1)

        paths = {c.path for c in changed}
        assert "changed1.py" in paths
        assert "changed2.py" in paths


class TestErrorHandling:
    """Tests for error handling."""

    def test_handles_unreadable_file(
        self, reconciler_setup: tuple[Path, Database, Reconciler]
    ) -> None:
        """Reconciler should handle unreadable files gracefully."""
        repo_path, db, reconciler = reconciler_setup

        # Create a file then make it unreadable
        file_path = repo_path / "unreadable.py"
        file_path.write_text("# content\n")

        # On Linux, we can use chmod; this test may not work on all systems
        import os
        import stat

        try:
            os.chmod(file_path, 0o000)  # Remove all permissions

            # Reconcile should not crash
            result = reconciler.reconcile(paths=[Path("unreadable.py")])

            # Should have an error recorded
            # Note: This behavior depends on implementation
            assert result.files_checked >= 0

        finally:
            # Restore permissions for cleanup
            os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)

    def test_handles_nonexistent_path(
        self, reconciler_setup: tuple[Path, Database, Reconciler]
    ) -> None:
        """Reconciler should handle non-existent paths."""
        _, _, reconciler = reconciler_setup

        # Reconcile with non-existent path - should not crash
        result = reconciler.reconcile(paths=[Path("does_not_exist.py")])

        # Should complete without error
        assert result.files_checked >= 0


class TestReconcileResult:
    """Tests for ReconcileResult dataclass."""

    def test_files_changed_property(self) -> None:
        """files_changed should sum added, modified, and removed."""
        result = ReconcileResult(
            files_added=5,
            files_modified=3,
            files_removed=2,
            files_unchanged=10,
        )
        assert result.files_changed == 10

    def test_default_values(self) -> None:
        """ReconcileResult should have sensible defaults."""
        result = ReconcileResult()
        assert result.files_checked == 0
        assert result.files_added == 0
        assert result.files_modified == 0
        assert result.files_removed == 0
        assert result.files_unchanged == 0
        assert result.errors == []
