"""Recon domain models — enums, dataclasses, constants, classifiers.

Single Responsibility: All type definitions and classification logic live
here.  No I/O, no database access, no async.  Pure functions + data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codeplane.index.models import DefFact

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


# ===================================================================
# File-type classifiers
# ===================================================================


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
