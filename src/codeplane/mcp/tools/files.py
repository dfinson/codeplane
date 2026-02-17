"""Files MCP tools - read_source, read_file_full, list_files handlers.

Three-tool read model:
- read_source: bounded semantic retrieval (span-based or structural-unit-based)
- read_file_full: gated bulk access (two-phase confirmation, resource-first delivery)
- list_files: directory listing (unchanged)
"""

import hashlib
import secrets
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeplane.config.constants import (
    FILES_LIST_MAX,
    MAX_SPAN_LINES,
    MAX_TARGETS_PER_CALL,
    SMALL_FILE_THRESHOLD,
)
from codeplane.mcp.delivery import ScopeManager, build_envelope
from codeplane.mcp.errors import (
    MCPError,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# Session key for read confirmation tokens
_READ_CONFIRM_TOKEN_KEY = "__read_confirmation_token__"

# Global scope manager for file tools
_scope_manager = ScopeManager()


def _compute_file_sha256(full_path: Any) -> str:
    """Compute SHA256 of entire file contents."""
    content = full_path.read_bytes()
    return hashlib.sha256(content).hexdigest()


# =============================================================================
# Parameter Models
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
    """Generate summary for read_source."""
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
    """Generate summary for files.list."""
    loc = path or "repo root"
    trunc = " (truncated)" if truncated else ""
    return f"{total} entries in {loc}{trunc}"


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register file tools with FastMCP server."""

    @mcp.tool
    async def read_source(
        ctx: Context,
        targets: list[SpanTarget] | None = Field(
            None,
            description=(
                "Span-based targets. Each specifies path + start_line + end_line (both required)."
            ),
        ),
        structural_targets: list[StructuralTarget] | None = Field(
            None,
            description="Structural-unit targets. Each specifies path + symbol_id + unit.",
        ),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
        confirm_reason: str | None = Field(
            None,
            description="Reason for exceeding caps (min 15 chars). Required with confirmation_token.",
        ),
        confirmation_token: str | None = Field(
            None, description="Token from a previous blocked call."
        ),
    ) -> dict[str, Any]:
        """Bounded semantic retrieval of source code.

        Dual addressing:
        - Span: [{path, start_line, end_line}] — both lines required
        - Structural: [{path, symbol_id, unit}] — unit: function|class|signature|docstring

        Returns file_sha256 per file (for span-based edits).
        Hard caps enforced: max 500 lines/span, 20KB total, 20 targets/call.
        Exceeding caps requires two-phase confirmation.
        """
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        all_targets: list[SpanTarget | StructuralTarget] = []
        if targets:
            all_targets.extend(targets)
        if structural_targets:
            all_targets.extend(structural_targets)

        if not all_targets:
            return {"error": "At least one target (span or structural) is required."}

        # Check caps and require confirmation if exceeded
        needs_confirmation = False
        cap_reasons: list[str] = []

        if len(all_targets) > MAX_TARGETS_PER_CALL:
            needs_confirmation = True
            cap_reasons.append(f"{len(all_targets)} targets exceeds cap of {MAX_TARGETS_PER_CALL}")

        span_targets = [t for t in all_targets if isinstance(t, SpanTarget)]
        for t in span_targets:
            span_lines = t.end_line - t.start_line + 1
            if span_lines > MAX_SPAN_LINES:
                needs_confirmation = True
                cap_reasons.append(
                    f"{t.path}:{t.start_line}-{t.end_line} ({span_lines} lines) exceeds cap of {MAX_SPAN_LINES}"
                )

        if needs_confirmation:
            # Check for valid confirmation
            stored_token = session.fingerprints.get(_READ_CONFIRM_TOKEN_KEY)
            if (
                confirmation_token
                and confirm_reason
                and len(confirm_reason) >= 15
                and stored_token
                and confirmation_token == stored_token
            ):
                # Confirmed — clear token and proceed
                session.fingerprints.pop(_READ_CONFIRM_TOKEN_KEY, None)
            else:
                # Generate token and block
                token = secrets.token_hex(16)
                session.fingerprints[_READ_CONFIRM_TOKEN_KEY] = token
                return {
                    "status": "blocked",
                    "confirmation_token": token,
                    "reason": f"Read exceeds caps: {'; '.join(cap_reasons)}",
                    "cap_violations": cap_reasons,
                    "agentic_hint": (
                        f"To proceed, retry with confirmation_token='{token}' "
                        f"AND confirm_reason='<reason min 15 chars>'."
                    ),
                }

        # Process targets

        from codeplane.files.ops import validate_path_in_repo

        files_out: list[dict[str, Any]] = []
        not_found: list[str] = []
        total_bytes = 0

        # Process span targets
        for t in span_targets:
            try:
                full_path = validate_path_in_repo(app_ctx.repo_root, t.path)
            except MCPError:
                not_found.append(t.path)
                continue

            if not full_path.is_file():
                not_found.append(t.path)
                continue

            content = full_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines(keepends=True)
            # start_line/end_line are 1-based inclusive. Convert to 0-based
            # Python slice: [start-1 : end] gives inclusive range.
            start_idx = max(0, t.start_line - 1)
            end_idx = min(len(lines), t.end_line)
            span_content = "".join(lines[start_idx:end_idx])
            line_count = end_idx - start_idx
            line_count = end_idx - start_idx

            file_sha = _compute_file_sha256(full_path)
            total_bytes += len(span_content.encode("utf-8"))

            from codeplane.core.languages import EXTENSION_TO_NAME

            lang = EXTENSION_TO_NAME.get(full_path.suffix.lower(), "unknown")

            files_out.append(
                {
                    "path": t.path,
                    "content": span_content,
                    "language": lang,
                    "line_count": line_count,
                    "range": [t.start_line, t.end_line],
                    "file_sha256": file_sha,
                }
            )

        # Process structural targets
        struct_targets: list[StructuralTarget] = [
            t for t in all_targets if isinstance(t, StructuralTarget)
        ]
        for st in struct_targets:
            try:
                full_path = validate_path_in_repo(app_ctx.repo_root, st.path)
            except MCPError:
                not_found.append(st.path)
                continue

            if not full_path.is_file():
                not_found.append(st.path)
                continue

            file_sha = _compute_file_sha256(full_path)

            # Look up symbol in index
            resolved = await _resolve_structural_target(app_ctx, st)
            if resolved is None:
                files_out.append(
                    {
                        "path": st.path,
                        "error": f"Symbol '{st.symbol_id}' not found in {st.path}",
                        "file_sha256": file_sha,
                    }
                )
                continue

            content, start_line, end_line = resolved
            total_bytes += len(content.encode("utf-8"))

            from codeplane.core.languages import EXTENSION_TO_NAME

            lang = EXTENSION_TO_NAME.get(full_path.suffix.lower(), "unknown")

            files_out.append(
                {
                    "path": st.path,
                    "content": content,
                    "language": lang,
                    "line_count": end_line - start_line + 1,
                    "range": [start_line, end_line],
                    "file_sha256": file_sha,
                    "symbol_id": st.symbol_id,
                    "unit": st.unit,
                }
            )

        # Track scope usage
        scope_usage = None
        if scope_id:
            budget = _scope_manager.get_or_create(scope_id)
            budget.increment_read(total_bytes)
            exceeded = budget.check_budget("read_bytes")
            if exceeded:
                from codeplane.mcp.errors import BudgetExceededError

                raise BudgetExceededError(scope_id, "read_bytes", exceeded)
            scope_usage = budget.to_usage_dict()

        summary = _summarize_read(files_out, len(not_found))

        response: dict[str, Any] = {
            "files": files_out,
            "summary": summary,
        }
        if not_found:
            response["not_found"] = not_found

        return build_envelope(
            response,
            resource_kind="source",
            scope_id=scope_id,
            scope_usage=scope_usage,
            inline_summary=summary,
        )

    @mcp.tool
    async def read_file_full(
        ctx: Context,
        paths: list[str] = Field(..., description="File paths relative to repo root"),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
        confirm_reason: str | None = Field(
            None,
            description="Reason for reading large file (min 15 chars). Required with confirmation_token.",
        ),
        confirmation_token: str | None = Field(
            None, description="Token from a previous blocked call."
        ),
    ) -> dict[str, Any]:
        """Gated bulk file access with two-phase confirmation.

        Reads entire files. Files above the small threshold require two-phase confirmation.
        Returns not_found list for missing paths.
        Default delivery=resource when supported, paged otherwise, inline only for tiny files.
        """
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        from codeplane.files.ops import validate_path_in_repo

        # Pre-check file sizes
        large_files: list[str] = []
        for p in paths:
            try:
                full = validate_path_in_repo(app_ctx.repo_root, p)
                if full.is_file() and full.stat().st_size > SMALL_FILE_THRESHOLD:
                    large_files.append(p)
            except (MCPError, OSError):
                pass

        if large_files:
            stored_token = session.fingerprints.get(_READ_CONFIRM_TOKEN_KEY)
            if (
                confirmation_token
                and confirm_reason
                and len(confirm_reason) >= 15
                and stored_token
                and confirmation_token == stored_token
            ):
                session.fingerprints.pop(_READ_CONFIRM_TOKEN_KEY, None)
            else:
                token = secrets.token_hex(16)
                session.fingerprints[_READ_CONFIRM_TOKEN_KEY] = token
                return {
                    "status": "blocked",
                    "confirmation_token": token,
                    "reason": f"Full read of {len(large_files)} large file(s): {', '.join(large_files[:5])}",
                    "large_files": large_files,
                    "agentic_hint": (
                        f"To proceed, retry with confirmation_token='{token}' "
                        f"AND confirm_reason='<reason min 15 chars>'."
                    ),
                }

        # Read files
        files_out: list[dict[str, Any]] = []
        not_found: list[str] = []
        total_bytes = 0
        warnings: list[dict[str, Any]] = []

        for p in paths:
            try:
                full_path = validate_path_in_repo(app_ctx.repo_root, p)
            except MCPError:
                not_found.append(p)
                continue

            if not full_path.is_file():
                not_found.append(p)
                continue

            content = full_path.read_text(encoding="utf-8", errors="replace")
            file_sha = _compute_file_sha256(full_path)
            byte_count = len(content.encode("utf-8"))
            total_bytes += byte_count

            from codeplane.core.languages import EXTENSION_TO_NAME

            lang = EXTENSION_TO_NAME.get(full_path.suffix.lower(), "unknown")
            line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

            files_out.append(
                {
                    "path": p,
                    "content": content,
                    "language": lang,
                    "line_count": line_count,
                    "file_sha256": file_sha,
                    "byte_size": byte_count,
                }
            )

            # Check duplicate full read
            if scope_id:
                budget = _scope_manager.get_or_create(scope_id)
                dup_warning = budget.check_duplicate_read(p)
                if dup_warning:
                    warnings.append(dup_warning)

        # Track scope usage
        scope_usage = None
        if scope_id:
            budget = _scope_manager.get_or_create(scope_id)
            for p in [f["path"] for f in files_out]:
                byte_size = next((f["byte_size"] for f in files_out if f["path"] == p), 0)
                budget.increment_full_read(p, byte_size)
            exceeded = budget.check_budget("full_reads")
            if exceeded:
                from codeplane.mcp.errors import BudgetExceededError

                raise BudgetExceededError(scope_id, "full_reads", exceeded)
            scope_usage = budget.to_usage_dict()

        summary = _summarize_read(files_out, len(not_found))
        response: dict[str, Any] = {
            "files": files_out,
            "summary": summary,
        }
        if not_found:
            response["not_found"] = not_found
        if warnings:
            response["warnings"] = warnings

        return build_envelope(
            response,
            resource_kind="source",
            scope_id=scope_id,
            scope_usage=scope_usage,
            inline_summary=summary,
        )

    @mcp.tool
    async def list_files(
        ctx: Context,
        path: str | None = Field(
            None, description="Directory path relative to repo root (default: repo root)"
        ),
        pattern: str | None = Field(
            None, description="Glob pattern to filter (e.g., '*.py', '**/*.ts')"
        ),
        recursive: bool = Field(False, description="Recurse into subdirectories"),
        include_hidden: bool = Field(False, description="Include dotfiles and dotdirs"),
        include_metadata: bool = Field(False, description="Include size and mtime for files"),
        file_type: Literal["all", "file", "directory"] = Field(
            "all", description="Filter by entry type"
        ),
        limit: int = Field(200, ge=1, le=FILES_LIST_MAX, description="Maximum entries to return"),
    ) -> dict[str, Any]:
        """List files and directories with optional filtering."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = app_ctx.file_ops.list_files(
            path=path,
            pattern=pattern,
            recursive=recursive,
            include_hidden=include_hidden,
            include_metadata=include_metadata,
            file_type=file_type,
            limit=limit,
        )

        return {
            "path": result.path,
            "entries": [
                {
                    "name": e.name,
                    "path": e.path,
                    "type": e.type,
                    **(
                        {"size": e.size, "modified_at": e.modified_at}
                        if include_metadata and e.type == "file"
                        else {}
                    ),
                }
                for e in result.entries
            ],
            "total": result.total,
            "truncated": result.truncated,
            "summary": _summarize_list(result.path, result.total, result.truncated),
        }

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)


async def _resolve_structural_target(
    app_ctx: "AppContext",
    target: StructuralTarget,
) -> tuple[str, int, int] | None:
    """Resolve a structural target to (content, start_line, end_line).

    Returns None if the symbol is not found.
    """
    from codeplane.index._internal.indexing import resolve_scope_region_for_path

    # Look up symbol definition
    def_fact = await app_ctx.coordinator.get_def(target.symbol_id, context_id=None)
    if def_fact is None:
        return None

    # Get file path
    from codeplane.index.models import File

    with app_ctx.coordinator.db.session() as session:
        file_rec = session.get(File, def_fact.file_id)
        if file_rec is None:
            return None
        file_path = file_rec.path

    # Verify it's the right file
    if file_path != target.path:
        # Symbol found but in different file
        return None

    if target.unit == "signature":
        # Return just the signature line
        full_path = app_ctx.repo_root / target.path
        if not full_path.is_file():
            return None
        content = full_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)
        start = def_fact.start_line
        sig_end = start  # Default: single line
        # Try to find the end of the signature (e.g., up to the colon for Python)
        if start <= len(lines):
            sig_end = start
        return "".join(lines[start - 1 : sig_end]), start, sig_end

    if target.unit == "docstring":
        # Return docstring if present
        full_path = app_ctx.repo_root / target.path
        if not full_path.is_file():
            return None
        content = full_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)
        # Simple heuristic: lines immediately after definition that are docstrings
        start = def_fact.start_line  # 1-indexed
        if start < len(lines):
            # start is 1-indexed, so lines[start] is the line after the def.
            idx = start
            while idx < len(lines) and idx < start + 5:
                line = lines[idx].strip()
                if line.startswith(('"""', "'''")):
                    doc_start = idx + 1
                    doc_end = doc_start
                    while doc_end < len(lines):
                        if lines[doc_end].strip().endswith(('"""', "'''")):
                            return (
                                "".join(lines[doc_start - 1 : doc_end + 1]),
                                doc_start,
                                doc_end + 1,
                            )
                        doc_end += 1
                    return "".join(lines[doc_start - 1 : doc_end]), doc_start, doc_end
                elif line and not line.startswith("#"):
                    break
                idx += 1
        return None

    # function or class unit — use scope resolution
    pref: Literal["function", "class"] = "function" if target.unit == "function" else "class"
    with app_ctx.coordinator.db.session() as session:
        scope_region, content = resolve_scope_region_for_path(
            session,
            app_ctx.coordinator.repo_root,
            target.path,
            def_fact.start_line,
            preference=pref,
            fallback_lines=25,
        )
    return content, scope_region.start_line, scope_region.end_line
