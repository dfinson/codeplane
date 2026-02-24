"""Graph expansion — expand seed definitions via structural graph walk.

Single Responsibility: Take a seed DefFact and produce a rich context dict
with source, callees, callers, imports, and siblings.

Uses semantic spans (full DefFact span) instead of arbitrary line caps.
Progressive disclosure: signature+docstring tier for large defs,
full span for normal ones.  Fan-out brake limits total expansion cost.

Depends on: models (constants, classifiers), AppContext (I/O).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeplane.mcp.tools.recon.models import (
    _BARREL_FILENAMES,
    _LARGE_DEF_THRESHOLD_LINES,
    _MAX_CALLEES_PER_SEED,
    _MAX_CALLERS_PER_SEED,
    _MAX_IMPORT_DEFS_PER_SEED,
    _MAX_IMPORT_SCAFFOLDS,
    _SEED_BODY_MAX_LINES,
)

if TYPE_CHECKING:
    from codeplane.index.models import DefFact, RefFact
    from codeplane.mcp.context import AppContext


# ===================================================================
# Low-level helpers
# ===================================================================


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


# ===================================================================
# Graph Expansion
# ===================================================================


async def _expand_seed(
    app_ctx: AppContext,
    seed: DefFact,
    repo_root: Path,
    *,
    depth: int = 1,
    task_terms: list[str] | None = None,
    budget_remaining: int | None = None,  # noqa: ARG001 — kept for signature compat
) -> dict[str, Any]:
    """Expand a single seed via graph walk with semantic spans.

    Returns a compact dict with full source and compressed metadata.
    callees/callers/siblings are string arrays (``"symbol [path:span]"``),
    not nested objects — this cuts per-seed overhead by ~60%.

    Source is always included: recon is ONE call, ALL context.
    Progressive disclosure still applies for very large defs.
    """
    coordinator = app_ctx.coordinator

    seed_path = await _file_path_for_id(app_ctx, seed.file_id)
    full_path = repo_root / seed_path
    seed_line_count = seed.end_line - seed.start_line + 1

    result: dict[str, Any] = {
        "def_uid": seed.def_uid,
        "path": seed_path,
        "symbol": _def_signature_text(seed),
        "kind": seed.kind,
        "span": f"{seed.start_line}-{seed.end_line}",
    }

    # Source — always included (ONE call, ALL context)
    if full_path.exists():
        if seed_line_count > _LARGE_DEF_THRESHOLD_LINES:
            sig_end = min(seed.end_line, seed.start_line + _SEED_BODY_MAX_LINES - 1)
            result["source"] = _read_lines(full_path, seed.start_line, sig_end)
            result["disclosure"] = "partial"
            result["total_lines"] = seed_line_count
        else:
            result["source"] = _read_lines(full_path, seed.start_line, seed.end_line)
        result["file_sha256"] = _compute_sha256(full_path)

    if depth < 1:
        return result

    # Callees — compact string array: "symbol [path:span]"
    callees = await coordinator.get_callees(seed, limit=_MAX_CALLEES_PER_SEED * 2)
    _terms = task_terms or []
    callee_scored: list[tuple[str, float]] = []
    for c in callees:
        if c.def_uid == seed.def_uid:
            continue
        c_path = await _file_path_for_id(app_ctx, c.file_id)
        c_name_lower = c.name.lower()
        relevance = (1.0 if any(t in c_name_lower for t in _terms) else 0.0) if _terms else 0.5
        compact = f"{_def_signature_text(c)} [{c_path}:{c.start_line}-{c.end_line}]"
        callee_scored.append((compact, relevance))

    callee_scored.sort(key=lambda x: -x[1])
    callee_strs = [s for s, _ in callee_scored[:_MAX_CALLEES_PER_SEED]]
    if callee_strs:
        result["callees"] = callee_strs

    # Imports — compact string array
    imports = await coordinator.get_file_imports(seed_path, limit=50)
    import_strs: list[str] = []
    seen_import_paths: set[str] = set()

    seed_dir = str(Path(seed_path).parent) + "/"
    filtered_imports = [
        imp for imp in imports if imp.resolved_path and imp.resolved_path != seed_path
    ]
    filtered_imports.sort(
        key=lambda imp: (
            -len(os.path.commonprefix([seed_dir, imp.resolved_path or ""])),
            imp.resolved_path or "",
        )
    )

    for imp in filtered_imports:
        assert imp.resolved_path is not None  # filtered above
        if imp.resolved_path in seen_import_paths:
            continue
        seen_import_paths.add(imp.resolved_path)
        imp_full = repo_root / imp.resolved_path
        if not imp_full.exists():
            continue
        with coordinator.db.session() as _session:
            from codeplane.index._internal.indexing.graph import FactQueries

            _fq = FactQueries(_session)
            imp_file = _fq.get_file_by_path(imp.resolved_path)
            if imp_file is None or imp_file.id is None:
                continue
            imp_file_defs = _fq.list_defs_in_file(imp_file.id, limit=20)
            imp_scored: list[tuple[DefFact, float]] = []
            _terms = task_terms or []
            for idef in imp_file_defs:
                if idef.def_uid == seed.def_uid:
                    continue
                ihub = min(_fq.count_callers(idef.def_uid), 30)
                iname_lower = idef.name.lower()
                term_match = 1.0 if any(t in iname_lower for t in _terms) else 0.0
                if term_match == 0 and ihub < 3 and _terms:
                    continue
                score = ihub * 2 + term_match * 5
                imp_scored.append((idef, score))
            imp_scored.sort(key=lambda x: (-x[1], x[0].def_uid))
            for idef, _iscore in imp_scored[:3]:
                import_strs.append(
                    f"{_def_signature_text(idef)} [{imp.resolved_path}:{idef.start_line}-{idef.end_line}]"
                )
        if len(import_strs) >= _MAX_IMPORT_DEFS_PER_SEED:
            break
    if import_strs:
        result["import_defs"] = import_strs

    # Callers — compact string array: "path:line"
    caller_strs: list[str] = []
    refs = await coordinator.get_references(seed, _context_id=0, limit=50)
    resolved_refs: list[tuple[str, RefFact]] = []
    for ref in refs:
        ref_path = await _file_path_for_id(app_ctx, ref.file_id)
        resolved_refs.append((ref_path, ref))
    resolved_refs.sort(key=lambda x: (x[0], x[1].start_line))

    seen_caller_files: set[str] = set()
    for ref_path, ref in resolved_refs:
        if len(caller_strs) >= _MAX_CALLERS_PER_SEED:
            break
        if (
            ref_path == seed_path
            and ref.start_line >= seed.start_line
            and ref.start_line <= seed.end_line
        ):
            continue
        if ref_path in seen_caller_files:
            continue
        seen_caller_files.add(ref_path)
        caller_strs.append(f"{ref_path}:{ref.start_line}")
    if caller_strs:
        result["callers"] = caller_strs

    # Siblings — compact string array
    _MAX_SIBLINGS = 5
    with coordinator.db.session() as _session:
        from codeplane.index._internal.indexing.graph import FactQueries

        _fq = FactQueries(_session)
        frec = _fq.get_file_by_path(seed_path)
        if frec is not None and frec.id is not None:
            sibling_defs = _fq.list_defs_in_file(frec.id, limit=30)
            siblings: list[str] = []
            for sd in sibling_defs:
                if sd.def_uid == seed.def_uid:
                    continue
                if len(siblings) >= _MAX_SIBLINGS:
                    break
                if sd.kind in ("function", "method", "class"):
                    siblings.append(f"{sd.kind} {sd.name} [{sd.start_line}-{sd.end_line}]")
            if siblings:
                result["siblings"] = siblings

    return result


# ===================================================================
# Barrel & import scaffolds
# ===================================================================


def _collect_barrel_paths(seed_paths: set[str], repo_root: Path) -> set[str]:
    """Find barrel/index files for directories containing seed files."""
    barrel_paths: set[str] = set()
    seen_dirs: set[str] = set()
    for spath in seed_paths:
        parent = str(Path(spath).parent)
        if parent in seen_dirs:
            continue
        seen_dirs.add(parent)
        for barrel_name in _BARREL_FILENAMES:
            candidate = f"{parent}/{barrel_name}" if parent != "." else barrel_name
            if candidate not in seed_paths and (repo_root / candidate).exists():
                barrel_paths.add(candidate)
                break
    return barrel_paths


async def _build_import_scaffolds(
    app_ctx: AppContext,
    seed_paths: set[str],
    repo_root: Path,
) -> list[dict[str, Any]]:
    """Build lightweight scaffolds for barrel files and imported files."""
    from codeplane.mcp.tools.files import _build_scaffold

    coordinator = app_ctx.coordinator

    barrel_paths = _collect_barrel_paths(seed_paths, repo_root)
    scaffolds: list[dict[str, Any]] = []
    for bp in sorted(barrel_paths):
        full = repo_root / bp
        if full.exists():
            scaffold = await _build_scaffold(app_ctx, bp, full)
            scaffolds.append(scaffold)

    imported_paths: set[str] = set()
    for spath in seed_paths:
        imports = await coordinator.get_file_imports(spath, limit=50)
        for imp in imports:
            if (
                imp.resolved_path
                and imp.resolved_path not in seed_paths
                and imp.resolved_path not in barrel_paths
            ):
                imported_paths.add(imp.resolved_path)

    remaining_slots = _MAX_IMPORT_SCAFFOLDS - len(scaffolds)
    for imp_path in sorted(imported_paths)[:remaining_slots]:
        full = repo_root / imp_path
        if full.exists():
            scaffold = await _build_scaffold(app_ctx, imp_path, full)
            scaffolds.append(scaffold)

    return scaffolds
