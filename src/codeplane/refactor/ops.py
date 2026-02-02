"""Refactor operations - refactor_* tools implementation.

Index-based refactoring with probabilistic candidate sets.
Per SPEC.md §23.7 refactor tool specification.

Uses DefFact/RefFact from the index to find candidate rename sites.
Candidates are ranked by certainty - agent reviews before applying.
"""

from __future__ import annotations

import uuid
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
    certainty: Literal["high", "medium", "low"]  # From index certainty


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
    high_certainty_count: int = 0
    low_certainty_count: int = 0  # Agent should review these


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
    """Refactoring via index-based candidate discovery.

    Uses DefFact/RefFact to find rename candidates with certainty scores.
    Agent reviews low-certainty candidates before applying.
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
        _include_comments: bool = True,
        _contexts: list[str] | None = None,
    ) -> RefactorResult:
        """Rename a symbol across the codebase.

        Uses index to find definition and references. Returns candidates
        with certainty scores for agent review.

        Args:
            symbol: Symbol name or path:line:col locator
            new_name: New name for the symbol
            include_comments: Also update comments/docs (default True)
            contexts: Limit to specific contexts

        Returns:
            RefactorResult with preview. Call apply() to execute.
        """
        refactor_id = str(uuid.uuid4())[:8]

        # Find definition via index
        def_fact = await self._coordinator.get_def(symbol)
        if def_fact is None:
            return RefactorResult(
                refactor_id=refactor_id,
                status="divergence",
                divergence=RefactorDivergence(
                    resolution_options=[f"Symbol '{symbol}' not found in index"]
                ),
            )

        # Find all references
        refs = await self._coordinator.get_references(def_fact, _context_id=0)

        # Build edit hunks from refs
        edits_by_file: dict[str, list[EditHunk]] = {}

        # Add definition site
        def_file = await self._get_file_path(def_fact.file_id)
        if def_file:
            edits_by_file.setdefault(def_file, []).append(
                EditHunk(
                    old=def_fact.name,
                    new=new_name,
                    line=def_fact.start_line,
                    certainty="high",  # Definition is always high certainty
                )
            )

        # Add reference sites
        for ref in refs:
            ref_file = await self._get_file_path(ref.file_id)
            if ref_file:
                # Map index certainty to hunk certainty
                cert: Literal["high", "medium", "low"] = (
                    "high" if ref.certainty == "CERTAIN" else "low"
                )
                edits_by_file.setdefault(ref_file, []).append(
                    EditHunk(
                        old=symbol,
                        new=new_name,
                        line=ref.start_line,
                        certainty=cert,
                    )
                )

        # Build preview
        file_edits = [FileEdit(path=path, hunks=hunks) for path, hunks in edits_by_file.items()]
        high_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "high")
        low_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "low")

        preview = RefactorPreview(
            files_affected=len(file_edits),
            edits=file_edits,
            high_certainty_count=high_count,
            low_certainty_count=low_count,
        )

        self._pending[refactor_id] = preview

        return RefactorResult(
            refactor_id=refactor_id,
            status="previewed",
            preview=preview,
        )

    async def _get_file_path(self, file_id: int) -> str | None:
        """Look up file path from file_id."""
        from codeplane.index.models import File

        with self._coordinator.db.session() as session:
            file = session.get(File, file_id)
            return file.path if file else None

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
        # TODO: Use ImportFact to find all imports of this module
        # and generate edit hunks for updating import paths
        raise NotImplementedError("Move not yet implemented")

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
            RefactorResult with preview showing references to clean up.
        """
        # TODO: Find all references and flag them for cleanup
        raise NotImplementedError("Delete not yet implemented")

    async def apply(self, refactor_id: str) -> RefactorResult:
        """Apply a previewed refactoring.

        Args:
            refactor_id: ID from preview result

        Returns:
            RefactorResult with applied delta.
        """
        if refactor_id not in self._pending:
            raise ValueError(f"No pending refactor with ID: {refactor_id}")

        # TODO: Apply edits via MutationOps
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
