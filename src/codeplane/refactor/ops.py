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
from typing import TYPE_CHECKING, Any, Literal

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


def _compute_rename_certainty_from_ref(ref: Any) -> Literal["high", "medium", "low"]:
    """
    Compute certainty for a rename candidate based on RefFact properties.

    Certainty tiers (per SPEC.md §7.3):
    - PROVEN refs: Same-file lexical bind with LocalBindFact certainty=CERTAIN -> "high"
    - STRONG refs: Cross-file with explicit ImportFact + ExportSurface trace -> "high"
    - ANCHORED refs: Ambiguous but grouped in AnchorGroup -> "medium"
    - UNKNOWN refs: Cannot classify -> "low"

    Also considers the RefFact's own certainty field as a fallback.
    """
    # Check ref_tier first (most authoritative)
    ref_tier = getattr(ref, "ref_tier", None)
    if ref_tier:
        if ref_tier in ("PROVEN", "proven"):
            return "high"
        elif ref_tier in ("STRONG", "strong"):
            return "high"  # Explicit import trace
        elif ref_tier in ("ANCHORED", "anchored"):
            return "medium"
        # UNKNOWN falls through to certainty check

    # Fallback to certainty field
    certainty = getattr(ref, "certainty", None)
    if certainty in ("CERTAIN", "certain"):
        return "high"

    return "low"


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

    def _compute_rename_certainty(self, ref: Any) -> Literal["high", "medium", "low"]:
        """Compute certainty for a rename candidate.

        Delegates to module-level function for reusability.
        """
        return _compute_rename_certainty_from_ref(ref)

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
                        # Compute certainty based on RefTier (per SPEC.md)
                        cert = self._compute_rename_certainty(ref)
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
        """Use Tantivy index for lexical fallback - NOT filesystem scan.

        Queries the index for the symbol, then scans matched files for ALL
        occurrences. Tantivy returns one result per document (file), so we
        need to find all line occurrences within each matched file.
        """
        # Search the index for the symbol
        search_response = await self._coordinator.search(symbol, limit=500)

        # Collect unique file paths from search results
        matched_files: set[str] = set()
        for hit in search_response.results:
            if hit.snippet and _word_boundary_match(hit.snippet, symbol):
                matched_files.add(hit.path)

        # For each matched file, scan for ALL occurrences
        for file_path in matched_files:
            full_path = self._repo_root / file_path
            if not full_path.exists():
                continue

            try:
                content = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Find all lines containing the symbol with word boundaries
            lines = content.splitlines()
            for line_num, line in enumerate(lines, 1):  # 1-indexed
                if _word_boundary_match(line, symbol):
                    loc = (file_path, line_num)
                    if loc not in seen_locations:
                        seen_locations.add(loc)
                        edits_by_file.setdefault(file_path, []).append(
                            EditHunk(
                                old=symbol,
                                new=new_name,
                                line=line_num,
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

        Uses ImportFact to find all imports referencing the source module
        and generates edits to update them to the new path.

        Args:
            from_path: Source path (relative to repo root)
            to_path: Destination path (relative to repo root)
            include_comments: Update comments/docs mentioning the path

        Returns:
            RefactorResult with preview. Call apply() to execute.
        """
        from sqlmodel import or_, select

        from codeplane.index.models import File, ImportFact

        refactor_id = str(uuid.uuid4())[:8]
        edits_by_file: dict[str, list[EditHunk]] = {}
        seen_locations: set[tuple[str, int]] = set()

        # Normalize paths (remove leading ./ and trailing slashes)
        from_path = from_path.lstrip("./").rstrip("/")
        to_path = to_path.lstrip("./").rstrip("/")

        # Convert file paths to module paths for import matching
        # e.g., "src/utils/helper.py" -> "src.utils.helper"
        from_module = self._path_to_module(from_path)
        to_module = self._path_to_module(to_path)

        # Build SQL filter patterns:
        # - Exact match: source_literal == from_module
        # - Prefix match: source_literal LIKE from_module.%
        # - imported_name match for bare imports
        from_module_prefix = f"{from_module}.%"
        bare_name = from_module.split(".")[-1]  # e.g., "helper" from "src.utils.helper"

        with self._coordinator.db.session() as session:
            # Push filtering to SQL - don't fetch all imports
            # Note: source_literal is nullable, these comparisons handle NULL correctly
            stmt = (
                select(ImportFact, File.path)
                .join(File, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                .where(
                    or_(
                        # Match source_literal exactly or as prefix
                        ImportFact.source_literal == from_module,
                        ImportFact.source_literal.like(from_module_prefix),  # type: ignore[union-attr]
                        # Match imported_name for bare "import foo" statements
                        ImportFact.imported_name == from_module,
                        ImportFact.imported_name == bare_name,
                    )
                )
            )
            results = session.exec(stmt).all()

            for imp, file_path in results:
                # Determine what to replace
                old_value = ""
                new_value = ""

                if imp.source_literal:
                    if imp.source_literal == from_module:
                        old_value = from_module
                        new_value = to_module
                    elif imp.source_literal.startswith(from_module + "."):
                        old_value = imp.source_literal
                        new_value = imp.source_literal.replace(from_module, to_module, 1)

                # If source_literal didn't match, check imported_name
                if not old_value:
                    if imp.imported_name == from_module:
                        old_value = from_module
                        new_value = to_module
                    elif imp.imported_name == bare_name:
                        # For "import helper" -> need to update if it refers to our module
                        # This is lower certainty since we can't be sure
                        old_value = bare_name
                        new_value = to_module.split(".")[-1]  # new bare name

                if old_value:
                    # Read file to find exact line
                    full_path = self._repo_root / file_path
                    if full_path.exists():
                        try:
                            content = full_path.read_text(encoding="utf-8")
                            lines = content.splitlines()
                            for i, line in enumerate(lines, 1):
                                if old_value in line and "import" in line.lower():
                                    loc = (file_path, i)
                                    if loc not in seen_locations:
                                        seen_locations.add(loc)
                                        # Certainty based on match type
                                        cert: Literal["high", "medium", "low"] = (
                                            "high" if imp.source_literal else "medium"
                                        )
                                        edits_by_file.setdefault(file_path, []).append(
                                            EditHunk(
                                                old=old_value,
                                                new=new_value,
                                                line=i,
                                                certainty=cert,
                                            )
                                        )
                                    break
                        except (OSError, UnicodeDecodeError):
                            pass

        # Lexical fallback: search for module path strings in all files
        await self._add_move_lexical_fallback(
            from_module, to_module, from_path, to_path, seen_locations, edits_by_file
        )

        # Scan comments if requested
        if include_comments:
            affected_files = set(edits_by_file.keys())
            # Check for path mentions in comments
            for pattern, replacement in [(from_path, to_path), (from_module, to_module)]:
                await self._add_comment_occurrences(
                    pattern, replacement, affected_files, edits_by_file
                )

        # Build preview
        preview = self._build_preview(edits_by_file)
        self._pending[refactor_id] = preview

        return RefactorResult(
            refactor_id=refactor_id,
            status="previewed",
            preview=preview,
        )

    async def _add_move_lexical_fallback(
        self,
        from_module: str,
        to_module: str,
        from_path: str,
        to_path: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Use Tantivy index for move lexical fallback - NOT filesystem scan.

        Searches for quoted module/path strings via the index.
        """
        # Search for module path in quotes
        for old_val, new_val in [(from_module, to_module), (from_path, to_path)]:
            # Search for the value (index will find files containing it)
            search_response = await self._coordinator.search(f'"{old_val}"', limit=200)

            for hit in search_response.results:
                loc = (hit.path, hit.line)
                if loc in seen_locations:
                    continue

                # Verify quoted string match
                if hit.snippet and (f'"{old_val}"' in hit.snippet or f"'{old_val}'" in hit.snippet):
                    seen_locations.add(loc)
                    edits_by_file.setdefault(hit.path, []).append(
                        EditHunk(
                            old=old_val,
                            new=new_val,
                            line=hit.line,
                            certainty="low",
                        )
                    )

    def _path_to_module(self, path: str) -> str:
        """Convert file path to Python module path."""
        # Remove .py extension and convert / to .
        module = path.replace("/", ".").replace("\\", ".")
        if module.endswith(".py"):
            module = module[:-3]
        return module

    def _build_preview(self, edits_by_file: dict[str, list[EditHunk]]) -> RefactorPreview:
        """Build RefactorPreview from edits."""
        file_edits = [FileEdit(path=path, hunks=hunks) for path, hunks in edits_by_file.items()]
        high_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "high")
        medium_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "medium")
        low_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "low")

        verification_required = low_count > 0
        low_certainty_files: list[str] = []
        verification_guidance = None

        if verification_required:
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
                f"{low_count} low-certainty matches found.\n\n"
                f"Files: {files_summary}\n\n"
                f"Use refactor_inspect to review before applying."
            )

        return RefactorPreview(
            files_affected=len(file_edits),
            edits=file_edits,
            high_certainty_count=high_count,
            medium_certainty_count=medium_count,
            low_certainty_count=low_count,
            verification_required=verification_required,
            low_certainty_files=low_certainty_files,
            verification_guidance=verification_guidance,
        )

    async def delete(
        self,
        target: str,
        *,
        include_comments: bool = True,
    ) -> RefactorResult:
        """Delete a symbol or file, finding all references that need cleanup.

        Unlike rename/move, delete doesn't auto-fix references - it surfaces them
        for manual cleanup since deletion semantics vary (remove import, replace
        with alternative, etc.).

        Args:
            target: Symbol name or file path to delete
            include_comments: Include comment references in preview

        Returns:
            RefactorResult with preview showing all references.
            Hunks have old=target, new="" to indicate deletion sites.
        """
        refactor_id = str(uuid.uuid4())[:8]
        edits_by_file: dict[str, list[EditHunk]] = {}
        seen_locations: set[tuple[str, int]] = set()

        # Check if target is a file path or symbol name
        is_file = "/" in target or target.endswith(".py")

        if is_file:
            # Find imports of this file/module
            await self._find_file_references(target, seen_locations, edits_by_file)
        else:
            # Find references to this symbol
            await self._find_symbol_references(target, seen_locations, edits_by_file)

        # Lexical fallback for both cases
        await self._add_delete_lexical_fallback(target, seen_locations, edits_by_file)

        # Scan comments if requested
        if include_comments:
            affected_files = set(edits_by_file.keys())
            # For delete, we mark comment refs but don't auto-remove
            await self._add_comment_occurrences(target, "", affected_files, edits_by_file)

        # Build preview with guidance
        preview = self._build_delete_preview(target, edits_by_file)
        self._pending[refactor_id] = preview

        return RefactorResult(
            refactor_id=refactor_id,
            status="previewed",
            preview=preview,
        )

    async def _find_file_references(
        self,
        file_path: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Find all imports referencing a file/module."""
        from sqlmodel import or_, select

        from codeplane.index.models import File, ImportFact

        # Normalize and convert to module path
        file_path = file_path.lstrip("./").rstrip("/")
        module_path = self._path_to_module(file_path)
        module_prefix = f"{module_path}.%"
        bare_name = module_path.split(".")[-1]

        with self._coordinator.db.session() as session:
            # Push filtering to SQL
            # Note: source_literal is nullable, these comparisons handle NULL correctly
            stmt = (
                select(ImportFact, File.path)
                .join(File, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                .where(
                    or_(
                        ImportFact.source_literal == module_path,
                        ImportFact.source_literal.like(module_prefix),  # type: ignore[union-attr]
                        ImportFact.imported_name == module_path,
                        ImportFact.imported_name == bare_name,
                    )
                )
            )
            results = session.exec(stmt).all()

            for imp, ref_file in results:
                full_path = self._repo_root / ref_file
                if full_path.exists():
                    try:
                        content = full_path.read_text(encoding="utf-8")
                        lines = content.splitlines()
                        # Find the import line
                        search_term = imp.source_literal or imp.imported_name
                        for i, line in enumerate(lines, 1):
                            if search_term in line and "import" in line.lower():
                                loc = (ref_file, i)
                                if loc not in seen_locations:
                                    seen_locations.add(loc)
                                    # source_literal match = high certainty
                                    # imported_name only = medium certainty
                                    cert: Literal["high", "medium", "low"] = (
                                        "high" if imp.source_literal else "medium"
                                    )
                                    edits_by_file.setdefault(ref_file, []).append(
                                        EditHunk(
                                            old=line.strip(),
                                            new="",  # Deletion marker
                                            line=i,
                                            certainty=cert,
                                        )
                                    )
                                break
                    except (OSError, UnicodeDecodeError):
                        pass

    async def _find_symbol_references(
        self,
        symbol: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Find all references to a symbol."""
        # Get all definitions with this name
        all_defs = await self._coordinator.get_all_defs(symbol)

        for def_fact in all_defs:
            # Mark the definition site
            def_file = await self._get_file_path(def_fact.file_id)
            if def_file:
                loc = (def_file, def_fact.start_line)
                if loc not in seen_locations:
                    seen_locations.add(loc)
                    edits_by_file.setdefault(def_file, []).append(
                        EditHunk(
                            old=def_fact.name,
                            new="",  # Deletion marker
                            line=def_fact.start_line,
                            certainty="high",
                        )
                    )

            # Get all references
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
                                new="",
                                line=ref.start_line,
                                certainty=cert,
                            )
                        )

    async def _add_delete_lexical_fallback(
        self,
        target: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Use Tantivy index for delete lexical fallback - NOT filesystem scan."""
        # Build search patterns
        patterns = [target]
        if "/" in target or target.endswith(".py"):
            patterns.append(self._path_to_module(target))

        for pattern in patterns:
            search_response = await self._coordinator.search(pattern, limit=500)

            for hit in search_response.results:
                loc = (hit.path, hit.line)
                if loc in seen_locations:
                    continue

                # Verify word boundary match
                if hit.snippet and _word_boundary_match(hit.snippet, pattern):
                    seen_locations.add(loc)
                    edits_by_file.setdefault(hit.path, []).append(
                        EditHunk(
                            old=pattern,
                            new="",
                            line=hit.line,
                            certainty="low",
                        )
                    )

    def _build_delete_preview(
        self,
        target: str,
        edits_by_file: dict[str, list[EditHunk]],
    ) -> RefactorPreview:
        """Build preview with delete-specific guidance."""
        preview = self._build_preview(edits_by_file)

        # Override guidance for delete operation
        total_refs = sum(len(fe.hunks) for fe in preview.edits)
        preview.verification_required = True
        preview.verification_guidance = (
            f"Found {total_refs} references to '{target}' that need cleanup.\n\n"
            f"Delete does NOT auto-remove references. You must:\n"
            f"  1. Review each reference with refactor_inspect\n"
            f"  2. Decide how to handle: remove import, replace with alternative, etc.\n"
            f"  3. Use atomic_edit_files to make changes manually\n"
            f"  4. Call refactor_cancel to clear this preview\n\n"
            f"High certainty: {preview.high_certainty_count} (index-backed)\n"
            f"Low certainty: {preview.low_certainty_count} (lexical matches)"
        )

        return preview

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
