"""Refactor operations - refactor_* tools implementation.

Index-based refactoring with probabilistic candidate sets.
Per SPEC.md §23.7 refactor tool specification.

Uses DefFact/RefFact from the index to find candidate rename sites.
Candidates are ranked by certainty - agent reviews before applying.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from codeplane.index.ops import IndexCoordinator
    from codeplane.mutation.ops import Edit, MutationDelta, MutationOps

RefactorAction = Literal["rename", "move", "delete", "preview", "apply", "cancel"]


@dataclass
class EditHunk:
    """A single edit hunk in a refactor preview."""

    old: str
    new: str
    line: int
    certainty: Literal["high", "medium", "low"]


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
    medium_certainty_count: int = 0
    low_certainty_count: int = 0
    # Verification guidance for agents
    verification_required: bool = False
    low_certainty_files: list[str] = field(default_factory=list)
    verification_guidance: str | None = None


@dataclass
class InspectResult:
    """Result of inspecting low-certainty matches in a file."""

    path: str
    matches: list[dict[str, str | int]]  # {line, snippet, context_before, context_after}


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


def _scan_file_for_comment_occurrences(
    content: str,
    symbol: str,
    language: str | None,
) -> list[tuple[int, str]]:
    """Scan file content for symbol occurrences in comments and docstrings.

    Returns list of (line_number, context_snippet) tuples.
    """
    occurrences: list[tuple[int, str]] = []
    lines = content.splitlines()

    # Patterns for comments and docstrings by language
    if language in ("python", None):
        # Python: # comments, triple-quoted strings
        in_docstring = False
        docstring_delimiter = None

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Check for docstring boundaries
            if not in_docstring and (stripped.startswith('"""') or stripped.startswith("'''")):
                docstring_delimiter = stripped[:3]
                in_docstring = True
                # Check if ends on same line
                if stripped.count(docstring_delimiter) >= 2:
                    in_docstring = False
                    if _word_boundary_match(line, symbol):
                        occurrences.append((i, stripped[:60]))
                elif _word_boundary_match(line, symbol):
                    occurrences.append((i, stripped[:60]))
                continue

            if in_docstring:
                if docstring_delimiter and docstring_delimiter in stripped[3:]:
                    in_docstring = False
                if _word_boundary_match(line, symbol):
                    occurrences.append((i, stripped[:60]))
                continue

            # Check for # comments
            if "#" in line:
                comment_start = line.index("#")
                comment_text = line[comment_start:]
                if _word_boundary_match(comment_text, symbol):
                    occurrences.append((i, stripped[:60]))

    elif language in ("javascript", "typescript", "java", "go", "rust", "cpp"):
        # C-style: // comments, /* */ blocks, and JSDoc /** */
        in_block_comment = False

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            if in_block_comment:
                if "*/" in line:
                    in_block_comment = False
                if _word_boundary_match(line, symbol):
                    occurrences.append((i, stripped[:60]))
                continue

            if "/*" in line:
                in_block_comment = True
                if "*/" in line[line.index("/*") + 2 :]:
                    in_block_comment = False
                if _word_boundary_match(line, symbol):
                    occurrences.append((i, stripped[:60]))
                continue

            # Check for // comments
            if "//" in line:
                comment_start = line.index("//")
                comment_text = line[comment_start:]
                if _word_boundary_match(comment_text, symbol):
                    occurrences.append((i, stripped[:60]))

    return occurrences


def _word_boundary_match(text: str, symbol: str) -> bool:
    """Check if symbol appears in text as a whole word."""
    pattern = rf"\b{re.escape(symbol)}\b"
    return bool(re.search(pattern, text))


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

        Uses index to find ALL definitions with the given name and their
        references. Also performs codebase-wide lexical search as fallback.
        Returns candidates with certainty scores for agent review.

        Args:
            symbol: Symbol name or path:line:col locator
            new_name: New name for the symbol
            _include_comments: Also update comments/docs (default True)
            _contexts: Limit to specific contexts

        Returns:
            RefactorResult with preview. Call apply() to execute.
        """
        refactor_id = str(uuid.uuid4())[:8]

        # Find ALL definitions with this name (not just the first)
        all_defs = await self._coordinator.get_all_defs(symbol)

        edits_by_file: dict[str, list[EditHunk]] = {}
        seen_locations: set[tuple[str, int]] = set()  # (path, line) to dedupe

        # Process each definition and its references
        for def_fact in all_defs:
            def_file = await self._get_file_path(def_fact.file_id)
            if def_file:
                loc = (def_file, def_fact.start_line)
                if loc not in seen_locations:
                    seen_locations.add(loc)
                    edits_by_file.setdefault(def_file, []).append(
                        EditHunk(
                            old=def_fact.name,
                            new=new_name,
                            line=def_fact.start_line,
                            certainty="high",
                        )
                    )

            # Get references for this definition
            refs = await self._coordinator.get_references(def_fact, _context_id=0)
            for ref in refs:
                ref_file = await self._get_file_path(ref.file_id)
                if ref_file:
                    loc = (ref_file, ref.start_line)
                    if loc not in seen_locations:
                        seen_locations.add(loc)
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

        # Codebase-wide lexical search as low-certainty fallback
        await self._add_lexical_fallback(symbol, new_name, seen_locations, edits_by_file)

        # Scan for comment/docstring occurrences
        if _include_comments:
            affected_files = set(edits_by_file.keys())
            await self._add_comment_occurrences(symbol, new_name, affected_files, edits_by_file)

        # Build preview
        file_edits = [FileEdit(path=path, hunks=hunks) for path, hunks in edits_by_file.items()]
        high_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "high")
        medium_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "medium")
        low_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "low")

        # Build compact verification guidance if there are low-certainty matches
        verification_required = low_count > 0
        low_certainty_files: list[str] = []
        verification_guidance = None

        if verification_required:
            # Collect files with low-certainty matches and their counts
            file_counts: dict[str, int] = {}
            for fe in file_edits:
                low_in_file = sum(1 for h in fe.hunks if h.certainty == "low")
                if low_in_file > 0:
                    file_counts[fe.path] = low_in_file
                    low_certainty_files.append(fe.path)

            files_summary = ", ".join(f"{p} ({c})" for p, c in list(file_counts.items())[:5])
            if len(file_counts) > 5:
                files_summary += f", ... and {len(file_counts) - 5} more files"

            verification_guidance = (
                f"{low_count} low-certainty lexical matches may include false positives "
                f"(e.g., English word vs symbol name).\n\n"
                f"Files: {files_summary}\n\n"
                f"BEFORE calling refactor_apply:\n"
                f"  1. Use refactor_inspect(refactor_id, path) to review matches with context\n"
                f"  2. Or use read_files / search to verify manually\n"
                f"  3. If false positives exist, use refactor_cancel and handle manually"
            )

        preview = RefactorPreview(
            files_affected=len(file_edits),
            edits=file_edits,
            high_certainty_count=high_count,
            medium_certainty_count=medium_count,
            low_certainty_count=low_count,
            verification_required=verification_required,
            low_certainty_files=low_certainty_files,
            verification_guidance=verification_guidance,
        )

        self._pending[refactor_id] = preview

        return RefactorResult(
            refactor_id=refactor_id,
            status="previewed",
            preview=preview,
        )

    async def _add_comment_occurrences(
        self,
        symbol: str,
        new_name: str,
        affected_files: set[str],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Scan affected files for comment/docstring occurrences."""
        from codeplane.index.models import File

        for file_path in affected_files:
            full_path = self._repo_root / file_path
            if not full_path.exists():
                continue

            try:
                content = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Detect language from file
            with self._coordinator.db.session() as session:
                from sqlmodel import select

                file_record = session.exec(select(File).where(File.path == file_path)).first()
                language = file_record.language_family if file_record else None

            # Find comment occurrences
            comment_hits = _scan_file_for_comment_occurrences(content, symbol, language)

            # Get existing edit lines to avoid duplicates
            existing_lines = {h.line for h in edits_by_file.get(file_path, [])}

            for line_num, _snippet in comment_hits:
                if line_num not in existing_lines:
                    edits_by_file.setdefault(file_path, []).append(
                        EditHunk(
                            old=symbol,
                            new=new_name,
                            line=line_num,
                            certainty="medium",  # Comment occurrences are medium certainty
                        )
                    )

    async def _add_lexical_fallback(
        self,
        symbol: str,
        new_name: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Codebase-wide lexical search as low-certainty fallback.

        Finds all occurrences of the symbol as a whole word across the
        entire codebase. These are marked as low certainty since we can't
        semantically verify they refer to the same symbol.
        """
        from codeplane.index.models import File

        # Get all indexed files
        with self._coordinator.db.session() as session:
            from sqlmodel import select

            files = session.exec(select(File)).all()

        for file_record in files:
            full_path = self._repo_root / file_record.path
            if not full_path.exists():
                continue

            try:
                content = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Search for whole-word matches
            pattern = rf"\b{re.escape(symbol)}\b"
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                loc = (file_record.path, i)
                if loc in seen_locations:
                    continue

                if re.search(pattern, line):
                    seen_locations.add(loc)
                    edits_by_file.setdefault(file_record.path, []).append(
                        EditHunk(
                            old=symbol,
                            new=new_name,
                            line=i,
                            certainty="low",
                        )
                    )

    async def inspect(
        self,
        refactor_id: str,
        path: str,
        *,
        context_lines: int = 2,
    ) -> InspectResult:
        """Inspect low-certainty matches in a file with surrounding context.

        Use this to verify lexical matches before applying a refactor.

        Args:
            refactor_id: ID from refactor_rename preview
            path: File path to inspect
            context_lines: Lines of context before/after (default 2)

        Returns:
            InspectResult with snippets and context for each match
        """
        preview = self._pending.get(refactor_id)
        if preview is None:
            return InspectResult(path=path, matches=[])

        # Find the file in the preview
        file_edit = next((fe for fe in preview.edits if fe.path == path), None)
        if file_edit is None:
            return InspectResult(path=path, matches=[])

        # Read the file
        full_path = self._repo_root / path
        try:
            content = full_path.read_text(encoding="utf-8")
            lines = content.splitlines()
        except (OSError, UnicodeDecodeError):
            return InspectResult(path=path, matches=[])

        matches: list[dict[str, str | int]] = []
        for hunk in file_edit.hunks:
            if hunk.certainty != "low":
                continue

            line_idx = hunk.line - 1  # 0-indexed
            if 0 <= line_idx < len(lines):
                # Get context
                start = max(0, line_idx - context_lines)
                end = min(len(lines), line_idx + context_lines + 1)

                matches.append(
                    {
                        "line": hunk.line,
                        "snippet": lines[line_idx].strip(),
                        "context_before": "\n".join(lines[start:line_idx]),
                        "context_after": "\n".join(lines[line_idx + 1 : end]),
                    }
                )

        return InspectResult(path=path, matches=matches)

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

    async def apply(self, refactor_id: str, mutation_ops: MutationOps) -> RefactorResult:
        """Apply a previewed refactoring.

        Args:
            refactor_id: ID from preview result
            mutation_ops: MutationOps instance to perform edits

        Returns:
            RefactorResult with applied delta.
        """
        if refactor_id not in self._pending:
            raise ValueError(f"No pending refactor with ID: {refactor_id}")

        preview = self._pending[refactor_id]
        edits: list[Edit] = []

        # Import Edit here to avoid circular imports if not available at module level
        # But we added it to TYPE_CHECKING. We need it at runtime.
        from codeplane.mutation.ops import Edit

        for file_edit in preview.edits:
            full_path = self._repo_root / file_edit.path
            if not full_path.exists():
                # Skip or warn? For now, skip files that disappeared
                continue

            # Read file content
            content = full_path.read_text(encoding="utf-8")
            lines = content.splitlines(keepends=True)

            # Group hunks by line for this file
            hunks_by_line: dict[int, list[EditHunk]] = {}
            for hunk in file_edit.hunks:
                hunks_by_line.setdefault(hunk.line, []).append(hunk)

            # Apply edits to lines
            new_lines = []
            for i, line_content in enumerate(lines, 1):  # 1-based indexing
                if i in hunks_by_line:
                    # Apply replacements on this line
                    # Sort by length of 'old' descending to avoid substring issues often
                    # but simple replace is dangerous without columns.
                    # Proceeding with simple replace per current arch.
                    current_line = line_content
                    for hunk in hunks_by_line[i]:
                        current_line = current_line.replace(hunk.old, hunk.new)
                    new_lines.append(current_line)
                else:
                    new_lines.append(line_content)

            # Reconstruct content
            new_content = "".join(new_lines)

            edits.append(Edit(path=file_edit.path, action="update", content=new_content))

        # Execute mutation
        mutation_result = mutation_ops.atomic_edit_files(edits)

        # Clear pending
        del self._pending[refactor_id]

        return RefactorResult(
            refactor_id=refactor_id,
            status="applied",
            applied=mutation_result.delta,
        )

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
