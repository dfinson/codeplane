"""Recon MCP tool — task-aware code discovery.

Collapses the multi-call context-gathering stage (search → scaffold → read_source)
into a single call. Uses BM25 seed selection + structural reranking + graph-walk
expansion to deliver relevant source, scaffolds, and metadata in one response.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.index.models import DefFact
    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Seed selection
_MAX_BM25_CANDIDATES = 40  # Raw candidates from Tantivy
_MAX_SEEDS = 5  # Seeds after structural reranking
_DEFAULT_DEPTH = 1  # Graph expansion depth

# Budget defaults (bytes)
_DEFAULT_BUDGET_BYTES = 15_000
_MAX_BUDGET_BYTES = 30_000

# Per-tier line caps
_SEED_BODY_MAX_LINES = 120
_CALLEE_SIG_MAX_LINES = 5
_CALLER_CONTEXT_LINES = 8  # lines around each caller ref
_MAX_CALLERS_PER_SEED = 5
_MAX_CALLEES_PER_SEED = 15
_MAX_IMPORT_SCAFFOLDS = 5

# Priority tiers for budget allocation
_P1_SEED_BODIES = 1
_P2_CALLEE_SIGS = 2
_P3_CALLER_CONTEXTS = 3
_P4_IMPORT_SCAFFOLDS = 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_sha256(full_path: Path) -> str:
    """Compute SHA256 of file contents."""
    return hashlib.sha256(full_path.read_bytes()).hexdigest()


def _read_lines(full_path: Path, start: int, end: int) -> str:
    """Read lines [start, end] (1-indexed, inclusive) from a file."""
    try:
        text = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    lines = text.splitlines(keepends=True)
    # Clamp to file bounds
    s = max(0, start - 1)
    e = min(len(lines), end)
    return "".join(lines[s:e])


def _def_signature_text(d: DefFact) -> str:
    """Build a compact one-line signature for a DefFact."""
    parts = [f"{d.kind} {d.name}"]
    if d.signature_text:
        sig = d.signature_text if d.signature_text.startswith("(") else f"({d.signature_text})"
        parts.append(sig)
    if d.return_type:
        parts.append(f" -> {d.return_type}")
    return "".join(parts)


async def _file_path_for_id(app_ctx: AppContext, file_id: int) -> str:
    """Resolve a file_id to its repo-relative path."""
    from codeplane.index.models import File as FileModel

    with app_ctx.coordinator.db.session() as session:
        f = session.get(FileModel, file_id)
        return f.path if f else "unknown"


# ---------------------------------------------------------------------------
# Seed Selection
# ---------------------------------------------------------------------------


async def _select_seeds(
    app_ctx: AppContext,
    task: str,
    explicit_seeds: list[str] | None,
    max_seeds: int,
) -> list[DefFact]:
    """Select seed definitions using BM25 + structural reranking.

    1. If explicit seeds are given, resolve them via get_def.
    2. Otherwise, run symbol search + lexical search, merge candidates.
    3. Rerank by hub score (caller count) and deduplicate.
    """
    from codeplane.index._internal.indexing.graph import FactQueries
    from codeplane.index.ops import SearchMode

    coordinator = app_ctx.coordinator
    seeds: list[DefFact] = []

    # Explicit seeds: resolve by name
    if explicit_seeds:
        for name in explicit_seeds[:max_seeds]:
            d = await coordinator.get_def(name)
            if d is not None:
                seeds.append(d)
        if seeds:
            return seeds

    # BM25 candidate generation — symbol + lexical searches merged
    symbol_resp = await coordinator.search(
        task,
        mode=SearchMode.SYMBOL,
        limit=_MAX_BM25_CANDIDATES,
    )
    lexical_resp = await coordinator.search(
        task,
        mode=SearchMode.TEXT,
        limit=_MAX_BM25_CANDIDATES,
    )

    # Merge hits, resolve to DefFacts, deduplicate
    seen_paths: set[tuple[str, int]] = set()
    candidate_defs: list[DefFact] = []

    with coordinator.db.session() as session:
        fq = FactQueries(session)

        for hit in symbol_resp.results + lexical_resp.results:
            key = (hit.path, hit.line)
            if key in seen_paths:
                continue
            seen_paths.add(key)

            # Find the DefFact at this location
            file_rec = fq.get_file_by_path(hit.path)
            if file_rec is None or file_rec.id is None:
                continue

            # Find defs that contain (or start at) this hit line
            defs_in_file = fq.list_defs_in_file(file_rec.id, limit=500)
            for d in defs_in_file:
                if d.start_line <= hit.line <= d.end_line:
                    if d.def_uid not in {c.def_uid for c in candidate_defs}:
                        candidate_defs.append(d)
                    break

    if not candidate_defs:
        return []

    # Structural reranking: score by hub-ness (caller count)
    scored: list[tuple[DefFact, int]] = []
    with coordinator.db.session() as session:
        fq = FactQueries(session)
        for d in candidate_defs:
            caller_count = fq.count_callers(d.def_uid)
            scored.append((d, caller_count))

    # Sort by caller count descending (hubs first), then by BM25 position (original order)
    scored.sort(key=lambda x: -x[1])

    # Take top seeds, but ensure file diversity (max 2 seeds per file)
    file_counts: dict[int, int] = {}
    for d, _score in scored:
        fid = d.file_id
        if file_counts.get(fid, 0) >= 2:
            continue
        file_counts[fid] = file_counts.get(fid, 0) + 1
        seeds.append(d)
        if len(seeds) >= max_seeds:
            break

    return seeds


# ---------------------------------------------------------------------------
# Graph Expansion
# ---------------------------------------------------------------------------


async def _expand_seed(
    app_ctx: AppContext,
    seed: DefFact,
    repo_root: Path,
    *,
    depth: int = 1,
) -> dict[str, Any]:
    """Expand a single seed via graph walk.

    Returns a dict with:
      - seed_body: source text of the seed definition
      - callee_sigs: signatures of symbols it calls
      - callers: context snippets around call sites
      - imports: scaffold of files the seed's file imports from
    """
    coordinator = app_ctx.coordinator

    seed_path = await _file_path_for_id(app_ctx, seed.file_id)
    full_path = repo_root / seed_path

    result: dict[str, Any] = {
        "path": seed_path,
        "symbol": _def_signature_text(seed),
        "kind": seed.kind,
        "span": {"start_line": seed.start_line, "end_line": seed.end_line},
    }

    # P1: Seed body (source text)
    if full_path.exists():
        body_end = min(seed.end_line, seed.start_line + _SEED_BODY_MAX_LINES - 1)
        result["source"] = _read_lines(full_path, seed.start_line, body_end)
        result["file_sha256"] = _compute_sha256(full_path)
        if seed.end_line > body_end:
            result["truncated"] = True
            result["total_lines"] = seed.end_line - seed.start_line + 1

    if depth < 1:
        return result

    # P2: Callees — signatures of symbols this seed references
    callees = await coordinator.get_callees(seed, limit=_MAX_CALLEES_PER_SEED)
    callee_sigs: list[dict[str, str]] = []
    for c in callees:
        # Skip self-references
        if c.def_uid == seed.def_uid:
            continue
        c_path = await _file_path_for_id(app_ctx, c.file_id)
        callee_sigs.append(
            {
                "symbol": _def_signature_text(c),
                "path": c_path,
                "span": f"{c.start_line}-{c.end_line}",
            }
        )
    if callee_sigs:
        result["callees"] = callee_sigs

    # P3: Callers — context snippets around references to this seed
    refs = await coordinator.get_references(seed, _context_id=0, limit=50)
    caller_snippets: list[dict[str, Any]] = []
    seen_caller_files: set[str] = set()
    for ref in refs:
        if len(caller_snippets) >= _MAX_CALLERS_PER_SEED:
            break
        ref_path = await _file_path_for_id(app_ctx, ref.file_id)
        # Skip same-file self-references
        if (
            ref_path == seed_path
            and ref.start_line >= seed.start_line
            and ref.start_line <= seed.end_line
        ):
            continue
        # File diversity: max 1 caller per file
        if ref_path in seen_caller_files:
            continue
        seen_caller_files.add(ref_path)

        ref_full = repo_root / ref_path
        if ref_full.exists():
            ctx_start = max(1, ref.start_line - _CALLER_CONTEXT_LINES // 2)
            ctx_end = ref.start_line + _CALLER_CONTEXT_LINES // 2
            snippet = _read_lines(ref_full, ctx_start, ctx_end)
            caller_snippets.append(
                {
                    "path": ref_path,
                    "line": ref.start_line,
                    "context": snippet,
                }
            )
    if caller_snippets:
        result["callers"] = caller_snippets

    return result


async def _build_import_scaffolds(
    app_ctx: AppContext,
    seed_paths: set[str],
    repo_root: Path,
) -> list[dict[str, Any]]:
    """Build lightweight scaffolds for files imported by seed files.

    Only includes files not already covered as seeds.
    """
    from codeplane.mcp.tools.files import _build_scaffold

    coordinator = app_ctx.coordinator
    imported_paths: set[str] = set()

    for spath in seed_paths:
        imports = await coordinator.get_file_imports(spath, limit=50)
        for imp in imports:
            if imp.resolved_path and imp.resolved_path not in seed_paths:
                imported_paths.add(imp.resolved_path)

    scaffolds: list[dict[str, Any]] = []
    for imp_path in sorted(imported_paths)[:_MAX_IMPORT_SCAFFOLDS]:
        full = repo_root / imp_path
        if full.exists():
            scaffold = await _build_scaffold(app_ctx, imp_path, full)
            scaffolds.append(scaffold)

    return scaffolds


# ---------------------------------------------------------------------------
# Budget Assembly
# ---------------------------------------------------------------------------


def _estimate_bytes(obj: Any) -> int:
    """Rough byte estimate of a JSON-serializable object."""
    import json

    return len(json.dumps(obj, default=str).encode("utf-8"))


def _trim_to_budget(result: dict[str, Any], budget: int) -> dict[str, Any]:
    """Trim response to fit within budget, removing lowest-priority content first.

    Priority (keep order): seeds > callees > callers > scaffolds
    """
    current = _estimate_bytes(result)
    if current <= budget:
        return result

    # Trim P4: import scaffolds
    if "import_scaffolds" in result:
        while result["import_scaffolds"] and _estimate_bytes(result) > budget:
            result["import_scaffolds"].pop()
        if not result["import_scaffolds"]:
            del result["import_scaffolds"]
        if _estimate_bytes(result) <= budget:
            return result

    # Trim P3: callers within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "callers" in seed_data:
                while seed_data["callers"] and _estimate_bytes(result) > budget:
                    seed_data["callers"].pop()
                if not seed_data["callers"]:
                    del seed_data["callers"]

    if _estimate_bytes(result) <= budget:
        return result

    # Trim P2: callees within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "callees" in seed_data:
                while seed_data["callees"] and _estimate_bytes(result) > budget:
                    seed_data["callees"].pop()
                if not seed_data["callees"]:
                    del seed_data["callees"]

    return result


# ---------------------------------------------------------------------------
# Tool Summaries
# ---------------------------------------------------------------------------


def _summarize_recon(
    seed_count: int,
    callee_count: int,
    caller_count: int,
    scaffold_count: int,
    task_preview: str,
) -> str:
    """Generate summary for recon response."""
    parts = [f'{seed_count} seeds for "{task_preview}"']
    if callee_count:
        parts.append(f"{callee_count} callees")
    if caller_count:
        parts.append(f"{caller_count} callers")
    if scaffold_count:
        parts.append(f"{scaffold_count} scaffolds")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------


def register_tools(mcp: FastMCP, app_ctx: AppContext) -> None:
    """Register recon tool with FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Recon: task-aware code discovery",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def recon(
        ctx: Context,
        task: str = Field(
            description=(
                "Natural language description of the task. "
                "Used for BM25 seed selection. Be specific: include symbol names, "
                "file patterns, or domain terms when known."
            ),
        ),
        seeds: list[str] | None = Field(
            None,
            description=(
                "Optional explicit seed symbol names (e.g., ['IndexCoordinator', 'FactQueries']). "
                "If provided, skips BM25 search and expands directly from these definitions."
            ),
        ),
        depth: int = Field(
            default=_DEFAULT_DEPTH,
            ge=0,
            le=2,
            description=(
                "Graph expansion depth. 0 = seeds only (no callees/callers). "
                "1 = one hop (default). 2 = two hops (expensive)."
            ),
        ),
        budget: int = Field(
            default=_DEFAULT_BUDGET_BYTES,
            le=_MAX_BUDGET_BYTES,
            description="Response size budget in bytes. Content is trimmed by priority to fit.",
        ),
        max_seeds: int = Field(
            default=_MAX_SEEDS,
            ge=1,
            le=10,
            description="Maximum number of seed definitions to expand.",
        ),
    ) -> dict[str, Any]:
        """Task-aware code discovery in a single call.

        Replaces the multi-call pattern: search → read_scaffold → read_source.
        Given a task description, finds relevant code via BM25 seed selection,
        reranks by structural importance (hub score), then graph-walks to
        deliver seed bodies, callee signatures, caller contexts, and import
        scaffolds — all in one response.

        Returns file_sha256 per source file for write_source compatibility.
        """
        coordinator = app_ctx.coordinator
        repo_root = coordinator.repo_root

        # Step 1: Seed selection (BM25 + structural rerank)
        selected_seeds = await _select_seeds(app_ctx, task, seeds, max_seeds)

        if not selected_seeds:
            task_preview = task[:40] + "..." if len(task) > 40 else task
            return {
                "seeds": [],
                "summary": _summarize_recon(0, 0, 0, 0, task_preview),
                "agentic_hint": (
                    "No relevant definitions found. Try: "
                    "(1) use search(mode='lexical') for text patterns, "
                    "(2) use map_repo to browse the repo structure, "
                    "(3) rephrase the task with specific symbol names."
                ),
            }

        # Step 2: Expand each seed
        seed_results: list[dict[str, Any]] = []
        seed_paths: set[str] = set()
        total_callees = 0
        total_callers = 0

        for seed_def in selected_seeds:
            expanded = await _expand_seed(app_ctx, seed_def, repo_root, depth=depth)
            seed_results.append(expanded)
            seed_paths.add(expanded["path"])
            total_callees += len(expanded.get("callees", []))
            total_callers += len(expanded.get("callers", []))

        # Step 3: Import scaffolds for seed files
        scaffolds: list[dict[str, Any]] = []
        if depth >= 1:
            scaffolds = await _build_import_scaffolds(app_ctx, seed_paths, repo_root)

        # Step 4: Assemble response
        task_preview = task[:40] + "..." if len(task) > 40 else task
        response: dict[str, Any] = {
            "seeds": seed_results,
            "summary": _summarize_recon(
                len(seed_results),
                total_callees,
                total_callers,
                len(scaffolds),
                task_preview,
            ),
        }

        if scaffolds:
            response["import_scaffolds"] = scaffolds

        # Step 5: Budget trimming
        response = _trim_to_budget(response, budget)

        # Agentic hint
        seed_paths_list = sorted(seed_paths)
        response["agentic_hint"] = (
            f"Recon found {len(seed_results)} seed(s) across: {', '.join(seed_paths_list)}. "
            "Use write_source with file_sha256 from the seed source to edit. "
            "Use read_source for additional spans. Use checkpoint after edits."
        )

        from codeplane.mcp.delivery import wrap_existing_response

        return wrap_existing_response(
            response,
            resource_kind="recon_result",
        )
