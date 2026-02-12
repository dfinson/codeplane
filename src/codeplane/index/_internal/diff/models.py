"""Data models for semantic diff.

All models are plain dataclasses / frozen dataclasses — no DB coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DefSnapshot:
    """Point-in-time snapshot of a single definition.

    This is the comparison unit.  Identity key for matching across states
    is ``(kind, lexical_path)``.
    """

    kind: str
    name: str
    lexical_path: str
    signature_hash: str | None = None
    display_name: str | None = None
    start_line: int = 0
    start_col: int = 0
    end_line: int = 0
    end_col: int = 0


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """A file that appears in the git diff."""

    path: str
    status: str  # "added", "modified", "deleted", "renamed"
    has_grammar: bool
    language: str | None = None  # language family detected for this file


@dataclass(frozen=True, slots=True)
class FileChangeInfo:
    """Structured metadata for a non-structurally-analyzed file."""

    path: str
    status: str  # "added", "modified", "deleted", "renamed"
    category: str  # "prod", "test", "build", "config", "docs", "unknown"
    language: str | None = None


@dataclass
class RefTierBreakdown:
    """Reference counts broken down by resolution tier."""

    proven: int = 0  # Same-file lexical bind, certain
    strong: int = 0  # Cross-file with explicit import trace
    anchored: int = 0  # Ambiguous but grouped in anchor group
    unknown: int = 0  # Cannot classify

    @property
    def total(self) -> int:
        return self.proven + self.strong + self.anchored + self.unknown


@dataclass
class ImpactInfo:
    """Blast-radius metadata for one structural change."""

    reference_count: int | None = None
    ref_tiers: RefTierBreakdown | None = None
    reference_basis: str = "unknown"  # "ref_facts_resolved" | "ref_facts_partial" | "unknown"
    referencing_files: list[str] | None = None
    importing_files: list[str] | None = None
    import_count: int | None = None  # distinct from reference_count (import mentions vs usage refs)
    affected_test_files: list[str] | None = None
    confidence: str = "high"  # high | medium | low
    visibility: str | None = None  # public | private | protected | internal
    is_static: bool | None = None


@dataclass
class StructuralChange:
    """Enriched structural change — the final output unit."""

    path: str
    kind: str  # raw tree-sitter kind
    name: str
    qualified_name: str | None
    change: str  # added | removed | signature_changed | body_changed | renamed
    structural_severity: str  # breaking | non_breaking
    behavior_change_risk: str  # low | medium | high | unknown
    old_sig: str | None
    new_sig: str | None
    impact: ImpactInfo | None
    risk_basis: str | None = None  # machine-readable reason for behavior_change_risk
    entity_id: str | None = None  # stable ID (def_uid or hash)
    start_line: int = 0
    start_col: int = 0
    end_line: int = 0
    end_col: int = 0
    lines_changed: int | None = None  # count of changed lines in entity span
    nested_changes: list[StructuralChange] | None = None
    delta_tags: list[str] = field(default_factory=list)  # e.g. ["control_flow_changed"]
    change_preview: str | None = None  # first N changed lines


@dataclass
class RawStructuralChange:
    """Pre-enrichment structural change from the engine."""

    path: str
    kind: str
    name: str
    qualified_name: str | None
    change: str
    structural_severity: str  # breaking | non_breaking
    old_sig: str | None
    new_sig: str | None
    is_internal: bool  # True if this is a local variable inside a function
    start_line: int = 0
    start_col: int = 0
    end_line: int = 0
    end_col: int = 0
    old_name: str | None = None  # For renames
    lines_changed: int | None = None
    delta_tags: list[str] | None = None  # e.g. ["parameters_changed", "minor_change"]


@dataclass
class RawDiffResult:
    """Result from the engine layer, before enrichment."""

    changes: list[RawStructuralChange]
    non_structural_files: list[FileChangeInfo]
    files_analyzed: int


@dataclass
class AnalysisScope:
    """Metadata about what was analyzed and how.

    Provides provenance, capability disclosure, and reproducibility
    information so consumers can reason about completeness.
    """

    base_sha: str | None = None  # resolved SHA of base ref (None for epoch mode)
    target_sha: str | None = None  # resolved SHA of target ref (None for worktree)
    worktree_dirty: bool | None = None  # True if target is worktree and has uncommitted changes
    mode: str = "git"  # "git" | "epoch"
    entity_id_scheme: str = "def_uid"  # how entity_id is derived
    files_parsed: int = 0  # files successfully parsed with tree-sitter
    files_no_grammar: int = 0  # files with no supported grammar
    files_parse_failed: int = 0  # files where parsing was attempted but failed
    languages_analyzed: list[str] = field(default_factory=list)  # languages seen


@dataclass
class SemanticDiffResult:
    """Final enriched semantic diff result."""

    structural_changes: list[StructuralChange]
    non_structural_changes: list[FileChangeInfo]
    summary: str
    breaking_summary: str | None
    files_analyzed: int
    base_description: str
    target_description: str
    scope: AnalysisScope | None = None
