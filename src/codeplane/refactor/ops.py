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
    warning: str | None = None  # Agent guidance (e.g., path:line:col format detected)


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

    async def _extract_symbol_at_location(
        self,
        file_path: str,
        line_num: int,
    ) -> str | None:
        """Try to extract symbol name from a file:line location.

        Used when agent mistakenly passes path:line:col format.
        Returns the symbol name if found at that line.
        """
        full_path = self._repo_root / file_path
        if not full_path.exists():
            return None

        # Read the line and try to extract identifier
        try:
            content = full_path.read_text()
            lines = content.splitlines()
            if 0 < line_num <= len(lines):
                line = lines[line_num - 1]
                # Try to find a likely symbol name (class/def/var assignment)
                patterns = [
                    r"class\s+(\w+)",
                    r"def\s+(\w+)",
                    r"async\s+def\s+(\w+)",
                    r"^(\w+)\s*=",
                    r"^\s*(\w+)\s*:",
                ]
                for pattern in patterns:
                    m = re.search(pattern, line)
                    if m:
                        return m.group(1)
        except (OSError, UnicodeDecodeError):
            pass

        return None

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
            symbol: Symbol name (e.g., "MyClass", "my_function")
            new_name: New name for the symbol
            _include_comments: Also update comments/docs (default True)
            _contexts: Limit to specific contexts

        Returns:
            RefactorResult with preview. Call apply() to execute.
        """
        # Detect and strip path:line:col prefix if agent mistakenly passed it
        symbol_warning: str | None = None
        path_line_col_pattern = re.compile(r"^(.+):(\d+):(\d+)$")
        match = path_line_col_pattern.match(symbol)
        if match:
            # Extract just the symbol name - this is a guess, warn the agent
            original_input = symbol
            # The "symbol" they probably meant is just the identifier, not the locator
            # We can't reliably extract it from path:line:col, so search the file
            file_path = match.group(1)
            line_num = int(match.group(2))

            # Try to find definition at that location to get actual symbol name
            extracted_symbol = await self._extract_symbol_at_location(file_path, line_num)
            if extracted_symbol:
                symbol = extracted_symbol
                symbol_warning = (
                    f"WARNING: Detected path:line:col format '{original_input}'. "
                    f"Extracted symbol name '{symbol}'. "
                    f"In future, pass just the symbol name directly (e.g., 'MyClass' not 'path/file.py:10:5'). "
                    f"Please verify '{symbol}' is the correct symbol to rename."
                )
            else:
                # Couldn't extract - return early with error guidance
                return RefactorResult(
                    refactor_id=str(uuid.uuid4())[:8],
                    status="previewed",
                    preview=RefactorPreview(
                        files_affected=0,
                        edits=[],
                        verification_required=True,
                        verification_guidance=(
                            f"ERROR: Received path:line:col format '{original_input}' but could not "
                            f"extract symbol name at that location. "
                            f"Pass the symbol name directly (e.g., 'MyClass' not 'path/file.py:10:5')."
                        ),
                    ),
                )

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
            refs = await self._coordinator.get_all_references(def_fact, _context_id=0)
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
            warning=symbol_warning,
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

        Supports all languages via language-aware import variant generation:
        - Python/Lua: dotted paths with/without src. prefix
        - Go/Rust/Java/etc.: uses declared_module from File record
        - JS/TS: handled via lexical fallback (relative paths)

        Args:
            from_path: Source path (relative to repo root)
            to_path: Destination path (relative to repo root)
            include_comments: Update comments/docs mentioning the path

        Returns:
            RefactorResult with preview. Call apply() to execute.
        """
        from sqlmodel import select

        from codeplane.index._internal.indexing.module_mapping import (
            file_to_import_candidates,
            file_to_import_sql_patterns,
            get_module_separator,
            infer_target_declared_module,
        )
        from codeplane.index.models import File, ImportFact

        refactor_id = str(uuid.uuid4())[:8]
        edits_by_file: dict[str, list[EditHunk]] = {}
        seen_locations: set[tuple[str, int]] = set()

        # Normalize paths (remove leading ./ and trailing slashes)
        from_path = from_path.lstrip("./").rstrip("/")
        to_path = to_path.lstrip("./").rstrip("/")

        # Look up source file to get language_family and declared_module
        language_family: str | None = None
        declared_module: str | None = None
        with self._coordinator.db.session() as session:
            file_record = session.exec(select(File).where(File.path == from_path)).first()
            if file_record:
                language_family = file_record.language_family
                declared_module = file_record.declared_module

        # Generate import candidates for both source and target paths
        from_candidates = file_to_import_candidates(from_path, language_family, declared_module)

        # For declaration-based languages, infer the target declared_module
        to_declared_module = infer_target_declared_module(
            from_path, to_path, declared_module, language_family
        )
        to_candidates = file_to_import_candidates(to_path, language_family, to_declared_module)
        # Build mapping from old import string to new import string
        # The first candidate is the "canonical" form
        to_canonical = to_candidates[0] if to_candidates else self._path_to_module(to_path)
        replacement_map: dict[str, str] = {}
        for fc in from_candidates:
            # Map each from variant to the corresponding to variant
            # For dotted paths, preserve structure
            if to_candidates and len(to_candidates) > 1:
                # If from has src. prefix, use to with src. prefix (first candidate)
                # If from lacks src. prefix, use to without it (second candidate)
                if fc.startswith("src.") and to_candidates[0].startswith("src."):
                    replacement_map[fc] = to_candidates[0]
                elif not fc.startswith("src.") and len(to_candidates) > 1:
                    replacement_map[fc] = to_candidates[1]
                else:
                    replacement_map[fc] = to_canonical
            else:
                replacement_map[fc] = to_canonical

        # Get SQL patterns for querying ImportFact
        exact_matches, prefix_patterns = file_to_import_sql_patterns(
            from_path, language_family, declared_module
        )

        # Also match bare name for Python "import foo" style
        sep = get_module_separator(language_family)
        bare_names: list[str] = []
        for candidate in from_candidates:
            bare = candidate.rsplit(sep, 1)[-1] if sep in candidate else candidate
            if bare and bare not in bare_names:
                bare_names.append(bare)
        with self._coordinator.db.session() as session:
            from sqlmodel import col, or_, select

            # Build SQL conditions for all variants
            conditions: list[Any] = []

            # Exact matches
            if exact_matches:
                conditions.append(col(ImportFact.source_literal).in_(exact_matches))

            # Prefix matches (submodule imports)
            for prefix in prefix_patterns:
                conditions.append(col(ImportFact.source_literal).like(f"{prefix}%"))

            # Bare name matches for imported_name
            if bare_names:
                conditions.append(col(ImportFact.imported_name).in_(bare_names))

            # Also check imported_name for full module paths
            if from_candidates:
                conditions.append(col(ImportFact.imported_name).in_(from_candidates))

            if not conditions:
                # No SQL conditions - skip to lexical fallback
                pass
            else:
                stmt = (
                    select(ImportFact, File.path)
                    .join(File, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                    .where(or_(*conditions))
                )
                results = session.exec(stmt).all()

                for imp, file_path in results:
                    # Determine what to replace
                    old_value = ""
                    new_value = ""

                    if imp.source_literal:
                        # Check exact match first
                        if imp.source_literal in replacement_map:
                            old_value = imp.source_literal
                            new_value = replacement_map[imp.source_literal]
                        else:
                            # Check prefix match (submodule import)
                            for from_c, to_c in replacement_map.items():
                                if imp.source_literal.startswith(from_c + sep):
                                    old_value = imp.source_literal
                                    new_value = imp.source_literal.replace(from_c, to_c, 1)
                                    break

                    # If source_literal didn't match, check imported_name
                    if not old_value:
                        if imp.imported_name in replacement_map:
                            old_value = imp.imported_name
                            new_value = replacement_map[imp.imported_name]
                        elif imp.imported_name in bare_names:
                            # Bare import like "import helper"
                            to_bare = (
                                to_canonical.rsplit(sep, 1)[-1]
                                if sep in to_canonical
                                else to_canonical
                            )
                            old_value = imp.imported_name
                            new_value = to_bare

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
            from_candidates,
            to_canonical,
            replacement_map,
            from_path,
            to_path,
            seen_locations,
            edits_by_file,
        )

        # Scan comments if requested
        if include_comments:
            affected_files = set(edits_by_file.keys())
            # Check for path mentions in comments
            patterns_to_search = [(from_path, to_path)]
            for fc in from_candidates:
                patterns_to_search.append((fc, replacement_map.get(fc, to_canonical)))
            for pattern, replacement in patterns_to_search:
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
        from_candidates: list[str],
        to_canonical: str,
        replacement_map: dict[str, str],
        from_path: str,
        to_path: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Use Tantivy index for move lexical fallback - NOT filesystem scan.

        Searches for quoted module/path strings via the index.
        Handles all language variants.
        """
        # Build search patterns: all from_candidates + file paths
        search_pairs: list[tuple[str, str]] = [(from_path, to_path)]
        for fc in from_candidates:
            # Use replacement_map to preserve prefix structure (e.g. src. prefix)
            search_pairs.append((fc, replacement_map.get(fc, to_canonical)))
        for old_val, new_val in search_pairs:
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
        """Find all imports referencing a file/module.

        Supports all languages via language-aware import variant generation.
        """
        from sqlmodel import select

        from codeplane.index._internal.indexing.module_mapping import (
            file_to_import_candidates,
            file_to_import_sql_patterns,
        )
        from codeplane.index.models import File, ImportFact

        # Normalize path
        file_path = file_path.lstrip("./").rstrip("/")

        # Look up source file to get language_family and declared_module
        language_family: str | None = None
        declared_module: str | None = None
        with self._coordinator.db.session() as session:
            file_record = session.exec(select(File).where(File.path == file_path)).first()
            if file_record:
                language_family = file_record.language_family
                declared_module = file_record.declared_module

        # Generate import candidates using language-aware utility
        candidates = file_to_import_candidates(file_path, language_family, declared_module)
        exact_matches, prefix_patterns = file_to_import_sql_patterns(
            file_path, language_family, declared_module
        )

        # Also match bare name for Python "import foo" style
        bare_names: list[str] = []
        for candidate in candidates:
            bare = candidate.split(".")[-1] if "." in candidate else candidate
            if bare and bare not in bare_names:
                bare_names.append(bare)

        with self._coordinator.db.session() as session:
            from sqlmodel import col, or_, select

            # Build SQL conditions for all variants
            conditions: list[Any] = []

            # Exact matches
            if exact_matches:
                conditions.append(col(ImportFact.source_literal).in_(exact_matches))

            # Prefix matches (submodule imports)
            for prefix in prefix_patterns:
                conditions.append(col(ImportFact.source_literal).like(f"{prefix}%"))

            # Bare name matches for imported_name
            if bare_names:
                conditions.append(col(ImportFact.imported_name).in_(bare_names))

            # Also check imported_name for full module paths
            if candidates:
                conditions.append(col(ImportFact.imported_name).in_(candidates))

            if not conditions:
                return

            stmt = (
                select(ImportFact, File.path)
                .join(File, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                .where(or_(*conditions))
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
            refs = await self._coordinator.get_all_references(def_fact, _context_id=0)
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
        """Use Tantivy index for delete lexical fallback - NOT filesystem scan.

        Searches for all import variants via the index.
        """
        from sqlmodel import select

        from codeplane.index._internal.indexing.module_mapping import file_to_import_candidates
        from codeplane.index.models import File

        # Build search patterns based on target type
        patterns: list[str] = [target]

        # If target looks like a file path, generate all import variants
        if "/" in target or target.endswith(".py"):
            # Look up file info for language-aware variant generation
            language_family: str | None = None
            declared_module: str | None = None
            with self._coordinator.db.session() as session:
                file_record = session.exec(
                    select(File).where(File.path == target.lstrip("./").rstrip("/"))
                ).first()
                if file_record:
                    language_family = file_record.language_family
                    declared_module = file_record.declared_module

            # Add all import variants
            for candidate in file_to_import_candidates(target, language_family, declared_module):
                if candidate not in patterns:
                    patterns.append(candidate)

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
            f"  3. Use write_source to make changes manually\n"
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
        mutation_result = mutation_ops.write_source(edits)

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
