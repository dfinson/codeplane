"""Graph expansion — expand seed definitions via structural graph walk.

Single Responsibility: Take a seed DefFact and produce a rich context dict
with source, callees, callers, imports, and siblings.

Depends on: models (constants, classifiers), AppContext (I/O).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeplane.mcp.tools.recon.models import (
    _BARREL_FILENAMES,
    _CALLER_CONTEXT_LINES,
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
        sig = (
            d.signature_text
            if d.signature_text.startswith("(")
            else f"({d.signature_text})"
        )
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
) -> dict[str, Any]:
    """Expand a single seed via graph walk.

    Returns a dict with:
      - seed_body: source text of the seed definition
      - callee_sigs: signatures of symbols it calls
      - callers: context snippets around call sites
      - imports: scaffold of files the seed's file imports from

    When *task_terms* is provided, import_defs are scored by task relevance
    (term match in def name) + hub score.
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
        body_end = min(
            seed.end_line, seed.start_line + _SEED_BODY_MAX_LINES - 1
        )
        result["source"] = _read_lines(full_path, seed.start_line, body_end)
        result["file_sha256"] = _compute_sha256(full_path)
        if seed.end_line > body_end:
            result["truncated"] = True
            result["total_lines"] = seed.end_line - seed.start_line + 1

    if depth < 1:
        return result

    # P2: Callees — signatures of symbols this seed references
    callees = await coordinator.get_callees(
        seed, limit=_MAX_CALLEES_PER_SEED * 2
    )
    callee_sigs: list[dict[str, str]] = []
    _terms = task_terms or []
    for c in callees:
        if len(callee_sigs) >= _MAX_CALLEES_PER_SEED:
            break
        if c.def_uid == seed.def_uid:
            continue
        c_path = await _file_path_for_id(app_ctx, c.file_id)
        # Relaxed filter: include all callees from the same repo
        # (old filter required 2+ shared path segments, dropping cross-package)
        # Prioritize task-relevant callees
        c_name_lower = c.name.lower()
        is_relevant = any(t in c_name_lower for t in _terms) if _terms else False
        callee_sigs.append(
            {
                "symbol": _def_signature_text(c),
                "path": c_path,
                "span": f"{c.start_line}-{c.end_line}",
                **({"task_relevant": True} if is_relevant else {}),
            }
        )
    # Sort: task-relevant callees first
    callee_sigs.sort(key=lambda x: (0 if x.get("task_relevant") else 1))
    if callee_sigs:
        result["callees"] = callee_sigs

    # P2.5: Imports — top defs from files imported by this seed's file
    imports = await coordinator.get_file_imports(seed_path, limit=50)
    import_defs: list[dict[str, str]] = []
    seen_import_paths: set[str] = set()

    seed_dir = str(Path(seed_path).parent) + "/"
    filtered_imports = [
        imp
        for imp in imports
        if imp.resolved_path and imp.resolved_path != seed_path
    ]
    filtered_imports.sort(
        key=lambda imp: (
            -len(
                os.path.commonprefix([seed_dir, imp.resolved_path or ""])
            ),
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
                term_match = (
                    1.0
                    if any(t in iname_lower for t in _terms)
                    else 0.0
                )
                if term_match == 0 and ihub < 3 and _terms:
                    continue
                score = ihub * 2 + term_match * 5
                imp_scored.append((idef, score))
            imp_scored.sort(key=lambda x: (-x[1], x[0].def_uid))
            for idef, _iscore in imp_scored[:3]:
                import_defs.append(
                    {
                        "symbol": _def_signature_text(idef),
                        "path": imp.resolved_path,
                        "span": f"{idef.start_line}-{idef.end_line}",
                    }
                )
        if len(import_defs) >= _MAX_IMPORT_DEFS_PER_SEED:
            break
    if import_defs:
        result["import_defs"] = import_defs

    # P3: Callers — context snippets around references to this seed
    refs = await coordinator.get_references(seed, _context_id=0, limit=50)
    resolved_refs: list[tuple[str, RefFact]] = []
    for ref in refs:
        ref_path = await _file_path_for_id(app_ctx, ref.file_id)
        resolved_refs.append((ref_path, ref))
    resolved_refs.sort(key=lambda x: (x[0], x[1].start_line))

    caller_snippets: list[dict[str, Any]] = []
    seen_caller_files: set[str] = set()
    for ref_path, ref in resolved_refs:
        if len(caller_snippets) >= _MAX_CALLERS_PER_SEED:
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

    # P4: Siblings — other key defs in the same file (for context)
    _MAX_SIBLINGS = 5
    with coordinator.db.session() as _session:
        from codeplane.index._internal.indexing.graph import FactQueries

        _fq = FactQueries(_session)
        frec = _fq.get_file_by_path(seed_path)
        if frec is not None and frec.id is not None:
            sibling_defs = _fq.list_defs_in_file(frec.id, limit=30)
            siblings: list[dict[str, str]] = []
            for sd in sibling_defs:
                if sd.def_uid == seed.def_uid:
                    continue
                if len(siblings) >= _MAX_SIBLINGS:
                    break
                # Only include substantial siblings (classes, functions, not variables)
                if sd.kind in ("function", "method", "class"):
                    siblings.append({
                        "symbol": _def_signature_text(sd),
                        "kind": sd.kind,
                        "span": f"{sd.start_line}-{sd.end_line}",
                    })
            if siblings:
                result["siblings"] = siblings

    return result


# ===================================================================
# Barrel & import scaffolds
# ===================================================================


def _collect_barrel_paths(
    seed_paths: set[str], repo_root: Path
) -> set[str]:
    """Find barrel/index files for directories containing seed files."""
    barrel_paths: set[str] = set()
    seen_dirs: set[str] = set()
    for spath in seed_paths:
        parent = str(Path(spath).parent)
        if parent in seen_dirs:
            continue
        seen_dirs.add(parent)
        for barrel_name in _BARREL_FILENAMES:
            candidate = (
                f"{parent}/{barrel_name}" if parent != "." else barrel_name
            )
            if candidate not in seed_paths and (
                repo_root / candidate
            ).exists():
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
