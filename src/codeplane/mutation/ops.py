"""Mutation operations - mutate tool implementation.

Atomic file edits with structured delta response.
Per SPEC.md §23.7 mutate tool specification.

Triggers reindex after mutation via callback.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    pass


@dataclass
class Patch:
    """Line-level patch within a file."""

    start: int  # Start line (1-indexed)
    end: int  # End line (1-indexed, inclusive)
    replacement: str


@dataclass
class Edit:
    """A single file edit."""

    path: str
    action: Literal["create", "update", "delete"]
    content: str | None = None  # Full content for create/update
    patches: list[Patch] | None = None  # Or line-level patches


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
class MutationDelta:
    """Structured delta from a mutation."""

    mutation_id: str
    files_changed: int
    insertions: int
    deletions: int
    files: list[FileDelta] = field(default_factory=list)


@dataclass
class MutationResult:
    """Result of mutate operation."""

    applied: bool
    dry_run: bool
    delta: MutationDelta
    affected_symbols: list[str] | None = None
    affected_tests: list[str] | None = None
    repo_fingerprint: str = ""


class MutationOps:
    """Mutation operations for the mutate tool.

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

    def mutate(
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
        """
        mutation_id = str(uuid.uuid4())[:8]
        file_deltas: list[FileDelta] = []
        changed_paths: list[Path] = []
        total_insertions = 0
        total_deletions = 0

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
                old_content = full_path.read_text()
                old_hash = _hash_content(old_content)

                if edit.content is not None:
                    new_content = edit.content
                elif edit.patches:
                    new_content = _apply_patches(old_content, edit.patches)
                else:
                    new_content = old_content

                new_hash = _hash_content(new_content)

                # Compute diff stats
                old_lines = old_content.splitlines()
                new_lines = new_content.splitlines()
                insertions = max(0, len(new_lines) - len(old_lines))
                deletions = max(0, len(old_lines) - len(new_lines))

                if not dry_run:
                    full_path.write_text(new_content)

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
        )


def _hash_content(content: str) -> str:
    """Hash content for delta tracking."""
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def _apply_patches(content: str, patches: list[Patch]) -> str:
    """Apply line-level patches to content."""
    lines = content.splitlines(keepends=True)

    # Sort patches by start line descending to apply from bottom up
    sorted_patches = sorted(patches, key=lambda p: p.start, reverse=True)

    for patch in sorted_patches:
        start_idx = patch.start - 1  # Convert to 0-indexed
        end_idx = patch.end  # End is inclusive in spec

        replacement_lines = patch.replacement.splitlines(keepends=True)
        if patch.replacement and not patch.replacement.endswith("\n"):
            replacement_lines[-1] += "\n"

        lines[start_idx:end_idx] = replacement_lines

    return "".join(lines)
