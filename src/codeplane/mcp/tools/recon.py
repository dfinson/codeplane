"""Recon MCP tool — task-aware code discovery.

Collapses the multi-call context-gathering stage (search → scaffold → read_source)
into a single call.  Uses:

1. ParsedTask — server-side structured extraction from free-text task
2. Intent classification (debug/implement/refactor/understand/test)
3. ArtifactKind classification (code/test/config/doc/build)
4. Four independent harvesters (embedding, term-match, lexical, explicit)
5. Configurable filter pipeline (replaces dual-signal gate)
6. Bounded scoring with separated relevance vs seed scores
7. Robust elbow detection for dynamic seed count
8. Graph-walk expansion with sibling context
9. Evidence-annotated structured response with timing diagnostics
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
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

_DEFAULT_DEPTH = 2  # Graph expansion depth

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

# Stop words for task tokenization — terms too generic to be useful
_STOP_WORDS = frozenset(
    {
        # English grammar
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "must",
        # Prepositions
        "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
        "into", "through", "during", "before", "after", "above", "below",
        "between", "under", "over",
        # Conjunctions
        "and", "or", "but", "not", "no", "nor", "so", "yet", "both", "either",
        # Pronouns & determiners
        "if", "then", "else", "when", "where", "how", "what", "which", "who",
        "that", "this", "these", "those", "it", "its", "i", "we", "you",
        "they", "me", "my", "our", "your", "his", "her",
        # Quantifiers
        "all", "each", "every", "any", "some", "such", "only", "also", "very",
        "just", "more",
        # Task-description noise (generic action verbs)
        "add", "fix", "implement", "change", "update", "modify", "create",
        "make", "use", "get", "set", "new", "code", "file", "method",
        "function", "class", "module", "test", "check", "ensure", "want",
        "like", "about", "etc", "using", "way", "thing", "tool", "run",
    }
)

# File extensions for path extraction
_PATH_EXTENSIONS = frozenset(
    {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
        ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".cs", ".swift",
        ".kt", ".scala", ".lua", ".r", ".m", ".mm", ".sh", ".bash",
        ".zsh", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".xml",
    }
)

# Config/doc file extensions
_CONFIG_EXTENSIONS = frozenset({
    ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".xml",
    ".env", ".properties",
})
_DOC_EXTENSIONS = frozenset({
    ".md", ".rst", ".txt", ".adoc",
})
_BUILD_FILES = frozenset({
    "Makefile", "CMakeLists.txt", "Dockerfile", "docker-compose.yml",
    "docker-compose.yaml", "Jenkinsfile", "Taskfile.yml",
})


# ===================================================================
# ArtifactKind — classify what kind of artifact a definition lives in
# ===================================================================


class ArtifactKind(StrEnum):
    """Classification of what kind of artifact a definition belongs to."""
    code = "code"
    test = "test"
    config = "config"
    doc = "doc"
    build = "build"


def _classify_artifact(path: str) -> ArtifactKind:
    """Classify a file path into an ArtifactKind."""
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix.lower()

    if _is_test_file(path):
        return ArtifactKind.test
    if name in _BUILD_FILES or name == "pyproject.toml":
        return ArtifactKind.build
    if suffix in _CONFIG_EXTENSIONS:
        return ArtifactKind.config
    if suffix in _DOC_EXTENSIONS:
        return ArtifactKind.doc
    return ArtifactKind.code


# ===================================================================
# TaskIntent — what the user is trying to accomplish
# ===================================================================


class TaskIntent(StrEnum):
    """High-level classification of what the user wants to do."""
    debug = "debug"
    implement = "implement"
    refactor = "refactor"
    understand = "understand"
    test = "test"
    unknown = "unknown"


_INTENT_KEYWORDS: dict[TaskIntent, frozenset[str]] = {
    TaskIntent.debug: frozenset({
        "bug", "fix", "error", "crash", "broken", "fail", "failing",
        "wrong", "issue", "debug", "trace", "traceback", "exception",
        "stacktrace", "investigate", "diagnose",
    }),
    TaskIntent.implement: frozenset({
        "add", "implement", "create", "build", "introduce", "support",
        "feature", "extend", "enable", "integrate", "wire",
    }),
    TaskIntent.refactor: frozenset({
        "refactor", "rename", "move", "extract", "split", "merge",
        "consolidate", "simplify", "clean", "reorganize", "restructure",
        "decouple", "inline",
    }),
    TaskIntent.understand: frozenset({
        "understand", "explain", "how", "what", "where", "why",
        "find", "locate", "show", "describe", "document", "reads",
        "overview", "architecture",
    }),
    TaskIntent.test: frozenset({
        "test", "tests", "testing", "coverage", "spec", "assertion",
        "mock", "fixture", "pytest", "unittest",
    }),
}


def _extract_intent(task: str) -> TaskIntent:
    """Extract the most likely intent from a task description.

    Counts keyword hits per intent category and returns the one
    with the most matches.  Falls back to ``unknown``.
    """
    words = set(re.split(r"[^a-zA-Z]+", task.lower()))
    best_intent = TaskIntent.unknown
    best_count = 0

    for intent, keywords in _INTENT_KEYWORDS.items():
        count = len(words & keywords)
        if count > best_count:
            best_count = count
            best_intent = intent

    return best_intent

# Regex for file paths in task text
_PATH_REGEX = re.compile(
    r"(?:^|[\s`\"'(,;])("
    r"(?:[\w./-]+/)?[\w.-]+"
    r"\.(?:py|js|ts|jsx|tsx|java|go|rs|c|cpp|h|hpp|rb|php|cs|swift|kt|scala"
    r"|lua|r|m|mm|sh|bash|zsh|yaml|yml|json|toml|cfg|ini|xml)"
    r")"
    r"(?:[\s`\"'),;:.]|$)",
    re.IGNORECASE,
)

# Regex for symbol-like identifiers (PascalCase or snake_case, 3+ chars)
_SYMBOL_REGEX = re.compile(
    r"\b([A-Z][a-zA-Z0-9]{2,}(?:[A-Z][a-z]+)*"  # PascalCase
    r"|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)"  # snake_case
    r"\b"
)


# ===================================================================
# ParsedTask — structured extraction from free-text
# ===================================================================


@dataclass(frozen=True)
class ParsedTask:
    """Structured extraction from a free-text task description.

    All fields are derived server-side — no agent cooperation required.
    The agent just sends ``task: str`` and the server extracts everything.

    Attributes:
        raw:              Original task text.
        intent:           Classified intent (debug/implement/refactor/etc.).
        primary_terms:    High-signal search terms (longest first).
        secondary_terms:  Lower-signal terms (short, generic, or from camelCase splits).
        explicit_paths:   File paths mentioned in the task text.
        explicit_symbols: Symbol-like identifiers mentioned in the task.
        keywords:         Union of primary + secondary for broad matching.
        query_text:       Synthesized embedding query (for dense retrieval).
    """

    raw: str
    intent: TaskIntent = TaskIntent.unknown
    primary_terms: list[str] = field(default_factory=list)
    secondary_terms: list[str] = field(default_factory=list)
    explicit_paths: list[str] = field(default_factory=list)
    explicit_symbols: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    query_text: str = ""


def parse_task(task: str) -> ParsedTask:
    """Parse a free-text task description into structured fields.

    Extraction pipeline:
    1. Extract quoted strings as high-priority exact terms.
    2. Extract file paths (``src/foo/bar.py``).
    3. Extract symbol-like identifiers (PascalCase, snake_case).
    4. Tokenize remaining text into primary (>=4 chars) and secondary (2-3 chars).
    5. Build a synthesized query for embedding similarity search.
    """
    if not task or not task.strip():
        return ParsedTask(raw=task, intent=TaskIntent.unknown)

    working = task

    # --- Step 1: Extract quoted strings ---
    quoted: list[str] = []
    for match in re.finditer(r"['\"]([^'\"]+)['\"]", working):
        quoted.append(match.group(1))
    for q in quoted:
        working = working.replace(f'"{q}"', " ").replace(f"'{q}'", " ")

    # --- Step 2: Extract file paths ---
    explicit_paths: list[str] = []
    path_seen: set[str] = set()
    for match in _PATH_REGEX.finditer(task):  # Use original task
        p = match.group(1).lstrip("./")
        if p and p not in path_seen:
            path_seen.add(p)
            explicit_paths.append(p)

    # --- Step 3: Extract symbol-like identifiers ---
    explicit_symbols: list[str] = []
    symbol_seen: set[str] = set()
    for match in _SYMBOL_REGEX.finditer(task):
        sym = match.group(1)
        if sym not in symbol_seen and sym.lower() not in _STOP_WORDS:
            symbol_seen.add(sym)
            explicit_symbols.append(sym)
    for q in quoted:
        if q not in symbol_seen and _SYMBOL_REGEX.match(q):
            symbol_seen.add(q)
            explicit_symbols.append(q)

    # --- Step 4: Tokenize into terms ---
    primary_terms: list[str] = []
    secondary_terms: list[str] = []
    seen_terms: set[str] = set()

    for q in quoted:
        low = q.lower()
        if low not in seen_terms and low not in _STOP_WORDS:
            seen_terms.add(low)
            primary_terms.append(low)

    words = re.split(r"[^a-zA-Z0-9_]+", working)
    for word in words:
        if not word:
            continue
        low = word.lower()
        if low not in seen_terms and low not in _STOP_WORDS and len(low) >= 2:
            seen_terms.add(low)
            if len(low) >= 4:
                primary_terms.append(low)
            else:
                secondary_terms.append(low)

        # Split camelCase
        camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)", word)
        for part in camel_parts:
            p = part.lower()
            if p not in seen_terms and p not in _STOP_WORDS and len(p) >= 3:
                seen_terms.add(p)
                if len(p) >= 4:
                    primary_terms.append(p)
                else:
                    secondary_terms.append(p)

        # Split snake_case
        if "_" in word:
            for part in word.split("_"):
                p = part.lower()
                if (
                    p
                    and p not in seen_terms
                    and p not in _STOP_WORDS
                    and len(p) >= 2
                ):
                    seen_terms.add(p)
                    if len(p) >= 4:
                        primary_terms.append(p)
                    else:
                        secondary_terms.append(p)

    primary_terms.sort(key=lambda x: -len(x))
    secondary_terms.sort(key=lambda x: -len(x))

    query_text = task.strip()
    keywords = primary_terms + secondary_terms
    intent = _extract_intent(task)

    return ParsedTask(
        raw=task,
        intent=intent,
        primary_terms=primary_terms,
        secondary_terms=secondary_terms,
        explicit_paths=explicit_paths,
        explicit_symbols=explicit_symbols,
        keywords=keywords,
        query_text=query_text,
    )


# ===================================================================
# Legacy compat — kept for existing tests, delegates to parse_task
# ===================================================================


def _tokenize_task(task: str) -> list[str]:
    """Extract meaningful search terms from a task description.

    **Legacy wrapper** — delegates to ``parse_task`` and returns the
    combined keywords list for backward compatibility with existing tests.
    """
    parsed = parse_task(task)
    return parsed.keywords


def _extract_paths(task: str) -> list[str]:
    """Extract explicit file paths from a task description.

    **Legacy wrapper** — delegates to ``parse_task`` for backward compat.
    """
    parsed = parse_task(task)
    return parsed.explicit_paths


# ===================================================================
# Multi-view query builders
# ===================================================================


def _build_query_views(parsed: ParsedTask) -> list[str]:
    """Build multiple embedding query texts (views) from a parsed task.

    Multi-view retrieval embeds several reformulations of the same task
    and merges results, improving recall over a single query.

    Views:
      1. **Natural-language** — raw task text (broad semantic match).
      2. **Code-style** — symbols + paths formatted as pseudo-code
         (matches embedding space of definitions).
      3. **Keyword-focused** — high-signal terms concatenated
         (targets exact-concept matches without noise).

    All views are batched into a single ``model.embed()`` call by
    :meth:`EmbeddingIndex.query_batch`, so there is no per-view
    latency overhead.
    """
    views: list[str] = [parsed.query_text]  # V1: NL view (always present)

    # V2: Code-style view — looks like the text format used at index time
    #     "kind qualified_name\nsignature\ndocstring"
    code_parts: list[str] = []
    for sym in parsed.explicit_symbols:
        code_parts.append(sym)
    for p in parsed.explicit_paths:
        code_parts.append(p)
    if parsed.primary_terms:
        code_parts.extend(parsed.primary_terms[:6])
    if code_parts:
        views.append(" ".join(code_parts))

    # V3: Keyword-focused view — only high-signal terms, no noise
    kw_parts = parsed.primary_terms[:10]
    if kw_parts and len(kw_parts) >= 2:
        views.append(" ".join(kw_parts))

    return views


def _merge_multi_view_results(
    per_view: list[list[tuple[str, float]]],
) -> list[tuple[str, float]]:
    """Merge results from multiple embedding views by max-similarity.

    For each def_uid that appears in any view's results, keeps the
    highest similarity score across views.  Returns the merged list
    sorted descending by score.
    """
    best: dict[str, float] = {}
    for view_results in per_view:
        for uid, sim in view_results:
            if uid not in best or sim > best[uid]:
                best[uid] = sim
    merged = sorted(best.items(), key=lambda x: (-x[1], x[0]))
    return merged


# ===================================================================
# EvidenceRecord — structured evidence from harvesters
# ===================================================================


@dataclass
class EvidenceRecord:
    """A single piece of evidence supporting a candidate's relevance."""

    category: str  # "embedding", "term_match", "lexical", "explicit"
    detail: str    # Human-readable description
    score: float = 0.0  # Normalized [0, 1] contribution


# ===================================================================
# HarvestCandidate — unified representation from all harvesters
# ===================================================================


@dataclass
class HarvestCandidate:
    """A definition candidate produced by one or more harvesters.

    Accumulates evidence from multiple sources.  The filter pipeline
    and scoring operate on these objects.

    Separated scores:
      - ``relevance_score``: How relevant to the task (for response ranking).
      - ``seed_score``: How good as a graph-expansion entry point
        (considers hub score, centrality, not just relevance).
    """

    def_uid: str
    def_fact: DefFact | None = None
    artifact_kind: ArtifactKind = ArtifactKind.code

    # Which harvesters found this candidate
    from_embedding: bool = False
    from_term_match: bool = False
    from_lexical: bool = False
    from_explicit: bool = False

    # Harvester-specific scores
    embedding_similarity: float = 0.0
    matched_terms: set[str] = field(default_factory=set)
    lexical_hit_count: int = 0

    # Structured evidence trail
    evidence: list[EvidenceRecord] = field(default_factory=list)

    # Separated scores (populated during scoring phase)
    relevance_score: float = 0.0
    seed_score: float = 0.0

    # Structural metadata (populated during enrichment)
    hub_score: int = 0
    file_path: str = ""
    is_test: bool = False
    is_barrel: bool = False
    shares_file_with_seed: bool = False
    is_callee_of_top: bool = False
    is_imported_by_top: bool = False

    @property
    def evidence_axes(self) -> int:
        """Count of independent harvester sources that found this candidate."""
        return sum([
            self.from_embedding,
            self.from_term_match,
            self.from_lexical,
            self.from_explicit,
        ])

    @property
    def has_semantic_evidence(self) -> bool:
        """Semantic axis: embedding sim >= 0.3, OR matched >= 2 terms,
        OR lexical hit, OR explicit mention."""
        return (
            (self.from_embedding and self.embedding_similarity >= 0.3)
            or len(self.matched_terms) >= 2
            or self.from_lexical
            or self.from_explicit
        )

    @property
    def has_structural_evidence(self) -> bool:
        """Structural axis: hub >= 1, OR shares file, OR callee-of,
        OR imported-by."""
        return (
            self.hub_score >= 1
            or self.shares_file_with_seed
            or self.is_callee_of_top
            or self.is_imported_by_top
        )


# ===================================================================
# Harvesters — four independent candidate sources
# ===================================================================


async def _harvest_embedding(
    app_ctx: AppContext,
    parsed: ParsedTask,
    *,
    top_k: int = 200,
) -> dict[str, HarvestCandidate]:
    """Harvester A: Multi-view dense vector similarity search.

    Builds multiple query views (natural-language, code-style,
    keyword-focused) from the parsed task and embeds them in a single
    batch call.  Results are merged by max-similarity per def_uid,
    improving recall without extra latency.
    """
    coordinator = app_ctx.coordinator

    views = _build_query_views(parsed)
    per_view = coordinator.query_similar_defs_batch(views, top_k=top_k)
    similar = _merge_multi_view_results(per_view)

    candidates: dict[str, HarvestCandidate] = {}
    for uid, sim in similar:
        if sim < 0.15:
            continue
        candidates[uid] = HarvestCandidate(
            def_uid=uid,
            from_embedding=True,
            embedding_similarity=sim,
            evidence=[EvidenceRecord(
                category="embedding",
                detail=f"semantic similarity {sim:.3f} (multi-view)",
                score=min(sim, 1.0),
            )],
        )

    log.debug(
        "recon.harvest.embedding",
        count=len(candidates),
        views=len(views),
        top5=[(uid.split("::")[-1], round(s, 3)) for uid, s in similar[:5]],
    )
    return candidates


async def _harvest_term_match(
    app_ctx: AppContext,
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester B: DefFact term matching via SQL LIKE."""
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    all_terms = parsed.primary_terms + parsed.secondary_terms
    if not all_terms:
        return candidates

    with coordinator.db.session() as session:
        fq = FactQueries(session)
        for term in all_terms:
            matching_defs = fq.find_defs_matching_term(term, limit=200)
            for d in matching_defs:
                uid = d.def_uid
                if uid not in candidates:
                    candidates[uid] = HarvestCandidate(
                        def_uid=uid,
                        def_fact=d,
                        from_term_match=True,
                    )
                else:
                    candidates[uid].from_term_match = True
                    if candidates[uid].def_fact is None:
                        candidates[uid].def_fact = d
                candidates[uid].matched_terms.add(term)
                candidates[uid].evidence.append(EvidenceRecord(
                    category="term_match",
                    detail=f"name matches term '{term}'",
                    score=0.5,
                ))

    log.debug(
        "recon.harvest.term_match",
        count=len(candidates),
        terms=len(all_terms),
    )
    return candidates


async def _harvest_lexical(
    app_ctx: AppContext,
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester C: Tantivy full-text search -> map hits to containing DefFact.

    Searches file content via Tantivy, then maps each line hit to the
    DefFact whose span contains that line.
    """
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    terms = parsed.primary_terms[:8]
    if not terms:
        return candidates

    if coordinator._lexical is None:
        return candidates

    query = " ".join(terms)
    search_results = coordinator._lexical.search(query, limit=500)

    if not search_results.results:
        return candidates

    # Group hits by file path
    file_hits: dict[str, list[int]] = {}
    for hit in search_results.results:
        if hit.file_path not in file_hits:
            file_hits[hit.file_path] = []
        file_hits[hit.file_path].append(hit.line)

    # Map line hits to containing DefFacts
    with coordinator.db.session() as session:
        fq = FactQueries(session)

        for file_path, lines in list(file_hits.items())[:50]:
            frec = fq.get_file_by_path(file_path)
            if frec is None or frec.id is None:
                continue

            defs_in_file = fq.list_defs_in_file(frec.id, limit=200)
            if not defs_in_file:
                continue

            for line in lines:
                for d in defs_in_file:
                    if d.start_line <= line <= d.end_line:
                        uid = d.def_uid
                        if uid not in candidates:
                            candidates[uid] = HarvestCandidate(
                                def_uid=uid,
                                def_fact=d,
                                from_lexical=True,
                                lexical_hit_count=1,
                                evidence=[EvidenceRecord(
                                    category="lexical",
                                    detail=f"full-text hit in {file_path}:{line}",
                                    score=0.4,
                                )],
                            )
                        else:
                            candidates[uid].from_lexical = True
                            candidates[uid].lexical_hit_count += 1
                            if candidates[uid].def_fact is None:
                                candidates[uid].def_fact = d
                        break

    log.debug(
        "recon.harvest.lexical",
        count=len(candidates),
        files_searched=len(file_hits),
    )
    return candidates


async def _harvest_explicit(
    app_ctx: AppContext,
    parsed: ParsedTask,
    explicit_seeds: list[str] | None = None,
) -> dict[str, HarvestCandidate]:
    """Harvester D: Explicit mentions (paths + symbols from task text).

    Resolves file paths to defs and symbol names to DefFacts.
    These bypass the dual-signal gate (trusted input).
    """
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    # D1: Explicit seed names provided by the agent
    if explicit_seeds:
        for name in explicit_seeds:
            d = await coordinator.get_def(name)
            if d is not None:
                candidates[d.def_uid] = HarvestCandidate(
                    def_uid=d.def_uid,
                    def_fact=d,
                    from_explicit=True,
                    evidence=[EvidenceRecord(
                        category="explicit",
                        detail=f"agent-provided seed '{name}'",
                        score=1.0,
                    )],
                )

    # D2: File paths mentioned in the task text
    if parsed.explicit_paths:
        with coordinator.db.session() as session:
            fq = FactQueries(session)
            for epath in parsed.explicit_paths:
                frec = fq.get_file_by_path(epath)
                if frec is None or frec.id is None:
                    continue
                defs_in = fq.list_defs_in_file(frec.id, limit=50)
                def_scored = []
                for d in defs_in:
                    hub = min(fq.count_callers(d.def_uid), 30)
                    def_scored.append((d, hub))
                def_scored.sort(key=lambda x: (-x[1], x[0].def_uid))
                for d, _hub in def_scored[:5]:
                    if d.def_uid not in candidates:
                        candidates[d.def_uid] = HarvestCandidate(
                            def_uid=d.def_uid,
                            def_fact=d,
                            from_explicit=True,
                            evidence=[EvidenceRecord(
                                category="explicit",
                                detail=f"in mentioned path '{epath}'",
                                score=0.9,
                            )],
                        )
                    else:
                        candidates[d.def_uid].from_explicit = True

    # D3: Symbol names mentioned in the task text
    if parsed.explicit_symbols:
        with coordinator.db.session() as session:
            fq = FactQueries(session)
            for sym in parsed.explicit_symbols:
                matching = fq.find_defs_matching_term(sym, limit=10)
                for d in matching:
                    if sym.lower() in d.name.lower() or (
                        d.qualified_name
                        and sym.lower() in d.qualified_name.lower()
                    ):
                        if d.def_uid not in candidates:
                            candidates[d.def_uid] = HarvestCandidate(
                                def_uid=d.def_uid,
                                def_fact=d,
                                from_explicit=True,
                                evidence=[EvidenceRecord(
                                    category="explicit",
                                    detail=f"name matches symbol '{sym}'",
                                    score=0.8,
                                )],
                            )
                        else:
                            candidates[d.def_uid].from_explicit = True

    log.debug(
        "recon.harvest.explicit",
        count=len(candidates),
        paths=len(parsed.explicit_paths),
        symbols=len(parsed.explicit_symbols),
    )
    return candidates


# ===================================================================
# Merge & Enrich — combine harvester outputs + resolve DefFacts
# ===================================================================


def _merge_candidates(
    *harvests: dict[str, HarvestCandidate],
) -> dict[str, HarvestCandidate]:
    """Merge candidates from multiple harvesters, accumulating evidence."""
    merged: dict[str, HarvestCandidate] = {}

    for harvest in harvests:
        for uid, cand in harvest.items():
            if uid not in merged:
                merged[uid] = cand
            else:
                existing = merged[uid]
                existing.from_embedding = (
                    existing.from_embedding or cand.from_embedding
                )
                existing.from_term_match = (
                    existing.from_term_match or cand.from_term_match
                )
                existing.from_lexical = (
                    existing.from_lexical or cand.from_lexical
                )
                existing.from_explicit = (
                    existing.from_explicit or cand.from_explicit
                )
                existing.embedding_similarity = max(
                    existing.embedding_similarity, cand.embedding_similarity
                )
                existing.matched_terms |= cand.matched_terms
                existing.lexical_hit_count += cand.lexical_hit_count
                existing.evidence.extend(cand.evidence)
                if existing.def_fact is None and cand.def_fact is not None:
                    existing.def_fact = cand.def_fact

    return merged


async def _enrich_candidates(
    app_ctx: AppContext,
    candidates: dict[str, HarvestCandidate],
) -> None:
    """Resolve missing DefFact objects and populate structural metadata.

    Mutates candidates in-place.
    """
    from codeplane.index._internal.indexing.graph import FactQueries
    from codeplane.index.models import File as FileModel

    coordinator = app_ctx.coordinator

    # Resolve missing DefFacts
    missing_uids = [uid for uid, c in candidates.items() if c.def_fact is None]
    if missing_uids:
        with coordinator.db.session() as session:
            fq = FactQueries(session)
            for uid in missing_uids:
                d = fq.get_def(uid)
                if d is not None:
                    candidates[uid].def_fact = d

    # Remove candidates that still lack a DefFact
    dead = [uid for uid, c in candidates.items() if c.def_fact is None]
    for uid in dead:
        del candidates[uid]

    # Populate structural metadata
    fid_path_cache: dict[int, str] = {}
    with coordinator.db.session() as session:
        fq = FactQueries(session)
        for uid, cand in list(candidates.items()):
            if cand.def_fact is None:
                continue
            d = cand.def_fact

            cand.hub_score = fq.count_callers(uid)

            if d.file_id not in fid_path_cache:
                frec = session.get(FileModel, d.file_id)
                fid_path_cache[d.file_id] = frec.path if frec else ""
            cand.file_path = fid_path_cache[d.file_id]

            cand.is_test = _is_test_file(cand.file_path)
            cand.is_barrel = _is_barrel_file(cand.file_path)
            cand.artifact_kind = _classify_artifact(cand.file_path)


# ===================================================================
# Filter Pipeline — configurable, intent-aware (replaces dual-gate)
# ===================================================================


def _apply_dual_gate(
    candidates: dict[str, HarvestCandidate],
) -> dict[str, HarvestCandidate]:
    """Legacy dual-gate wrapper — delegates to ``_apply_filters``."""
    return _apply_filters(candidates, TaskIntent.unknown)


def _apply_filters(
    candidates: dict[str, HarvestCandidate],
    intent: TaskIntent,
) -> dict[str, HarvestCandidate]:
    """Apply intent-aware filter pipeline.

    Filter stages:
    1. Explicit bypass — always pass (trusted input).
    2. Evidence minimum — require at least one strong signal.
    3. Intent-aware artifact filtering:
       - ``debug`` / ``implement`` / ``refactor`` → prefer code, include tests
       - ``test`` → prefer tests, include code
       - ``understand`` → include everything
       - ``unknown`` → require dual evidence (semantic AND structural)
    4. Barrel exclusion — barrel files are low-signal re-exports.

    Compared to the old dual-gate, this:
    - Is intent-aware (debugging includes tests, understanding includes docs)
    - Has per-artifact-kind thresholds instead of one-size-fits-all
    - Keeps evidence requirements but relaxes them for strong signals
    """
    filtered: dict[str, HarvestCandidate] = {}
    stats = {"bypassed": 0, "passed": 0, "filtered": 0}

    for uid, cand in candidates.items():
        # Stage 1: Explicit bypass
        if cand.from_explicit:
            filtered[uid] = cand
            stats["bypassed"] += 1
            continue

        # Stage 2: Minimum evidence gate
        if not cand.has_semantic_evidence:
            stats["filtered"] += 1
            continue

        # Stage 3: Intent-aware artifact filtering
        if intent in (TaskIntent.debug, TaskIntent.implement, TaskIntent.refactor):
            # For action intents: require structural evidence for non-test code
            # but relax for tests (they're often relevant for debugging)
            if cand.artifact_kind == ArtifactKind.test:
                if cand.embedding_similarity < 0.35:
                    stats["filtered"] += 1
                    continue
            elif cand.artifact_kind == ArtifactKind.code:
                if not cand.has_structural_evidence and cand.embedding_similarity < 0.4:
                    stats["filtered"] += 1
                    continue
            elif cand.artifact_kind in (ArtifactKind.config, ArtifactKind.doc) and cand.embedding_similarity < 0.45:
                # Config/doc only if strong embedding match
                stats["filtered"] += 1
                continue
        elif intent == TaskIntent.test:
            # For test intent: keep tests easily, require more from code
            if cand.artifact_kind == ArtifactKind.code and not cand.has_structural_evidence and cand.embedding_similarity < 0.35:
                stats["filtered"] += 1
                continue
        elif intent == TaskIntent.understand:
            # Understanding intent: keep anything with reasonable evidence
            if cand.evidence_axes < 1 and cand.embedding_similarity < 0.3:
                stats["filtered"] += 1
                continue
        else:
            # Unknown intent: fall back to dual-gate (require both)
            if not (cand.has_semantic_evidence and cand.has_structural_evidence):
                stats["filtered"] += 1
                continue

        # Stage 4: Barrel exclusion
        if cand.is_barrel and not cand.from_explicit:
            stats["filtered"] += 1
            continue

        filtered[uid] = cand
        stats["passed"] += 1

    log.debug("recon.filter_pipeline", intent=intent.value, **stats)
    return filtered


# ===================================================================
# Elbow Detection — dynamic seed count from score distribution
# ===================================================================


def find_elbow(
    scores: list[float], *, min_seeds: int = 3, max_seeds: int = 15
) -> int:
    """Find the natural cutoff in a sorted-descending score list.

    Uses the "maximum distance to chord" method.
    """
    n = len(scores)
    if n <= min_seeds:
        return n

    analysis = scores[:max_seeds]
    n_analysis = len(analysis)
    if n_analysis <= min_seeds:
        return n_analysis

    x1, y1 = 0, analysis[0]
    x2, y2 = n_analysis - 1, analysis[-1]

    if y1 - y2 < 0.5:
        return min(n_analysis, max_seeds)

    max_dist = 0.0
    elbow_idx = min_seeds

    dx = x2 - x1
    dy = y2 - y1
    chord_len = (dx * dx + dy * dy) ** 0.5

    if chord_len < 1e-10:
        return min_seeds

    for i in range(min_seeds, n_analysis):
        dist = abs(dy * i - dx * analysis[i] + x2 * y1 - y2 * x1) / chord_len
        if dist > max_dist:
            max_dist = dist
            elbow_idx = i

    result = elbow_idx + 1
    return max(min_seeds, min(result, max_seeds))


# ===================================================================
# Scoring — bounded features with separated relevance/seed scores
# ===================================================================


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))


# Artifact-kind weights: how much to boost/penalize each kind
_ARTIFACT_WEIGHTS: dict[ArtifactKind, dict[TaskIntent, float]] = {
    ArtifactKind.code: {
        TaskIntent.debug: 1.0, TaskIntent.implement: 1.0,
        TaskIntent.refactor: 1.0, TaskIntent.understand: 1.0,
        TaskIntent.test: 0.7, TaskIntent.unknown: 1.0,
    },
    ArtifactKind.test: {
        TaskIntent.debug: 0.6, TaskIntent.implement: 0.3,
        TaskIntent.refactor: 0.3, TaskIntent.understand: 0.5,
        TaskIntent.test: 1.0, TaskIntent.unknown: 0.3,
    },
    ArtifactKind.config: {
        TaskIntent.debug: 0.3, TaskIntent.implement: 0.4,
        TaskIntent.refactor: 0.2, TaskIntent.understand: 0.6,
        TaskIntent.test: 0.1, TaskIntent.unknown: 0.3,
    },
    ArtifactKind.doc: {
        TaskIntent.debug: 0.2, TaskIntent.implement: 0.3,
        TaskIntent.refactor: 0.1, TaskIntent.understand: 0.8,
        TaskIntent.test: 0.1, TaskIntent.unknown: 0.2,
    },
    ArtifactKind.build: {
        TaskIntent.debug: 0.1, TaskIntent.implement: 0.2,
        TaskIntent.refactor: 0.1, TaskIntent.understand: 0.3,
        TaskIntent.test: 0.1, TaskIntent.unknown: 0.1,
    },
}


def _score_candidates(
    candidates: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> list[tuple[str, float]]:
    """Score candidates with bounded features and separated scores.

    Features (all normalized to [0, 1]):
      f_emb:    Embedding similarity (already [0, 1]).
      f_hub:    Hub score, log-scaled and capped.
      f_terms:  Term match count, bounded.
      f_axes:   Evidence axis diversity, bounded.
      f_name:   Name contains primary term (binary).
      f_path:   Path contains primary term (binary).
      f_lexical: Lexical hit presence (binary, avoids double-counting with term).
      f_explicit: Explicit mention (binary).
      f_artifact: Intent-aware artifact weight [0, 1].

    Relevance score = weighted sum of all features (how relevant to task).
    Seed score = relevance * seed_multiplier (how good as entry point).
      - seed_multiplier boosts hub score and penalizes leaf nodes.

    Returns [(def_uid, seed_score)] sorted descending by seed_score.
    """
    import math

    scored: list[tuple[str, float]] = []

    for uid, cand in candidates.items():
        if cand.def_fact is None:
            continue

        # --- Bounded features ---
        f_emb = _clamp(cand.embedding_similarity)
        f_hub = _clamp(math.log1p(min(cand.hub_score, 30)) / math.log1p(30))
        f_terms = _clamp(len(cand.matched_terms) / 5.0)
        f_axes = _clamp((cand.evidence_axes - 1) / 3.0)
        f_lexical = _clamp(min(cand.lexical_hit_count, 5) / 5.0)
        f_explicit = 1.0 if cand.from_explicit else 0.0

        name_lower = cand.def_fact.name.lower()
        f_name = (
            1.0 if any(t in name_lower for t in parsed.primary_terms) else 0.0
        )
        f_path = (
            1.0
            if any(t in cand.file_path.lower() for t in parsed.primary_terms)
            else 0.0
        )

        # Artifact-kind weight based on intent
        kind_weights = _ARTIFACT_WEIGHTS.get(cand.artifact_kind, {})
        f_artifact = kind_weights.get(parsed.intent, 0.5)

        # --- Relevance score (how relevant to the task) ---
        relevance = (
            f_emb * 0.30
            + f_hub * 0.10
            + f_terms * 0.15
            + f_axes * 0.10
            + f_name * 0.12
            + f_path * 0.05
            + f_lexical * 0.08
            + f_explicit * 0.10
        ) * f_artifact

        # --- Seed score (how good as graph expansion entry) ---
        # Hub score matters more for seed selection (central = better root)
        seed_multiplier = 0.5 + f_hub * 0.3 + f_explicit * 0.2
        seed_sc = relevance * seed_multiplier

        cand.relevance_score = relevance
        cand.seed_score = seed_sc

        scored.append((uid, seed_sc))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored


# ===================================================================
# Seed Selection Pipeline
# ===================================================================


async def _select_seeds(
    app_ctx: AppContext,
    task: str,
    explicit_seeds: list[str] | None = None,
    *,
    min_seeds: int = 3,
    max_seeds: int = 15,
) -> tuple[list[DefFact], ParsedTask, list[tuple[str, float]], dict[str, Any]]:
    """Select seed definitions using the full harvest -> filter -> score pipeline.

    Pipeline:
    1. Parse task -> ParsedTask (with intent classification)
    2. Run 4 harvesters in parallel
    3. Merge candidates (accumulate evidence)
    4. Enrich with structural metadata + artifact kind
    5. Apply intent-aware filter pipeline
    6. Score with bounded features + artifact-kind weights
    7. Find elbow for dynamic seed count
    8. Enforce file diversity

    Returns:
        (seeds, parsed_task, scored_candidates, diagnostics)
    """
    diagnostics: dict[str, Any] = {}
    t0 = time.monotonic()

    # 1. Parse task
    parsed = parse_task(task)
    diagnostics["intent"] = parsed.intent.value
    log.debug(
        "recon.parsed_task",
        intent=parsed.intent.value,
        primary=parsed.primary_terms[:5],
        secondary=parsed.secondary_terms[:3],
        paths=parsed.explicit_paths,
        symbols=parsed.explicit_symbols[:5],
    )

    # 2. Run harvesters in parallel (independent, no shared state)
    t_harvest = time.monotonic()
    emb_candidates, term_candidates, lex_candidates, exp_candidates = (
        await asyncio.gather(
            _harvest_embedding(app_ctx, parsed),
            _harvest_term_match(app_ctx, parsed),
            _harvest_lexical(app_ctx, parsed),
            _harvest_explicit(app_ctx, parsed, explicit_seeds),
        )
    )
    diagnostics["harvest_ms"] = round((time.monotonic() - t_harvest) * 1000)

    # 3. Merge
    merged = _merge_candidates(
        emb_candidates, term_candidates, lex_candidates, exp_candidates
    )

    diagnostics["harvested"] = {
        "embedding": len(emb_candidates),
        "term_match": len(term_candidates),
        "lexical": len(lex_candidates),
        "explicit": len(exp_candidates),
        "merged": len(merged),
    }

    log.debug(
        "recon.merged",
        total=len(merged),
        embedding=len(emb_candidates),
        term_match=len(term_candidates),
        lexical=len(lex_candidates),
        explicit=len(exp_candidates),
    )

    if not merged:
        diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
        return [], parsed, [], diagnostics

    # 4. Enrich with structural metadata + artifact kind
    await _enrich_candidates(app_ctx, merged)

    # 5. Intent-aware filter pipeline
    gated = _apply_filters(merged, parsed.intent)

    if not gated:
        log.info("recon.filter_empty", pre_filter=len(merged))
        # Fall back to ungated top embedding candidates
        gated = {
            uid: cand
            for uid, cand in merged.items()
            if cand.from_embedding and cand.embedding_similarity >= 0.3
        }
        if not gated:
            diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
            return [], parsed, [], diagnostics

    diagnostics["post_filter"] = len(gated)

    # 6. Score
    scored = _score_candidates(gated, parsed)

    if not scored:
        diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
        return [], parsed, [], diagnostics

    # 7. Elbow detection
    score_values = [s for _, s in scored]
    n_seeds = find_elbow(
        score_values, min_seeds=min_seeds, max_seeds=max_seeds
    )

    # 8. File diversity: max 2 seeds per file
    seeds: list[DefFact] = []
    file_counts: dict[int, int] = {}

    for uid, _score in scored:
        if len(seeds) >= n_seeds:
            break
        cand = gated[uid]
        if cand.def_fact is None:
            continue
        fid = cand.def_fact.file_id
        if file_counts.get(fid, 0) >= 2:
            continue
        file_counts[fid] = file_counts.get(fid, 0) + 1
        seeds.append(cand.def_fact)

    diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
    diagnostics["seeds_selected"] = len(seeds)
    diagnostics["elbow_k"] = n_seeds

    log.info(
        "recon.seeds_selected",
        count=len(seeds),
        elbow=n_seeds,
        scored_total=len(scored),
        names=[s.name for s in seeds],
        intent=parsed.intent.value,
        total_ms=diagnostics["total_ms"],
    )

    return seeds, parsed, scored, diagnostics


# ===================================================================
# Helpers
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


def _is_test_file(path: str) -> bool:
    """Check if a file path points to a test file."""
    parts = path.split("/")
    basename = parts[-1] if parts else ""
    return (
        any(p in ("tests", "test") for p in parts[:-1])
        or basename.startswith("test_")
        or basename.endswith("_test.py")
    )


def _is_barrel_file(path: str) -> bool:
    """Check if a file is a barrel/index re-export file."""
    name = PurePosixPath(path).name
    return name in _BARREL_FILENAMES


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


# ===================================================================
# Budget Assembly
# ===================================================================


def _estimate_bytes(obj: Any) -> int:
    """Rough byte estimate of a JSON-serializable object."""
    import json

    return len(json.dumps(obj, default=str).encode("utf-8"))


def _trim_to_budget(
    result: dict[str, Any], budget: int
) -> dict[str, Any]:
    """Trim response to fit within budget, removing lowest-priority content.

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
                while (
                    seed_data["callers"]
                    and _estimate_bytes(result) > budget
                ):
                    seed_data["callers"].pop()
                if not seed_data["callers"]:
                    del seed_data["callers"]

    if _estimate_bytes(result) <= budget:
        return result

    # Trim P2.5: import_defs within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "import_defs" in seed_data:
                while (
                    seed_data["import_defs"]
                    and _estimate_bytes(result) > budget
                ):
                    seed_data["import_defs"].pop()
                if not seed_data["import_defs"]:
                    del seed_data["import_defs"]

    if _estimate_bytes(result) <= budget:
        return result

    # Trim P2: callees within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "callees" in seed_data:
                while (
                    seed_data["callees"]
                    and _estimate_bytes(result) > budget
                ):
                    seed_data["callees"].pop()
                if not seed_data["callees"]:
                    del seed_data["callees"]

    return result


# ===================================================================
# Tool Summaries
# ===================================================================


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


# ===================================================================
# Tool Registration
# ===================================================================


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
                "Be specific: include symbol names, file paths, "
                "or domain terms when known.  The server extracts "
                "structured signals automatically."
            ),
        ),
        seeds: list[str] | None = Field(
            None,
            description=(
                "Optional explicit seed symbol names "
                "(e.g., ['IndexCoordinator', 'FactQueries']). "
                "Treated as high-priority explicit mentions."
            ),
        ),
        depth: int = Field(
            default=_DEFAULT_DEPTH,
            ge=0,
            le=3,
            description=(
                "Graph expansion depth. 0 = seeds only (no callees/callers/imports). "
                ">=1 = expand seeds with callees, callers, imports, and siblings. "
                "Default 2."
            ),
        ),
        budget: int = Field(
            default=_DEFAULT_BUDGET_BYTES,
            le=_MAX_BUDGET_BYTES,
            description="Response size budget in bytes.",
        ),
        max_seeds: int = Field(
            default=15,
            ge=1,
            le=20,
            description=(
                "Upper bound on seed count. Actual count is determined "
                "dynamically by score distribution (elbow detection)."
            ),
        ),
        verbosity: str = Field(
            default="normal",
            description=(
                "Response verbosity: 'minimal' (seeds only, no evidence), "
                "'normal' (seeds + scoring summary), 'detailed' (+ evidence + diagnostics)."
            ),
        ),
    ) -> dict[str, Any]:
        """Task-aware code discovery in a single call.

        Pipeline: parse_task (with intent classification) ->
        4 harvesters (embedding, term-match, lexical, explicit) ->
        intent-aware filter pipeline -> bounded scoring ->
        elbow detection -> graph expansion (with siblings) ->
        evidence-annotated structured response.

        Returns seeds with evidence, context files, scoring summary,
        diagnostics, and file_sha256 for write_source compatibility.
        """
        recon_id = uuid.uuid4().hex[:12]
        t_total = time.monotonic()

        coordinator = app_ctx.coordinator
        repo_root = coordinator.repo_root

        # Pipeline: parse, harvest, filter, score, select
        selected_seeds, parsed_task, scored_all, diagnostics = (
            await _select_seeds(
                app_ctx, task, seeds, min_seeds=3, max_seeds=max_seeds
            )
        )

        if not selected_seeds:
            task_preview = (
                task[:40] + "..." if len(task) > 40 else task
            )
            result: dict[str, Any] = {
                "recon_id": recon_id,
                "seeds": [],
                "summary": _summarize_recon(
                    0, 0, 0, 0, 0, task_preview
                ),
                "agentic_hint": (
                    "No relevant definitions found. Try: "
                    "(1) use search(mode='lexical') for text patterns, "
                    "(2) use map_repo to browse the repo structure, "
                    "(3) rephrase the task with specific symbol names."
                ),
            }
            if verbosity == "detailed":
                result["diagnostics"] = diagnostics
            return result

        # Expand each seed
        t_expand = time.monotonic()
        seed_results: list[dict[str, Any]] = []
        seed_paths: set[str] = set()
        total_callees = 0
        total_callers = 0
        total_import_defs = 0
        total_siblings = 0

        terms = parsed_task.keywords

        # Build per-seed evidence explanations from scored candidates
        scored_map: dict[str, float] = dict(scored_all)

        for seed_def in selected_seeds:
            expanded = await _expand_seed(
                app_ctx,
                seed_def,
                repo_root,
                depth=depth,
                task_terms=terms,
            )

            # Add artifact_kind to each seed result
            expanded["artifact_kind"] = _classify_artifact(expanded["path"]).value

            # Add evidence explanation if not minimal
            if verbosity != "minimal":
                uid = seed_def.def_uid
                score = scored_map.get(uid, 0.0)
                expanded["seed_score"] = round(score, 4)

            seed_results.append(expanded)
            seed_paths.add(expanded["path"])
            total_callees += len(expanded.get("callees", []))
            total_callers += len(expanded.get("callers", []))
            total_import_defs += len(expanded.get("import_defs", []))
            total_siblings += len(expanded.get("siblings", []))

        expand_ms = round((time.monotonic() - t_expand) * 1000)

        # Import scaffolds
        scaffolds: list[dict[str, Any]] = []
        if depth >= 1:
            scaffolds = await _build_import_scaffolds(
                app_ctx, seed_paths, repo_root
            )

        # Assemble response
        task_preview = task[:40] + "..." if len(task) > 40 else task
        response: dict[str, Any] = {
            "recon_id": recon_id,
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

        # Scoring summary (normal + detailed)
        if verbosity != "minimal":
            response["scoring_summary"] = {
                "pipeline": "harvest->filter(intent)->score(bounded)->elbow",
                "intent": parsed_task.intent.value,
                "candidates_harvested": len(scored_all),
                "seeds_selected": len(selected_seeds),
                "parsed_terms": parsed_task.primary_terms[:8],
                "explicit_paths": parsed_task.explicit_paths,
                "explicit_symbols": parsed_task.explicit_symbols[:5],
            }

        # Diagnostics (detailed only)
        if verbosity == "detailed":
            diagnostics["expand_ms"] = expand_ms
            diagnostics["total_ms"] = round(
                (time.monotonic() - t_total) * 1000
            )
            response["diagnostics"] = diagnostics

        # Budget trimming
        response = _trim_to_budget(response, budget)

        # Deterministic agentic hint based on intent
        seed_paths_list = sorted(seed_paths)
        paths_str = ", ".join(seed_paths_list[:5])
        if len(seed_paths_list) > 5:
            paths_str += f" (+{len(seed_paths_list) - 5} more)"

        intent = parsed_task.intent
        if intent == TaskIntent.debug:
            action_hint = (
                "Focus on the seed with highest score. "
                "Check callers for how the buggy code is invoked. "
                "Use read_source on caller paths for full context."
            )
        elif intent == TaskIntent.implement:
            action_hint = (
                "Use write_source with file_sha256 from seed source to edit. "
                "Check siblings for patterns to follow. "
                "Use checkpoint after edits."
            )
        elif intent == TaskIntent.refactor:
            action_hint = (
                "Check callers to understand impact of changes. "
                "Use refactor_rename for symbol renames across files. "
                "Use checkpoint after edits."
            )
        elif intent == TaskIntent.understand:
            action_hint = (
                "Read seed sources for implementation details. "
                "Check callees for dependencies and callers for usage. "
                "Use read_source for additional spans."
            )
        else:
            action_hint = (
                "Use write_source with file_sha256 from seed source to edit. "
                "Use read_source for additional spans. "
                "Use checkpoint after edits."
            )

        response["agentic_hint"] = (
            f"Recon found {len(seed_results)} seed(s) "
            f"(intent: {intent.value}) across: {paths_str}. "
            f"{action_hint}"
        )

        # Coverage hint
        if parsed_task.explicit_paths:
            missing_paths = [
                p
                for p in parsed_task.explicit_paths
                if p not in seed_paths
            ]
            if missing_paths:
                response["coverage_hint"] = (
                    "Mentioned paths not in seeds: "
                    f"{', '.join(missing_paths)}. "
                    "Use read_source to examine them directly."
                )

        # Follow-up pointers (structured suggestions)
        follow_ups: list[dict[str, str]] = []
        if parsed_task.explicit_paths:
            for p in parsed_task.explicit_paths:
                if p not in seed_paths:
                    follow_ups.append({
                        "action": "read_source",
                        "target": p,
                        "reason": "mentioned in task but not in seeds",
                    })
        if follow_ups:
            response["follow_up"] = follow_ups

        from codeplane.mcp.delivery import wrap_existing_response

        return wrap_existing_response(
            response,
            resource_kind="recon_result",
        )
