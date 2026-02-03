"""Filesystem reconciliation for change detection.

The Reconciler compares current file content hashes against stored hashes
and marks files for reindexing. It is the entry point for change detection.

CRITICAL INVARIANT: Reconcile must be serialized by the Coordinator.
Only ONE reconcile() call may execute at a time to prevent RepoState corruption.

Database access patterns:
- Uses db.immediate_transaction() for RepoState (serializable writes)
- Uses BulkWriter for file upserts (high volume)
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from codeplane.git import GitOps
from codeplane.index.models import File, Freshness, RepoState

if TYPE_CHECKING:
    from codeplane.index._internal.db.database import Database


@dataclass
class ChangedFile:
    """A file that changed between reconciliations."""

    path: str
    old_hash: str | None
    new_hash: str
    change_type: str  # 'added', 'modified', 'deleted'


@dataclass
class ReconcileResult:
    """Result of a reconciliation operation."""

    files_checked: int = 0
    files_added: int = 0
    files_modified: int = 0
    files_removed: int = 0
    files_unchanged: int = 0
    head_before: str | None = None
    head_after: str | None = None
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)
    cplignore_changed: bool = False  # True if .cplignore was modified

    @property
    def files_changed(self) -> int:
        """Total files that changed."""
        return self.files_added + self.files_modified + self.files_removed


class Reconciler:
    """Filesystem reconciliation service. INVARIANT: Caller must hold reconcile_lock."""

    def __init__(self, db: Database, repo_root: Path) -> None:
        self.db = db
        self.repo_root = repo_root
        self._git: GitOps | None = None

    @property
    def cplignore_path(self) -> Path:
        """Legacy: primary .cplignore path (for backward compat)."""
        return self.repo_root / ".codeplane" / ".cplignore"

    @property
    def git(self) -> GitOps:
        """Lazily open the git repository."""
        if self._git is None:
            self._git = GitOps(self.repo_root)
        return self._git

    def reconcile(self, paths: list[Path] | None = None) -> ReconcileResult:
        """
        Compare file content hashes and mark changed files.

        If paths is None, reconcile all tracked files in the repository.
        If paths is provided, only reconcile those specific files.

        Also detects .cplignore changes (ANY .cplignore file anywhere in repo)
        and sets cplignore_changed flag to trigger full reindex.

        Uses immediate_transaction for RepoState to prevent race conditions.
        Uses BulkWriter for file operations for performance.

        Args:
            paths: Optional list of paths to reconcile. If None, full reconcile.

        Returns:
            ReconcileResult with statistics about the reconciliation.
        """
        start_time = time.perf_counter()
        result = ReconcileResult()

        # Get current HEAD
        current_head = self._get_git_head()
        result.head_after = current_head

        # Check for .cplignore changes
        current_cplignore_hash = self._compute_cplignore_hash()

        # Update RepoState atomically with immediate transaction
        with self.db.immediate_transaction() as session:
            repo_state = session.get(RepoState, 1)
            if repo_state is None:
                repo_state = RepoState(id=1)
                session.add(repo_state)

            result.head_before = repo_state.last_seen_head
            repo_state.last_seen_head = current_head
            repo_state.checked_at = time.time()

            # Detect .cplignore change
            if repo_state.cplignore_hash != current_cplignore_hash:
                result.cplignore_changed = True
                repo_state.cplignore_hash = current_cplignore_hash

        # Determine which files to check
        if paths is None:
            files_to_check = self._get_all_tracked_files()
        else:
            files_to_check = [self._normalize_path(p) for p in paths]

        # Get current hashes from database
        db_hashes = self._get_db_hashes(files_to_check)

        # Compute current hashes and detect changes
        added: list[dict[str, str | float | None]] = []
        modified: list[dict[str, str | float | None]] = []
        removed_paths: list[str] = []

        for rel_path in files_to_check:
            abs_path = self.repo_root / rel_path
            result.files_checked += 1

            try:
                if not abs_path.exists():
                    if rel_path in db_hashes:
                        removed_paths.append(rel_path)
                        result.files_removed += 1
                    continue

                content_hash = self._compute_hash(abs_path)
                old_hash = db_hashes.get(rel_path)

                if old_hash is None:
                    # New file
                    added.append(
                        {
                            "path": rel_path,
                            "content_hash": content_hash,
                            "language": self._detect_language(rel_path),
                            "indexed_at": None,
                        }
                    )
                    result.files_added += 1
                elif old_hash != content_hash:
                    # Modified file
                    modified.append(
                        {
                            "path": rel_path,
                            "content_hash": content_hash,
                            "indexed_at": None,
                        }
                    )
                    result.files_modified += 1
                else:
                    result.files_unchanged += 1

            except OSError as e:
                result.errors.append(f"Error reading {rel_path}: {e}")

        # Apply changes via BulkWriter
        with self.db.bulk_writer() as writer:
            # Insert new files
            if added:
                writer.insert_many(File, added)

            # Update modified files
            if modified:
                writer.upsert_many(
                    File,
                    modified,
                    conflict_columns=["path"],
                    update_columns=["content_hash", "indexed_at"],
                )

            # Remove deleted files (CASCADE deletes dependent facts automatically)
            if removed_paths:
                placeholders = ", ".join(f":p{i}" for i in range(len(removed_paths)))
                params = {f"p{i}": p for i, p in enumerate(removed_paths)}
                writer.delete_where(File, f"path IN ({placeholders})", params)

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    def get_changed_files(self, since_head: str | None = None) -> list[ChangedFile]:
        """
        Get list of files changed since given HEAD.

        Args:
            since_head: Git commit hash to compare against.
                        If None, uses last_seen_head from RepoState.

        Returns:
            List of ChangedFile objects describing changes.
        """
        if since_head is None:
            with self.db.session() as session:
                repo_state = session.get(RepoState, 1)
                since_head = repo_state.last_seen_head if repo_state else None

        if since_head is None:
            return []

        current_head = self._get_git_head()
        if current_head == since_head:
            return []

        return self._get_git_diff(since_head, current_head)

    def get_file_state(self, path: str) -> Freshness:
        """
        Get the freshness state of a single file.

        Args:
            path: Relative path to file

        Returns:
            Freshness state
        """
        with self.db.session() as session:
            from sqlmodel import select

            stmt = select(File).where(File.path == path)
            db_file = session.exec(stmt).first()

            if db_file is None:
                return Freshness.UNINDEXED

            abs_path = self.repo_root / path
            if not abs_path.exists():
                return Freshness.DIRTY

            current_hash = self._compute_hash(abs_path)
            if current_hash != db_file.content_hash:
                return Freshness.DIRTY

            if db_file.indexed_at is None:
                return Freshness.UNINDEXED

            return Freshness.CLEAN

    def _get_git_head(self) -> str:
        """Get current HEAD commit hash."""
        return self.git.head().target_sha

    def _compute_cplignore_hash(self) -> str | None:
        """Compute combined hash of ALL .cplignore files in repo.

        Uses IgnoreChecker to find all .cplignore files hierarchically,
        then computes a combined hash. Any change to any .cplignore file
        will change this hash, triggering a full reindex.

        Returns None if no .cplignore files exist.
        """
        from codeplane.index._internal.ignore import IgnoreChecker

        # Create a fresh IgnoreChecker to discover all .cplignore files
        # We don't need the patterns - just the file discovery
        checker = IgnoreChecker(self.repo_root)
        return checker.compute_combined_hash()

    def _get_all_tracked_files(self) -> list[str]:
        """Get all files tracked by git."""
        return self.git.tracked_files()

    def _get_db_hashes(self, paths: list[str]) -> dict[str, str]:
        """Get content hashes from database for given paths."""
        if not paths:
            return {}

        with self.db.session() as session:
            from sqlmodel import col, select

            stmt = select(File.path, File.content_hash).where(col(File.path).in_(paths))
            results = session.exec(stmt)
            return {row[0]: row[1] for row in results if row[1] is not None}

    def _compute_hash(self, path: Path) -> str:
        """Compute SHA-256 hash of file content."""
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _normalize_path(self, path: Path) -> str:
        """Normalize path to relative POSIX format."""
        if path.is_absolute():
            path = path.relative_to(self.repo_root)
        return str(path).replace("\\", "/")

    def _detect_language(self, path: str) -> str | None:
        """Detect language from file extension."""
        ext_map = {
            ".py": "python",
            ".pyi": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".kt": "kotlin",
            ".scala": "scala",
            ".cs": "csharp",
            ".fs": "fsharp",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
            ".ex": "elixir",
            ".exs": "elixir",
            ".hs": "haskell",
            ".tf": "terraform",
            ".sql": "sql",
            ".md": "markdown",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".proto": "protobuf",
            ".graphql": "graphql",
            ".gql": "graphql",
            ".nix": "nix",
        }
        ext = Path(path).suffix.lower()
        return ext_map.get(ext)

    def _get_git_diff(self, from_commit: str, to_commit: str) -> list[ChangedFile]:
        """Get files changed between two commits using git diff."""
        changed: list[ChangedFile] = []

        try:
            diff_info = self.git.diff(base=from_commit, target=to_commit)

            for diff_file in diff_info.files:
                status = diff_file.status
                if status == "added":
                    change_type = "added"
                    old_hash = None
                    new_hash = ""
                elif status == "deleted":
                    change_type = "deleted"
                    old_hash = ""
                    new_hash = ""
                elif status in ("modified", "renamed"):
                    change_type = "modified"
                    old_hash = ""
                    new_hash = ""
                else:
                    continue

                path = diff_file.new_path or diff_file.old_path
                if path is not None:
                    changed.append(
                        ChangedFile(
                            path=path,
                            old_hash=old_hash,
                            new_hash=new_hash,
                            change_type=change_type,
                        )
                    )

        except Exception as e:
            # Invalid commit reference
            raise ValueError(f"Invalid commit reference: {e}") from e

        return changed
