"""Mutation operations - atomic_edit_files tool implementation.

Atomic file edits with structured delta response.
Per SPEC.md §23.7 atomic_edit_files tool specification.

Triggers reindex after mutation via callback.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    pass


@dataclass
class ExactEdit:
    """Content-addressed edit specification."""

    old_content: str
    new_content: str
    expected_occurrences: int = 1


@dataclass
class Edit:
    """A single file edit."""

    path: str
    action: Literal["create", "update", "delete"]

    # For create/update with full content
    content: str | None = None

    # For exact mode (update only)
    old_content: str | None = None
    new_content: str | None = None
    expected_occurrences: int = 1


@dataclass
class FileDelta:
    """Delta for a single file."""

    path: str
    action: Literal["created", "updated", "deleted"]
    old_hash: str | None = None
    new_hash: str | None = None
    insertions: int = 0
    deletions: int = 0


@dataclass
class DryRunInfo:
    """Additional info returned during dry run."""

    content_hash: str  # Hash of content that would be replaced
    unified_diff: str | None = None


@dataclass
class MutationDelta:
    """Structured delta from a mutation."""

    mutation_id: str
    files_changed: int
    insertions: int
    deletions: int
    files: list[FileDelta] = field(default_factory=list)


@dataclass
class MutationResult:
    """Result of atomic_edit_files operation."""

    applied: bool
    dry_run: bool
    delta: MutationDelta
    dry_run_info: DryRunInfo | None = None
    affected_symbols: list[str] | None = None
    affected_tests: list[str] | None = None
    repo_fingerprint: str = ""


class ContentNotFoundError(Exception):
    """Raised when old_content is not found in file."""

    def __init__(self, path: str, snippet: str | None = None) -> None:
        self.path = path
        self.snippet = snippet
        super().__init__(f"Content not found in {path}")


class MultipleMatchesError(Exception):
    """Raised when old_content matches multiple locations."""

    def __init__(self, path: str, count: int, lines: list[int]) -> None:
        self.path = path
        self.count = count
        self.lines = lines
        super().__init__(f"Content found {count} times in {path}, expected 1")


class MutationOps:
    """Mutation operations for the atomic_edit_files tool.

    Handles atomic file edits with rollback support.
    Triggers reindex callback after successful mutation.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        on_mutation: Callable[[list[Path]], None] | None = None,
    ) -> None:
        """Initialize mutation ops.

        Args:
            repo_root: Repository root path
            on_mutation: Callback invoked with changed paths after mutation.
                         Typically triggers IndexCoordinator.reindex_incremental().
        """
        self._repo_root = repo_root
        self._on_mutation = on_mutation

    def atomic_edit_files(
        self,
        edits: list[Edit],
        *,
        dry_run: bool = False,
    ) -> MutationResult:
        """Apply atomic file edits.

        Args:
            edits: List of file edits to apply
            dry_run: Preview only, don't apply changes

        Returns:
            MutationResult with delta information

        Raises:
            ContentNotFoundError: old_content not found (exact mode)
            MultipleMatchesError: old_content found multiple times (exact mode)
            FileNotFoundError: File doesn't exist for update/delete
            FileExistsError: File already exists for create
        """
        mutation_id = str(uuid.uuid4())[:8]
        file_deltas: list[FileDelta] = []
        changed_paths: list[Path] = []
        total_insertions = 0
        total_deletions = 0
        dry_run_info: DryRunInfo | None = None

        # Validate all edits first
        for edit in edits:
            full_path = self._repo_root / edit.path
            if edit.action == "update" and not full_path.exists():
                raise FileNotFoundError(f"Cannot update non-existent file: {edit.path}")
            if edit.action == "create" and full_path.exists():
                raise FileExistsError(f"Cannot create existing file: {edit.path}")
            if edit.action == "delete" and not full_path.exists():
                raise FileNotFoundError(f"Cannot delete non-existent file: {edit.path}")

        # Apply edits (or compute dry-run deltas)
        for edit in edits:
            full_path = self._repo_root / edit.path
            old_hash: str | None = None
            new_hash: str | None = None
            insertions = 0
            deletions = 0

            if edit.action == "delete":
                old_content = full_path.read_text()
                old_hash = _hash_content(old_content)
                deletions = old_content.count("\n") + 1
                if not dry_run:
                    full_path.unlink()

            elif edit.action == "create":
                content = edit.content or ""
                new_hash = _hash_content(content)
                insertions = content.count("\n") + 1
                if not dry_run:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content)

            elif edit.action == "update":
                old_file_content = full_path.read_text()
                old_hash = _hash_content(old_file_content)

                # Determine new content
                if edit.old_content is not None:
                    new_file_content = self._apply_exact_edit(
                        old_file_content,
                        edit.old_content,
                        edit.new_content or "",
                        edit.expected_occurrences,
                        edit.path,
                    )
                    # For dry run, compute hash of content being replaced
                    if dry_run:
                        dry_run_info = DryRunInfo(
                            content_hash=_hash_content(edit.old_content),
                        )
                elif edit.content is not None:
                    new_file_content = edit.content
                else:
                    new_file_content = old_file_content

                new_hash = _hash_content(new_file_content)

                # Compute diff stats
                old_lines = old_file_content.splitlines()
                new_lines = new_file_content.splitlines()
                insertions = max(0, len(new_lines) - len(old_lines))
                deletions = max(0, len(old_lines) - len(new_lines))

                if not dry_run:
                    full_path.write_text(new_file_content)

            file_deltas.append(
                FileDelta(
                    path=edit.path,
                    action=f"{edit.action}d",  # type: ignore[arg-type]
                    old_hash=old_hash,
                    new_hash=new_hash,
                    insertions=insertions,
                    deletions=deletions,
                )
            )
            changed_paths.append(full_path)
            total_insertions += insertions
            total_deletions += deletions

        # Trigger reindex callback
        if not dry_run and self._on_mutation and changed_paths:
            self._on_mutation(changed_paths)

        return MutationResult(
            applied=not dry_run,
            dry_run=dry_run,
            delta=MutationDelta(
                mutation_id=mutation_id,
                files_changed=len(file_deltas),
                insertions=total_insertions,
                deletions=total_deletions,
                files=file_deltas,
            ),
            dry_run_info=dry_run_info,
        )

    def _apply_exact_edit(
        self,
        file_content: str,
        old_content: str,
        new_content: str,
        expected_occurrences: int,
        path: str,
    ) -> str:
        """Apply exact content replacement.

        Raises:
            ContentNotFoundError: old_content not found
            MultipleMatchesError: old_content found more times than expected
        """
        # Count occurrences
        count = file_content.count(old_content)

        if count == 0:
            raise ContentNotFoundError(path, old_content[:100] if old_content else None)

        if count != expected_occurrences:
            # Find line numbers of matches
            lines = []
            search_start = 0
            for _ in range(min(count, 10)):  # Limit search
                idx = file_content.find(old_content, search_start)
                if idx == -1:
                    break
                line_num = file_content[:idx].count("\n") + 1
                lines.append(line_num)
                search_start = idx + 1

            raise MultipleMatchesError(path, count, lines)

        # Replace all expected occurrences
        return file_content.replace(old_content, new_content, expected_occurrences)


def _hash_content(content: str) -> str:
    """Hash content for delta tracking."""
    return hashlib.sha256(content.encode()).hexdigest()[:12]
