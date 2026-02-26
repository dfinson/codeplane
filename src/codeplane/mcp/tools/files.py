"""File helpers — scaffold builders, scope manager, SHA256 computation.

These helpers are used by the recon pipeline, resolve tool, and other subsystems.
The MCP tool handlers that were previously in this module (read_source,
read_file_full, list_files, reset_budget) have been removed in v2.
"""

import hashlib
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeplane.core.languages import EXTENSION_TO_NAME
from codeplane.mcp.delivery import ScopeManager

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# Global scope manager for budget tracking
_scope_manager = ScopeManager()


def _compute_file_sha256(full_path: Any) -> str:
    """Compute SHA256 of entire file contents."""
    content = full_path.read_bytes()
    return hashlib.sha256(content).hexdigest()


# =============================================================================
# Parameter Models (used by tests and other modules)
# =============================================================================


class SpanTarget(BaseModel):
    """Span-based read target. Both start_line and end_line are required."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path relative to repo root")
    start_line: int = Field(..., gt=0, description="Start line (1-indexed, inclusive)")
    end_line: int = Field(..., gt=0, description="End line (1-indexed, inclusive)")

    @model_validator(mode="after")
    def validate_range(self) -> "SpanTarget":
        if self.end_line < self.start_line:
            raise ValueError(
                f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
            )
        return self


class StructuralTarget(BaseModel):
    """Structural-unit-based read target."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path relative to repo root")
    symbol_id: str = Field(..., description="Symbol identifier (e.g. qualified name)")
    unit: Literal["function", "class", "signature", "docstring"] = Field(
        "function", description="Structural unit to retrieve"
    )


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_read(files: list[dict[str, Any]], not_found: int = 0) -> str:
    """Generate summary for file read results."""
    from codeplane.core.formatting import compress_path, format_path_list, pluralize

    if not files and not_found:
        return f"{not_found} file(s) not found"

    total_lines = sum(f.get("line_count", 0) for f in files)
    paths = [f["path"] for f in files]

    if len(paths) == 1:
        compressed = compress_path(paths[0], 35)
        rng = files[0].get("range")
        if rng:
            return f"1 file ({compressed}:{rng[0]}-{rng[1]}), {total_lines} lines"
        return f"1 file ({compressed}), {total_lines} lines"

    compressed_paths = [compress_path(p, 20) for p in paths]
    path_list = format_path_list(compressed_paths, max_total=40, compress=False)
    suffix = f", {not_found} not found" if not_found else ""
    return f"{pluralize(len(files), 'file')} ({path_list}), {total_lines} lines{suffix}"


def _summarize_list(path: str, total: int, truncated: bool) -> str:
    """Generate summary for directory listing."""
    loc = path or "repo root"
    trunc = " (truncated)" if truncated else ""
    return f"{total} entries in {loc}{trunc}"


async def _build_scaffold(
    app_ctx: "AppContext",
    rel_path: str,
    full_path: Any,
    *,
    include_docstrings: bool = False,
    include_constants: bool = False,
) -> dict[str, Any]:
    """Build a scaffold response for a file.

    Queries the structural index for DefFacts and ImportFacts, then assembles
    a hierarchical scaffold view with symbols organized by scope.
    """
    from pathlib import Path

    from codeplane.index._internal.indexing.graph import FactQueries
    from codeplane.index.models import DefFact, File, ImportFact

    # Look up the file in the index
    file_rec: File | None = None
    with app_ctx.coordinator.db.session() as session:
        from sqlmodel import select

        stmt = select(File).where(File.path == rel_path)
        file_rec = session.exec(stmt).first()

    if file_rec is None or file_rec.id is None:
        # Unindexed file fallback: return line count and hint
        return _build_unindexed_fallback(full_path, rel_path)

    # Query defs and imports for this file
    defs: list[DefFact] = []
    imports: list[ImportFact] = []
    with app_ctx.coordinator.db.session() as session:
        fq = FactQueries(session)
        defs = fq.list_defs_in_file(file_rec.id, limit=5000)
        imports = fq.list_imports(file_rec.id, limit=1000)

    # Detect language from extension
    ext = Path(rel_path).suffix.lower()
    language = EXTENSION_TO_NAME.get(ext, "unknown")

    # Group imports by source into compact text lines
    from collections import defaultdict

    source_groups: dict[str, list[str]] = defaultdict(list)
    bare_imports: list[str] = []
    for imp in imports:
        name = imp.imported_name
        if imp.alias:
            name = f"{name} as {imp.alias}"
        if imp.source_literal and imp.source_literal != imp.imported_name:
            source_groups[imp.source_literal].append(name)
        else:
            bare_imports.append(name)

    imports_out: list[str] = bare_imports[:]
    for source, names in sorted(source_groups.items()):
        imports_out.append(f"{source}: {', '.join(names)}")

    # Filter defs based on include_constants
    constant_kinds = frozenset({"variable", "constant", "val", "var", "property", "field"})
    filtered_defs = [d for d in defs if include_constants or d.kind not in constant_kinds]

    # Build symbol tree (hierarchical)
    symbols_out = _build_symbol_tree(
        filtered_defs,
        include_docstrings=include_docstrings,
    )

    # Compute file line count
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        total_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    except Exception:
        total_lines = 0

    result: dict[str, Any] = {
        "path": rel_path,
        "language": language,
        "total_lines": total_lines,
        "indexed": True,
        "imports": imports_out,
        "symbols": symbols_out,
        "summary": (
            f"scaffold: {rel_path} — {len(imports_out)} imports, "
            f"{len(filtered_defs)} symbols, {total_lines} lines"
        ),
    }
    return result


def _build_symbol_tree(
    defs: list[Any],
    *,
    include_docstrings: bool = False,
) -> list[str]:
    """Organize DefFacts into compact one-line text summaries.

    Each symbol becomes a single line like:
        class SpanTarget  [63-78]
          method validate_range(self) -> SpanTarget  @model_validator  [73-78]
        function _compute_sha256(full_path) -> str  [52-55]

    Nesting is expressed via 2-space indentation (line-range containment).
    """
    import json as _json

    # Sort by start_line for stable ordering
    sorted_defs = sorted(defs, key=lambda d: (d.start_line, d.start_col))

    container_kinds = frozenset(
        {
            "class",
            "struct",
            "enum",
            "interface",
            "trait",
            "module",
            "namespace",
            "impl",
            "protocol",
            "object",
            "record",
            "type_class",
        }
    )

    lines: list[str] = []
    # Stack of (end_line, depth) for nesting
    stack: list[tuple[int, int]] = []

    for d in sorted_defs:
        # Pop stack entries that this symbol is NOT contained within
        while stack and d.start_line >= stack[-1][0]:
            stack.pop()

        depth = len(stack)
        indent = "  " * depth

        # Build compact one-line summary
        parts: list[str] = [f"{d.kind} {d.name}"]

        if d.signature_text:
            sig = d.signature_text
            if not sig.startswith("("):
                sig = f"({sig})"
            parts.append(sig)

        if d.return_type:
            parts.append(f" -> {d.return_type}")

        if d.decorators_json:
            import contextlib

            with contextlib.suppress(ValueError, TypeError):
                dec_list = _json.loads(d.decorators_json)
                if dec_list:
                    # Strip leading @ if already present in stored strings
                    cleaned = [s.lstrip("@") for s in dec_list]
                    parts.append(f"  @{', @'.join(cleaned)}")

        parts.append(f"  [{d.start_line}-{d.end_line}]")

        lines.append(f"{indent}{''.join(parts)}")

        if include_docstrings and d.docstring:
            # Docstring as indented line below
            lines.append(f'{indent}  "{d.docstring}"')

        # If this is a container, push onto stack
        if d.kind in container_kinds:
            stack.append((d.end_line, depth + 1))

    return lines


def _build_unindexed_fallback(full_path: Any, rel_path: str) -> dict[str, Any]:
    """Fallback for files not in the structural index."""
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        total_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    except Exception:
        total_lines = 0

    return {
        "path": rel_path,
        "indexed": False,
        "total_lines": total_lines,
        "symbols": [],
        "imports": [],
        "summary": f"unindexed: {rel_path}, {total_lines} lines",
        "agentic_hint": (
            "This file is not in the structural index. Use recon_resolve to fetch its full content."
        ),
    }


async def _build_lite_scaffold(
    app_ctx: "AppContext",
    rel_path: str,
    full_path: Any,
) -> dict[str, Any]:
    """Build a lightweight scaffold with only symbol names and import sources.

    Costs ~74 tokens per file vs ~65 for a bare summary_line and ~188 for
    a full scaffold.  Gives agents enough signal (function/class names +
    dependency modules + file size) to judge edit-relevance without the
    overhead of full signatures, decorators, or line ranges.
    """

    from codeplane.index._internal.indexing.graph import FactQueries
    from codeplane.index.models import DefFact, File, ImportFact

    # Compute line count
    try:
        raw_text = full_path.read_text(encoding="utf-8", errors="replace")
        total_lines = raw_text.count("\n") + (1 if raw_text and not raw_text.endswith("\n") else 0)
    except Exception:
        total_lines = 0

    # Look up file in the index
    file_rec: File | None = None
    with app_ctx.coordinator.db.session() as session:
        from sqlmodel import select

        stmt = select(File).where(File.path == rel_path)
        file_rec = session.exec(stmt).first()

    if file_rec is None or file_rec.id is None:
        return {"total_lines": total_lines, "imports": [], "symbols": []}

    # Query defs and imports
    defs: list[DefFact] = []
    imports: list[ImportFact] = []
    with app_ctx.coordinator.db.session() as session:
        fq = FactQueries(session)
        defs = fq.list_defs_in_file(file_rec.id, limit=5000)
        imports = fq.list_imports(file_rec.id, limit=1000)

    # Import sources only — deduplicated module names
    sources: set[str] = set()
    for imp in imports:
        if imp.source_literal and imp.source_literal != imp.imported_name:
            sources.add(imp.source_literal)
        else:
            sources.add(imp.imported_name)
    import_sources = sorted(sources)

    # Symbol names only — "kind name", skip constants/variables
    constant_kinds = frozenset({"variable", "constant", "val", "var", "property", "field"})
    symbol_names = [f"{d.kind} {d.name}" for d in defs if d.kind not in constant_kinds]

    return {
        "total_lines": total_lines,
        "imports": import_sources,
        "symbols": symbol_names,
    }
