"""Recon MCP tool — task-aware code discovery.

Collapses the multi-call context-gathering stage (search → scaffold → read_source)
into a single call. Uses term-intersection seed selection over the structural
index + hub-score reranking + graph-walk expansion to deliver relevant source,
scaffolds, and metadata in one response.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.index.models import DefFact, RefFact
    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Seed selection
_MAX_SEEDS = 5  # Seeds after structural reranking
_DEFAULT_DEPTH = 1  # Graph expansion depth

# Budget defaults (bytes)
_DEFAULT_BUDGET_BYTES = 30_000
_MAX_BUDGET_BYTES = 60_000

# Per-tier line caps
_SEED_BODY_MAX_LINES = 80
_CALLEE_SIG_MAX_LINES = 5
_CALLER_CONTEXT_LINES = 8  # lines around each caller ref
_MAX_CALLERS_PER_SEED = 5
_MAX_CALLEES_PER_SEED = 8
_MAX_IMPORT_SCAFFOLDS = 5
_MAX_IMPORT_DEFS_PER_SEED = 10  # Max imported-file defs surfaced per seed

# Barrel / index files (language-agnostic re-export patterns)
_BARREL_FILENAMES = frozenset(
    {
        "__init__.py",
        "index.js",
        "index.ts",
        "index.tsx",
        "index.jsx",
        "index.mjs",
        "mod.rs",
    }
)

# Priority tiers for budget allocation
_P1_SEED_BODIES = 1
_P2_CALLEE_SIGS = 2
_P3_CALLER_CONTEXTS = 3
_P4_IMPORT_SCAFFOLDS = 4

# File extensions for path extraction
_PATH_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".cs",
        ".swift",
        ".kt",
        ".scala",
        ".lua",
        ".r",
        ".m",
        ".mm",
        ".sh",
        ".bash",
        ".zsh",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".cfg",
        ".ini",
        ".xml",
    }
)

# Stop words for task tokenization — terms too generic to be useful
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "must",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "over",
        "and",
        "or",
        "but",
        "not",
        "no",
        "nor",
        "so",
        "yet",
        "both",
        "either",
        "if",
        "then",
        "else",
        "when",
        "where",
        "how",
        "what",
        "which",
        "who",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "i",
        "we",
        "you",
        "they",
        "me",
        "my",
        "our",
        "your",
        "his",
        "her",
        "all",
        "each",
        "every",
        "any",
        "some",
        "such",
        "only",
        "also",
        "very",
        "just",
        "more",
        # task-description noise
        "add",
        "fix",
        "implement",
        "change",
        "update",
        "modify",
        "create",
        "make",
        "use",
        "get",
        "set",
        "new",
        "code",
        "file",
        "method",
        "function",
        "class",
        "module",
        "test",
        "check",
        "ensure",
        "want",
        "like",
        "about",
        "etc",
        "using",
        "way",
        "thing",
        "tool",
        "run",
    }
)


# ---------------------------------------------------------------------------
# Task Tokenization
# ---------------------------------------------------------------------------


def _tokenize_task(task: str) -> list[str]:
    """Extract meaningful search terms from a task description.

    Splits on non-alphanumeric boundaries, lowercases, filters stop words,
    and preserves camelCase/PascalCase segments.

    Returns deduplicated terms ordered by length descending (longer = more specific).
    """
    # Split camelCase/PascalCase into parts while keeping the original
    raw_tokens: list[str] = []

    # First, extract quoted strings as exact terms
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", task)
    for q in quoted:
        raw_tokens.append(q.lower())
        task = task.replace(f'"{q}"', " ").replace(f"'{q}'", " ")

    # Split on word boundaries (spaces, punctuation, underscores)
    words = re.split(r"[^a-zA-Z0-9_]+", task)
    for word in words:
        if not word:
            continue
        lower = word.lower()
        raw_tokens.append(lower)

        # Also split camelCase: "IndexCoordinator" → ["index", "coordinator"]
        camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)", word)
        for part in camel_parts:
            p = part.lower()
            if p != lower and len(p) >= 3:
                raw_tokens.append(p)

        # Split snake_case: "get_callees" → ["get", "callees"]
        if "_" in word:
            for part in word.split("_"):
                p = part.lower()
                if p and p != lower and len(p) >= 2:
                    raw_tokens.append(p)

    # Deduplicate, filter stop words and very short terms
    seen: set[str] = set()
    terms: list[str] = []
    for t in raw_tokens:
        if t in seen or t in _STOP_WORDS or len(t) < 2:
            continue
        seen.add(t)
        terms.append(t)

    # Sort by length descending — longer terms are more specific
    terms.sort(key=lambda x: -len(x))
    return terms


# ---------------------------------------------------------------------------
# Path Extraction
# ---------------------------------------------------------------------------

# Regex to find file-path-like strings: at least one directory separator + file extension
_PATH_REGEX = re.compile(
    r"(?:^|[\s`\"'(,;])("  # preceded by whitespace, backtick, quote, paren, etc.
    r"(?:[\w./-]+/)?[\w.-]+"  # path: optional dirs + filename
    r"\.(?:py|js|ts|jsx|tsx|java|go|rs|c|cpp|h|hpp|rb|php|cs|swift|kt|scala"
    r"|lua|r|m|mm|sh|bash|zsh|yaml|yml|json|toml|cfg|ini|xml)"
    r")"  # end capture
    r"(?:[\s`\"'),;:.]|$)",  # followed by whitespace, backtick, quote, paren, etc.
    re.IGNORECASE,
)


def _extract_paths(task: str) -> list[str]:
    """Extract explicit file paths from a task description.

    Looks for strings that resemble repo-relative file paths (e.g.,
    ``src/evee/core/base_model.py``, ``config/models.py``).

    Returns deduplicated paths in order of appearance.
    """
    matches = _PATH_REGEX.findall(task)
    seen: set[str] = set()
    paths: list[str] = []
    for m in matches:
        # Normalize: strip leading ./ if present
        p = m.lstrip("./")
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    return paths


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


def _is_test_file(path: str) -> bool:
    """Check if a file path points to a test file.

    Test files are deprioritized in seed selection because test functions
    are rarely the right starting point for understanding production code.
    They match many task terms (via imports of the symbols under test) but
    provide little implementation insight.
    """
    parts = path.split("/")
    basename = parts[-1] if parts else ""
    return (
        any(p in ("tests", "test") for p in parts[:-1])
        or basename.startswith("test_")
        or basename.endswith("_test.py")
    )


# ---------------------------------------------------------------------------
# Seed Selection
# ---------------------------------------------------------------------------


async def _select_seeds(
    app_ctx: AppContext,
    task: str,
    explicit_seeds: list[str] | None,
    max_seeds: int,
    bm25_file_scores: dict[str, float] | None = None,
) -> list[DefFact]:
    """Select seed definitions using path extraction + term-intersection scoring.

    Pipeline:

    1. If the caller provides explicit seed symbol names, use those directly.
    2. Extract explicit file paths from the task text (e.g.,
       ``src/evee/core/base_model.py``).  Resolve them to indexed File
       records, pull their top-level definitions (ranked by hub score),
       and treat them as *priority seeds* that fill the first slots.
    3. Tokenize the task into search terms.  For each term, query DefFact
       via SQL LIKE + score.
    4. Fill remaining seed slots from the scored term-intersection results,
       skipping defs already taken by path extraction.
    5. Enforce file diversity (max 2 seeds per file).

    Scoring formula (Phase 2):

        score = min(hub, 5) * 2            # structural importance (capped)
              + term_coverage * 8           # task-relevance via term matching
              + name_bonus * 3             # symbol name matches a task term
              + path_bonus                 # file path matches a task term
              + bm25_bonus * 4             # BM25 file-level task relevance
              - test_penalty               # deprioritize test files

    Test files are penalized because test functions typically have hub ≈ 0
    (nothing calls test functions) but match many task terms through their
    imports.  Without the penalty, test files often outscore the source
    files they test.
    """
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    seeds: list[DefFact] = []
    used_uids: set[str] = set()

    # ---- Explicit seeds: resolve by name ----
    if explicit_seeds:
        for name in explicit_seeds[:max_seeds]:
            d = await coordinator.get_def(name)
            if d is not None:
                seeds.append(d)
                used_uids.add(d.def_uid)
        if seeds:
            return seeds

    # ==================================================================
    # Phase 1: Path-extracted priority seeds
    # ==================================================================
    extracted_paths = _extract_paths(task)
    path_seed_file_ids: set[int] = set()

    if extracted_paths:
        log.debug("recon.extracted_paths", paths=extracted_paths)
        with coordinator.db.session() as session:
            fq = FactQueries(session)
            for epath in extracted_paths:
                if len(seeds) >= max_seeds:
                    break
                frec = fq.get_file_by_path(epath)
                if frec is None or frec.id is None:
                    continue
                path_seed_file_ids.add(frec.id)
                # Pull top-level defs from this file, ranked by hub score
                defs_in = fq.list_defs_in_file(frec.id, limit=50)
                # Score by hub (callers) so we pick the most important def
                def_scored = []
                for d in defs_in:
                    if d.def_uid in used_uids:
                        continue
                    hub = min(fq.count_callers(d.def_uid), 30)
                    def_scored.append((d, hub))
                def_scored.sort(key=lambda x: (-x[1], x[0].def_uid))
                for d, _hub in def_scored[:2]:  # max 2 per file
                    if len(seeds) >= max_seeds:
                        break
                    seeds.append(d)
                    used_uids.add(d.def_uid)

    if len(seeds) >= max_seeds:
        return seeds

    # ==================================================================
    # Phase 2: Term-intersection scoring (fills remaining slots)
    # ==================================================================

    # ---- Tokenize task ----
    terms = _tokenize_task(task)
    if not terms and not seeds:
        log.warning("recon.no_terms", task=task)
        return []

    if terms:
        log.debug("recon.terms", terms=terms, count=len(terms))

    # ---- Per-term SQLite LIKE queries ----
    def_term_hits: dict[str, set[str]] = {}
    def_lookup: dict[str, DefFact] = {}
    path_boost_file_ids: set[int] = set()

    if terms:
        with coordinator.db.session() as session:
            fq = FactQueries(session)

            for term in terms:
                matching_defs = fq.find_defs_matching_term(term, limit=200)
                for d in matching_defs:
                    uid = d.def_uid
                    if uid in used_uids:
                        continue
                    if uid not in def_term_hits:
                        def_term_hits[uid] = set()
                        def_lookup[uid] = d
                    def_term_hits[uid].add(term)

                matching_files = fq.find_files_matching_term(term, limit=50)
                for f in matching_files:
                    if f.id is not None:
                        path_boost_file_ids.add(f.id)

            # If file-path matches found but no defs matched, pull defs from those files
            if not def_term_hits and path_boost_file_ids:
                for fid in list(path_boost_file_ids)[:10]:
                    defs_in_file = fq.list_defs_in_file(fid, limit=50)
                    for d in defs_in_file:
                        uid = d.def_uid
                        if uid in used_uids:
                            continue
                        if uid not in def_term_hits:
                            def_term_hits[uid] = set()
                            def_lookup[uid] = d
                        def_term_hits[uid].add("__path__")

    if not def_term_hits and not seeds:
        log.info("recon.no_candidates", task=task, terms=terms)
        return seeds  # return any path-extracted seeds we already have

    # ---- Score: hub * 3 + coverage * 5 + BM25 * 3 + name * 3 + path - test ----
    #
    # Hub (capped at 20, max 60 pts) is the primary discriminator —
    # core implementation classes like ModelEvaluator score far above
    # noise (CLI stubs, event dataclasses).  Term coverage and BM25
    # provide task-relevance signal.  Test files get a -10 penalty.
    scored: list[tuple[DefFact, float]] = []
    if def_term_hits:
        from codeplane.index.models import File as FileModel

        with coordinator.db.session() as session:
            fq = FactQueries(session)
            # Build file_id -> path cache for BM25 lookups
            _fid_path_cache: dict[int, str] = {}
            for uid, matched_terms in def_term_hits.items():
                d = def_lookup[uid]

                real_terms = matched_terms - {"__path__"}
                term_coverage = len(matched_terms)
                caller_count = fq.count_callers(uid)
                path_bonus = 0.5 if d.file_id in path_boost_file_ids else 0.0
                name_lower = d.name.lower()
                name_bonus = 1.0 if any(t in name_lower for t in real_terms) else 0.0
                hub = min(caller_count, 20)  # cap at 20 (max 60 points)

                # Resolve file path (cached) for test detection + BM25
                if d.file_id not in _fid_path_cache:
                    frec = session.get(FileModel, d.file_id)
                    _fid_path_cache[d.file_id] = frec.path if frec else ""
                fpath = _fid_path_cache[d.file_id]

                # Test file penalty: test functions match many terms
                # (via imports) but aren't useful implementation starting
                # points.  The penalty offsets their term-coverage advantage.
                test_penalty = 10 if _is_test_file(fpath) else 0

                # BM25 file-level score: 0-1 normalized, then scaled
                bm25_bonus = 0.0
                if bm25_file_scores and fpath in bm25_file_scores:
                    max_bm25 = max(bm25_file_scores.values()) if bm25_file_scores else 1.0
                    bm25_bonus = bm25_file_scores[fpath] / max_bm25 if max_bm25 > 0 else 0.0

                score = (
                    hub * 3  # structural importance (max 60)
                    + term_coverage * 5  # task-relevance via term matching
                    + name_bonus * 3  # symbol name matches task term
                    + path_bonus  # file path matches task term
                    + bm25_bonus * 3  # BM25 file-level relevance (max 3)
                    - test_penalty  # deprioritize test files (-10)
                )
                scored.append((d, score))

        scored.sort(key=lambda x: (-x[1], x[0].def_uid))

    log.debug(
        "recon.scored_candidates",
        count=len(scored),
        top5=[(d.name, round(s, 1)) for d, s in scored[:5]],
    )

    # ---- Fill remaining slots with file diversity (max 2 seeds per file) ----
    file_counts: dict[int, int] = {}
    # Account for files already used by path-extracted seeds
    for s in seeds:
        fid = s.file_id
        file_counts[fid] = file_counts.get(fid, 0) + 1

    for d, _score in scored:
        if len(seeds) >= max_seeds:
            break
        fid = d.file_id
        if file_counts.get(fid, 0) >= 2:
            continue
        file_counts[fid] = file_counts.get(fid, 0) + 1
        seeds.append(d)
        used_uids.add(d.def_uid)

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
    task_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Expand a single seed via graph walk.

    Returns a dict with:
      - seed_body: source text of the seed definition
      - callee_sigs: signatures of symbols it calls
      - callers: context snippets around call sites
      - imports: scaffold of files the seed's file imports from

    When *task_terms* is provided, import_defs are scored by task relevance
    (term match in def name) + hub score.  Defs with no term match and hub < 3
    are dropped to reduce noise.

    Expansion relies on structural filters (package proximity for callees,
    hub + term-relevance for import_defs, file diversity for callers) rather
    than BM25 gating, which was found to drop too many true-positive
    infrastructure files (config, core types) that lack lexical overlap
    with task text.
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
    #      Filter to same-package or adjacent-package callees to reduce noise.
    callees = await coordinator.get_callees(seed, limit=_MAX_CALLEES_PER_SEED * 2)
    callee_sigs: list[dict[str, str]] = []
    seed_pkg = str(Path(seed_path).parent)
    for c in callees:
        if len(callee_sigs) >= _MAX_CALLEES_PER_SEED:
            break
        # Skip self-references
        if c.def_uid == seed.def_uid:
            continue
        c_path = await _file_path_for_id(app_ctx, c.file_id)
        # Filter distant packages: callee must share at least the top-level
        # source directory with the seed (e.g. both under src/evee/core or
        # src/evee/evaluation).  This drops noise like mcp/tools/base.py.
        c_pkg = str(Path(c_path).parent)
        common = os.path.commonprefix([seed_pkg + "/", c_pkg + "/"])
        # Require at least 2 path segments in common (e.g. "src/evee/")
        if common.count("/") < 2:
            continue
        callee_sigs.append(
            {
                "symbol": _def_signature_text(c),
                "path": c_path,
                "span": f"{c.start_line}-{c.end_line}",
            }
        )
    if callee_sigs:
        result["callees"] = callee_sigs

    # P2.5: Imports — top defs from files imported by this seed's file
    imports = await coordinator.get_file_imports(seed_path, limit=50)
    import_defs: list[dict[str, str]] = []
    seen_import_paths: set[str] = set()

    # Sort imports by path proximity to seed (same-package first)
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
        # For each imported file, include its top hub-scored def signature
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
            # Score defs by task relevance + hub score
            imp_scored: list[tuple[DefFact, float]] = []
            _terms = task_terms or []
            for idef in imp_file_defs:
                if idef.def_uid == seed.def_uid:
                    continue
                ihub = min(_fq.count_callers(idef.def_uid), 30)
                # Term match: does the def name contain a task term?
                iname_lower = idef.name.lower()
                term_match = 1.0 if any(t in iname_lower for t in _terms) else 0.0
                # Drop low-signal defs: no term match AND low hub
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
    # Resolve paths up-front and sort for deterministic order across reindexes
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


def _collect_barrel_paths(seed_paths: set[str], repo_root: Path) -> set[str]:
    """Find barrel/index files for directories containing seed files.

    Language-agnostic: checks for __init__.py, index.{js,ts,tsx}, mod.rs, etc.
    Only returns paths not already covered as seeds.
    """
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
                break  # one barrel per directory
    return barrel_paths


async def _build_import_scaffolds(
    app_ctx: AppContext,
    seed_paths: set[str],
    repo_root: Path,
) -> list[dict[str, Any]]:
    """Build lightweight scaffolds for barrel files and imported files.

    Barrel files (e.g. __init__.py, index.ts) for seed directories are always
    included first. Remaining slots are filled with imported files not already
    covered as seeds.
    """
    from codeplane.mcp.tools.files import _build_scaffold

    coordinator = app_ctx.coordinator

    # Priority 1: barrel/index files for seed directories
    barrel_paths = _collect_barrel_paths(seed_paths, repo_root)
    scaffolds: list[dict[str, Any]] = []
    for bp in sorted(barrel_paths):
        full = repo_root / bp
        if full.exists():
            scaffold = await _build_scaffold(app_ctx, bp, full)
            scaffolds.append(scaffold)

    # Priority 2: imported files (excluding seeds and already-scaffolded barrels)
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


# ---------------------------------------------------------------------------
# Budget Assembly
# ---------------------------------------------------------------------------


def _estimate_bytes(obj: Any) -> int:
    """Rough byte estimate of a JSON-serializable object."""
    import json

    return len(json.dumps(obj, default=str).encode("utf-8"))


def _trim_to_budget(result: dict[str, Any], budget: int) -> dict[str, Any]:
    """Trim response to fit within budget, removing lowest-priority content first.

    Priority (keep order): seeds > callees > import_defs > callers > scaffolds
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

    # Trim P2.5: import_defs within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "import_defs" in seed_data:
                while seed_data["import_defs"] and _estimate_bytes(result) > budget:
                    seed_data["import_defs"].pop()
                if not seed_data["import_defs"]:
                    del seed_data["import_defs"]

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
    import_def_count: int,
    scaffold_count: int,
    task_preview: str,
) -> str:
    """Generate summary for recon response."""
    parts = [f'{seed_count} seeds for "{task_preview}"']
    if callee_count:
        parts.append(f"{callee_count} callees")
    if import_def_count:
        parts.append(f"{import_def_count} import defs")
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

        # Step 1: BM25 scoring — compute BEFORE seed selection so file-level
        # relevance can inform seed ranking.  BM25 is NOT used for expansion
        # gating (removed: structural filters like package proximity, hub
        # score, and file diversity are sufficient; BM25 gating dropped too
        # many TP infrastructure files with low lexical overlap).
        raw_bm25 = coordinator.score_files_bm25(task)
        log.debug(
            "recon.bm25_scores",
            total_scored=len(raw_bm25),
            top5=sorted(raw_bm25.items(), key=lambda x: -x[1])[:5] if raw_bm25 else [],
        )

        # Step 2: Seed selection (term-intersection + BM25 + structural rerank)
        selected_seeds = await _select_seeds(
            app_ctx, task, seeds, max_seeds, bm25_file_scores=raw_bm25
        )

        if not selected_seeds:
            task_preview = task[:40] + "..." if len(task) > 40 else task
            return {
                "seeds": [],
                "summary": _summarize_recon(0, 0, 0, 0, 0, task_preview),
                "agentic_hint": (
                    "No relevant definitions found. Try: "
                    "(1) use search(mode='lexical') for text patterns, "
                    "(2) use map_repo to browse the repo structure, "
                    "(3) rephrase the task with specific symbol names."
                ),
            }

        # Step 3: Expand each seed
        seed_results: list[dict[str, Any]] = []
        seed_paths: set[str] = set()
        total_callees = 0
        total_callers = 0
        total_import_defs = 0

        # Tokenize task for import_def relevance filtering
        terms = _tokenize_task(task)

        for seed_def in selected_seeds:
            expanded = await _expand_seed(
                app_ctx,
                seed_def,
                repo_root,
                depth=depth,
                task_terms=terms,
            )
            seed_results.append(expanded)
            seed_paths.add(expanded["path"])
            total_callees += len(expanded.get("callees", []))
            total_callers += len(expanded.get("callers", []))
            total_import_defs += len(expanded.get("import_defs", []))

        # Step 4: Import scaffolds for seed files
        scaffolds: list[dict[str, Any]] = []
        if depth >= 1:
            scaffolds = await _build_import_scaffolds(app_ctx, seed_paths, repo_root)

        # Step 5: Assemble response
        task_preview = task[:40] + "..." if len(task) > 40 else task
        response: dict[str, Any] = {
            "seeds": seed_results,
            "summary": _summarize_recon(
                len(seed_results),
                total_callees,
                total_callers,
                total_import_defs,
                len(scaffolds),
                task_preview,
            ),
        }

        if scaffolds:
            response["import_scaffolds"] = scaffolds

        # Step 6: Budget trimming
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
