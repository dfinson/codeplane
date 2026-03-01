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
# Constants (all internal — not exposed to agents)
# ---------------------------------------------------------------------------

_INTERNAL_DEPTH = 2  # Graph expansion depth (backend-decided, not agent-facing)

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
        # Prepositions
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
        # Conjunctions
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
        # Pronouns & determiners
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
        # Quantifiers
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
        # Task-description noise (generic action verbs)
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

# Config/doc file extensions
_CONFIG_EXTENSIONS = frozenset(
    {
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".cfg",
        ".ini",
        ".xml",
        ".env",
        ".properties",
    }
)
_DOC_EXTENSIONS = frozenset(
    {
        ".md",
        ".rst",
        ".txt",
        ".adoc",
    }
)
_BUILD_FILES = frozenset(
    {
        "Makefile",
        "CMakeLists.txt",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "Jenkinsfile",
        "Taskfile.yml",
    }
)


# ===================================================================
# OutputTier — file-level output fidelity tier
# ===================================================================


class OutputTier(StrEnum):
    """Output fidelity tier for a file, determined by single-elbow detection.

    SCAFFOLD:  Above elbow — imports + signatures.
    LITE:      Below elbow — path + description only.

    v2 design: no FULL_FILE tier.  Full content is always fetched via
    recon_resolve as a separate step.

    Legacy aliases (FULL_FILE, MIN_SCAFFOLD, SUMMARY_ONLY) are kept for
    internal pipeline compatibility — they map to the v2 values and are
    NOT exposed in API output.
    """

    SCAFFOLD = "scaffold"
    LITE = "lite"

    # Internal aliases — pipeline scoring still uses 3-tier logic internally,
    # but both FULL_FILE and MIN_SCAFFOLD serialize to "scaffold" on output.
    FULL_FILE = "scaffold"
    MIN_SCAFFOLD = "scaffold"
    SUMMARY_ONLY = "lite"


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
    TaskIntent.debug: frozenset(
        {
            "bug",
            "fix",
            "error",
            "crash",
            "broken",
            "fail",
            "failing",
            "wrong",
            "issue",
            "debug",
            "trace",
            "traceback",
            "exception",
            "stacktrace",
            "investigate",
            "diagnose",
        }
    ),
    TaskIntent.implement: frozenset(
        {
            "add",
            "implement",
            "create",
            "build",
            "introduce",
            "support",
            "feature",
            "extend",
            "enable",
            "integrate",
            "wire",
        }
    ),
    TaskIntent.refactor: frozenset(
        {
            "refactor",
            "rename",
            "move",
            "extract",
            "split",
            "merge",
            "consolidate",
            "simplify",
            "clean",
            "reorganize",
            "restructure",
            "decouple",
            "inline",
        }
    ),
    TaskIntent.understand: frozenset(
        {
            "understand",
            "explain",
            "how",
            "what",
            "where",
            "why",
            "find",
            "locate",
            "show",
            "describe",
            "document",
            "reads",
            "overview",
            "architecture",
        }
    ),
    TaskIntent.test: frozenset(
        {
            "test",
            "tests",
            "testing",
            "coverage",
            "spec",
            "assertion",
            "mock",
            "fixture",
            "pytest",
            "unittest",
        }
    ),
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
    detail: str  # Human-readable description
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
    from_term_match: bool = False
    from_lexical: bool = False
    from_explicit: bool = False
    from_graph: bool = False

    # Harvester-specific scores
    matched_terms: set[str] = field(default_factory=set)
    lexical_hit_count: int = 0
    term_idf_score: float = 0.0  # IDF-weighted term relevance
    graph_quality: float = 0.0  # Graded edge quality (0-1)

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
        return sum(
            [
                self.from_term_match,
                self.from_lexical,
                self.from_explicit,
                self.from_graph,
            ]
        )

    @property
    def has_semantic_evidence(self) -> bool:
        """Semantic axis: embedding sim >= 0.15, OR matched >= 2 terms,
        OR single term with hub support, OR lexical hit, OR explicit mention,
        OR graph-discovered.

        The embedding threshold is deliberately low (0.15) — it only
        gates whether embedding produced any signal at all.  The
        elbow-based adaptive floor in ``_apply_filters`` handles the
        real precision cut downstream.
        """
        return (
            len(self.matched_terms) >= 2
            or (len(self.matched_terms) == 1 and self.hub_score >= 3)
            or self.from_lexical
            or self.from_explicit
            or self.from_graph
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

    def matches_negative(self, negative_mentions: list[str]) -> bool:
        """Return True if this candidate's name/path matches a negated term."""
        if not negative_mentions:
            return False
        name_lower = self.def_fact.name.lower() if self.def_fact else ""
        path_lower = self.file_path.lower()
        return any(neg in name_lower or neg in path_lower for neg in negative_mentions)

    @property
    def has_strong_single_axis(self) -> bool:
        """True if any one axis is strong enough to pass alone (OR gate).

        Used to let high-confidence candidates through even when they
        lack evidence on other axes.
        """
        return self.from_explicit or self.hub_score >= 8 or len(self.matched_terms) >= 3


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
        negative_mentions: Terms the user explicitly excludes ("not X", "except Y").
        is_stacktrace_driven: True if task contains error/traceback patterns.
        is_test_driven:   True if the primary goal is writing/fixing tests.
    """

    raw: str
    intent: TaskIntent = TaskIntent.unknown
    primary_terms: list[str] = field(default_factory=list)
    secondary_terms: list[str] = field(default_factory=list)
    explicit_paths: list[str] = field(default_factory=list)
    explicit_symbols: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    query_text: str = ""
    negative_mentions: list[str] = field(default_factory=list)
    is_stacktrace_driven: bool = False
    is_test_driven: bool = False


# ===================================================================
# FileCandidate — file-level candidate from embedding + secondary signals
# ===================================================================


@dataclass
class FileCandidate:
    """A file-level candidate produced by file-embedding search + secondary signals.

    Unlike def-level ``HarvestCandidate``, this represents an entire file.
    The ``tier`` is assigned by two-elbow detection after scoring.
    """

    path: str
    similarity: float = 0.0  # Cosine similarity from file-level embedding
    tier: OutputTier = OutputTier.SUMMARY_ONLY

    # Unique identifier assigned during recon pipeline assembly.
    # Used by recon_resolve to validate that the agent is requesting
    # files that originate from a recon result (not arbitrary paths).
    candidate_id: str = ""

    # Secondary signal reinforcements (from existing harvesters)
    term_match_count: int = 0  # Number of query terms found in file defs
    lexical_hit_count: int = 0  # Tantivy full-text hits in this file
    has_explicit_mention: bool = False  # Agent mentioned this file
    graph_connected: bool = False  # Connected via graph walk
    graph_quality: float = 0.0  # Graded edge quality for RRF ranking
    artifact_kind: ArtifactKind = ArtifactKind.code

    # Composite score (similarity + secondary signals)
    combined_score: float = 0.0

    # Expand reason for agent hint
    expand_reason: str = ""

    @property
    def evidence_summary(self) -> str:
        """Compact one-line evidence string."""
        parts: list[str] = []
        if self.similarity > 0:
            parts.append(f"sim({self.similarity:.2f})")
        if self.term_match_count > 0:
            parts.append(f"terms({self.term_match_count})")
        if self.lexical_hit_count > 0:
            parts.append(f"lex({self.lexical_hit_count})")
        if self.has_explicit_mention:
            parts.append("explicit")
        if self.graph_connected:
            parts.append("graph")
        return " ".join(parts)


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
