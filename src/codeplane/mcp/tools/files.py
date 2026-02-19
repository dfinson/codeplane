"""Files MCP tools - read_source, read_file_full, list_files handlers.

Three-tool read model:
- read_source: bounded semantic retrieval (span-based or structural-unit-based)
- read_file_full: gated bulk access (two-phase confirmation, resource-first delivery)
- list_files: directory listing (unchanged)
"""

import hashlib
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeplane.config.constants import (
    FILES_LIST_MAX,
    MAX_SPAN_LINES,
    MAX_TARGETS_PER_CALL,
    SMALL_FILE_THRESHOLD,
)
from codeplane.core.languages import EXTENSION_TO_NAME
from codeplane.mcp.delivery import (
    ScopeManager,
    build_envelope,
    resume_cursor,
    wrap_existing_response,
)
from codeplane.mcp.errors import (
    MCPError,
    MCPErrorCode,
)
from codeplane.mcp.gate import (
    EXPENSIVE_READ_GATE,
    READ_CAP_EXCEEDED_GATE,
    build_pattern_gate_spec,
    build_pattern_hint,
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
        cursor: str | None = Field(
            None,
            description="Cursor token from a previous paginated response. Pass to retrieve the next page.",
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
        # Resume paginated cursor if provided
        if cursor:
            page = resume_cursor(cursor)
            if page is None:
                return {
                    "error": "cursor_expired",
                    "message": "Cursor not found or expired. Re-issue the original read.",
                }
            return page

        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        # Evaluate pattern detector before executing read
        pattern_match = session.pattern_detector.evaluate()
        pattern_extras: dict[str, Any] = {}
        if pattern_match and pattern_match.severity == "break":
            gate_spec = build_pattern_gate_spec(pattern_match)
            # If agent provided a valid gate token, validate and proceed
            if confirmation_token:
                reason_str = confirm_reason if isinstance(confirm_reason, str) else ""
                gate_result = session.gate_manager.validate(confirmation_token, reason_str)
                if not gate_result.ok:
                    # Re-issue the gate — token was invalid
                    gate_block = session.gate_manager.issue(gate_spec)
                    return {
                        "status": "blocked",
                        "error": {
                            "code": "GATE_VALIDATION_FAILED",
                            "message": gate_result.error,
                        },
                        "gate": gate_block,
                        **build_pattern_hint(pattern_match),
                    }
                # Token valid — wipe slate and proceed
                session.pattern_detector.clear()
            else:
                # No token — block and return gate
                gate_block = session.gate_manager.issue(gate_spec)
                return {
                    "status": "blocked",
                    "gate": gate_block,
                    **build_pattern_hint(pattern_match),
                }
        elif pattern_match and pattern_match.severity == "warn":
            pattern_extras = build_pattern_hint(pattern_match)

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
            gm = session.gate_manager
            # Phase 2: Validate token + reason
            if confirmation_token:
                reason_str = confirm_reason or ""
                result = gm.validate(confirmation_token, reason_str)
                if not result.ok:
                    return {
                        "status": "blocked",
                        "error": result.error,
                        "hint": result.hint,
                        "summary": "gate validation failed",
                    }
                # Gate passed — proceed
            else:
                # Phase 1: Issue gate and block
                gate_block = gm.issue(READ_CAP_EXCEEDED_GATE)
                return {
                    "status": "blocked",
                    "gate": gate_block,
                    "confirmation_token": gate_block["id"],
                    "reason": f"Read exceeds caps: {'; '.join(cap_reasons)}",
                    "cap_violations": cap_reasons,
                    "agentic_hint": (
                        f"To proceed, retry with confirmation_token='{gate_block['id']}' "
                        f"AND confirm_reason='<reason min {READ_CAP_EXCEEDED_GATE.reason_min_chars} chars>'.\n"
                        f"{READ_CAP_EXCEEDED_GATE.reason_prompt}"
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
            line_count = max(0, end_idx - start_idx)

            file_sha = _compute_file_sha256(full_path)
            total_bytes += len(span_content.encode("utf-8"))

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
            exceeded_counter = "read_bytes"
            if not exceeded:
                exceeded = budget.check_budget("read_calls")
                exceeded_counter = "read_calls"
            if exceeded:
                from codeplane.mcp.errors import BudgetExceededError

                raise BudgetExceededError(scope_id, exceeded_counter, exceeded)
            scope_usage = budget.to_usage_dict()

        summary = _summarize_read(files_out, len(not_found))

        response: dict[str, Any] = {
            "files": files_out,
            "summary": summary,
        }
        if not_found:
            response["not_found"] = not_found
        if pattern_extras:
            response.update(pattern_extras)

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
            description="Reason for reading large file (min 50 chars). Required with confirmation_token.",
        ),
        confirmation_token: str | None = Field(
            None, description="Token from a previous blocked call."
        ),
        cursor: str | None = Field(
            None,
            description="Cursor token from a previous paginated response. Pass to retrieve the next page.",
        ),
    ) -> dict[str, Any]:
        """Gated bulk file access with two-phase confirmation.

        Reads entire files. Files above the small threshold require two-phase confirmation.
        Returns not_found list for missing paths.
        Default delivery=resource when supported, paged otherwise, inline only for tiny files.
        """
        # Resume paginated cursor if provided
        if cursor:
            page = resume_cursor(cursor)
            if page is None:
                return {
                    "error": "cursor_expired",
                    "message": "Cursor not found or expired. Re-issue the original read.",
                }
            return page

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
            gm = session.gate_manager
            # Phase 2: Validate token + reason
            if confirmation_token:
                reason_str = confirm_reason or ""
                result = gm.validate(confirmation_token, reason_str)
                if not result.ok:
                    return {
                        "status": "blocked",
                        "error": result.error,
                        "hint": result.hint,
                        "summary": "gate validation failed",
                    }
                # Gate passed — proceed
            else:
                # Phase 1: Issue gate and block
                gate_block = gm.issue(EXPENSIVE_READ_GATE)
                return {
                    "status": "blocked",
                    "gate": gate_block,
                    "confirmation_token": gate_block["id"],
                    "reason": f"Full read of {len(large_files)} large file(s): {', '.join(large_files[:5])}",
                    "large_files": large_files,
                    "agentic_hint": (
                        f"To proceed, retry with confirmation_token='{gate_block['id']}' "
                        f"AND confirm_reason='<reason min {EXPENSIVE_READ_GATE.reason_min_chars} chars>'.\n"
                        f"{EXPENSIVE_READ_GATE.reason_prompt}"
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

            # Track and check duplicate full read (increment BEFORE check
            # so the duplicate warning fires on the 2nd read, not the 3rd)
            if scope_id:
                budget = _scope_manager.get_or_create(scope_id)
                budget.increment_full_read(p, byte_count)
                dup_warning = budget.check_duplicate_read(p)
                if dup_warning:
                    warnings.append(dup_warning)

        # Check scope budget
        scope_usage = None
        if scope_id:
            budget = _scope_manager.get_or_create(scope_id)
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

    # -------------------------------------------------------------------------
    # Budget reset tool
    # -------------------------------------------------------------------------

    @mcp.tool
    async def read_scaffold(
        ctx: Context,
        path: str = Field(
            ...,
            description="File path relative to repo root.",
        ),
        include_docstrings: bool = Field(
            False,
            description="Include first-paragraph docstrings for each symbol.",
        ),
        include_constants: bool = Field(
            False,
            description="Include module-level constants and variables.",
        ),
    ) -> dict[str, Any]:
        """Semantic scaffold view of a file.

        Returns the structural skeleton: imports, classes, functions, methods,
        their signatures, decorators, return types, and line numbers.
        No source code is returned — only metadata from the structural index.

        For indexed files: structured scaffold from DefFact + ImportFact data.
        For unindexed files: falls back to paginated full-file read with hint.
        """
        from codeplane.files.ops import validate_path_in_repo

        try:
            full_path = validate_path_in_repo(app_ctx.repo_root, path)
        except MCPError:
            return {"error": "file_not_found", "message": f"File not found: {path}"}

        if not full_path.is_file():
            return {"error": "file_not_found", "message": f"Not a file: {path}"}

        scaffold = await _build_scaffold(
            app_ctx,
            path,
            full_path,
            include_docstrings=include_docstrings,
            include_constants=include_constants,
        )
        return wrap_existing_response(
            scaffold,
            resource_kind="scaffold",
            inline_summary=scaffold.get("summary"),
        )

    @mcp.tool
    async def reset_budget(
        ctx: Context,
        scope_id: str = Field(..., description="Scope ID for budget tracking"),
        category: str = Field(
            ...,
            description="Budget category to reset: 'read' or 'search'",
        ),
        justification: str = Field(
            ...,
            description=(
                "Why the reset is needed. Post-mutation: >= 50 chars. "
                "No-mutation ceiling reset: >= 250 chars."
            ),
        ),
    ) -> dict[str, Any]:
        """Request a budget ceiling reset.

        Read budgets become resettable after a mutation (write_source).
        Search budgets become resettable every 3 mutations.
        Pure-read workflows (no mutations) can request resets at ceiling
        with a longer justification (>= 250 chars).
        Returns before/after counter values and total reset count.
        """
        try:
            result = _scope_manager.request_reset(scope_id, category, justification)
        except ValueError as exc:
            raise MCPError(
                code=MCPErrorCode.BUDGET_EXCEEDED,
                message=str(exc),
                remediation=(
                    "Ensure you have mutation eligibility or are at ceiling. "
                    "Provide a justification of appropriate length."
                ),
            ) from exc
        return result


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
    # Verify it's the right file (normalise both to repo-relative)
    def _rel(p: str) -> str:
        from pathlib import Path

        pp = Path(p)
        if pp.is_absolute():
            try:
                return str(pp.relative_to(app_ctx.repo_root))
            except ValueError:
                return p
        return p

    if _rel(file_path) != _rel(target.path):
        # Symbol found but in different file
        return None
    if target.unit == "signature":
        # Return the function/class signature (may span multiple lines)
        full_path = app_ctx.repo_root / target.path
        if not full_path.is_file():
            return None
        content = full_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)
        start = def_fact.start_line  # 1-indexed
        if start < 1 or start > len(lines):
            return None
        sig_start_idx = start - 1  # 0-indexed
        sig_end = start  # 1-indexed inclusive, default: single line
        # Scan forward to find the end of the signature.
        # For Python, the header ends with a colon; cap the lookahead.
        max_lookahead_idx = min(len(lines), sig_start_idx + 25)
        for idx in range(sig_start_idx, max_lookahead_idx):
            stripped = lines[idx].strip()
            sig_end = idx + 1  # 1-indexed
            if stripped.endswith(":"):
                break
        return "".join(lines[sig_start_idx:sig_end]), start, sig_end

    if target.unit == "docstring":
        # Return docstring if present
        full_path = app_ctx.repo_root / target.path
        if not full_path.is_file():
            return None
        content = full_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)

        # First, find the end of the signature (colon-terminated line)
        start = def_fact.start_line  # 1-indexed
        if start < 1 or start > len(lines):
            return None
        sig_end_idx = start - 1  # 0-indexed, default: same as start
        max_sig = min(len(lines), start - 1 + 25)
        for i in range(start - 1, max_sig):
            if lines[i].strip().endswith(":"):
                sig_end_idx = i
                break

        # Search for docstring in lines following the signature
        search_start = sig_end_idx + 1  # 0-indexed
        search_limit = min(len(lines), search_start + 5)
        for idx in range(search_start, search_limit):
            stripped = lines[idx].strip()
            for quote in ('"""', "'''"):
                if stripped.startswith(quote):
                    # Single-line docstring: opening and closing on same line
                    if stripped.endswith(quote) and len(stripped) > len(quote):
                        return lines[idx], idx + 1, idx + 1
                    # Multi-line docstring: scan for closing quote
                    doc_end = idx + 1
                    while doc_end < len(lines):
                        if lines[doc_end].strip().endswith(quote):
                            return (
                                "".join(lines[idx : doc_end + 1]),
                                idx + 1,
                                doc_end + 1,
                            )
                        doc_end += 1
                    # Unterminated docstring — return what we found
                    return "".join(lines[idx:doc_end]), idx + 1, doc_end
            # Skip blank lines and comments between signature and docstring
            if stripped and not stripped.startswith("#"):
                break  # Non-docstring content reached
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
            "This file is not in the structural index. "
            "Use read_source with span targets to read specific line ranges, "
            "or read_file_full for the complete file."
        ),
    }
