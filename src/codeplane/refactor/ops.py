"""Refactor operations - refactor_* tools implementation.

LSP-based semantic refactoring.
Per SPEC.md §23.7 refactor tool specification.

Architecture invariant: LSP-only refactoring - CodePlane never guesses symbol bindings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from codeplane.index.ops import IndexCoordinator
    from codeplane.mutation.ops import MutationDelta

RefactorAction = Literal["rename", "move", "delete", "preview", "apply", "cancel"]


@dataclass
class EditHunk:
    """A single edit hunk in a refactor preview."""

    old: str
    new: str
    line: int
    semantic: bool  # True if SCIP-based, False if comment sweep


@dataclass
class FileEdit:
    """Edits for a single file in refactor preview."""

    path: str
    hunks: list[EditHunk] = field(default_factory=list)


@dataclass
class RefactorPreview:
    """Preview of refactoring changes."""

    files_affected: int
    edits: list[FileEdit] = field(default_factory=list)
    contexts_used: list[str] = field(default_factory=list)


@dataclass
class RefactorDivergence:
    """Divergence detected during refactoring."""

    conflicting_hunks: list[dict[str, str | list[str]]] = field(default_factory=list)
    resolution_options: list[str] = field(default_factory=list)


@dataclass
class RefactorResult:
    """Result of refactor operation."""

    refactor_id: str
    status: Literal["previewed", "applied", "cancelled", "divergence"]
    preview: RefactorPreview | None = None
    applied: MutationDelta | None = None
    divergence: RefactorDivergence | None = None


class RefactorOps:
    """Semantic refactoring operations via LSP.

    Uses LSP for all symbol resolution - never guesses bindings.
    Uses IndexCoordinator for symbol discovery, LSP for edits.
    """

    def __init__(
        self,
        repo_root: Path,
        coordinator: IndexCoordinator,
    ) -> None:
        """Initialize refactor ops.

        Args:
            repo_root: Repository root path
            coordinator: IndexCoordinator for symbol lookup
        """
        self._repo_root = repo_root
        self._coordinator = coordinator
        self._pending: dict[str, RefactorPreview] = {}

    async def rename(
        self,
        symbol: str,
        new_name: str,
        *,
        include_comments: bool = True,
        contexts: list[str] | None = None,
    ) -> RefactorResult:
        """Rename a symbol across the codebase.

        Args:
            symbol: Symbol name or path:line:col locator
            new_name: New name for the symbol
            include_comments: Also update comments/docs (default True)
            contexts: Limit to specific contexts

        Returns:
            RefactorResult with preview. Call apply() to execute.
        """
        # TODO: Implement LSP-based rename
        # 1. Resolve symbol to definition via coordinator
        # 2. Use LSP textDocument/rename to get workspace edits
        # 3. Optionally sweep comments for old name
        # 4. Return preview
        raise NotImplementedError("LSP rename not yet implemented")

    async def move(
        self,
        from_path: str,
        to_path: str,
        *,
        include_comments: bool = True,
    ) -> RefactorResult:
        """Move a file/module, updating all imports.

        Args:
            from_path: Source path
            to_path: Destination path
            include_comments: Update comments/docs

        Returns:
            RefactorResult with preview.
        """
        # TODO: Implement LSP-based move
        raise NotImplementedError("LSP move not yet implemented")

    async def delete(
        self,
        target: str,
        *,
        include_comments: bool = True,
    ) -> RefactorResult:
        """Delete a symbol or file, cleaning up references.

        Args:
            target: Symbol or path to delete
            include_comments: Clean up comments

        Returns:
            RefactorResult with preview.
        """
        # TODO: Implement delete with reference cleanup
        raise NotImplementedError("Delete refactor not yet implemented")

    async def apply(self, refactor_id: str) -> RefactorResult:
        """Apply a previewed refactoring.

        Args:
            refactor_id: ID from preview result

        Returns:
            RefactorResult with applied delta.
        """
        if refactor_id not in self._pending:
            raise ValueError(f"No pending refactor with ID: {refactor_id}")

        # TODO: Apply the pending edits via MutationOps
        raise NotImplementedError("Apply not yet implemented")

    async def cancel(self, refactor_id: str) -> RefactorResult:
        """Cancel a pending refactoring.

        Args:
            refactor_id: ID from preview result

        Returns:
            RefactorResult with cancelled status.
        """
        if refactor_id in self._pending:
            del self._pending[refactor_id]

        return RefactorResult(
            refactor_id=refactor_id,
            status="cancelled",
        )
