"""Data models for semantic diff.

All models are plain dataclasses / frozen dataclasses — no DB coupling.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    end_line: int = 0


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """A file that appears in the git diff."""

    path: str
    status: str  # "added", "modified", "deleted", "renamed"
    has_grammar: bool


@dataclass
class ImpactInfo:
    """Blast-radius metadata for one structural change."""

    reference_count: int | None = None
    referencing_files: list[str] | None = None
    importing_files: list[str] | None = None
    affected_test_files: list[str] | None = None
    confidence: str = "high"  # high | medium | low


@dataclass
class StructuralChange:
    """Enriched structural change — the final output unit."""

    path: str
    kind: str
    name: str
    qualified_name: str | None
    change: str  # added | removed | signature_changed | body_changed | renamed
    severity: str  # breaking | non_breaking
    old_sig: str | None
    new_sig: str | None
    impact: ImpactInfo | None
    nested_changes: list[StructuralChange] | None = None


@dataclass
class RawStructuralChange:
    """Pre-enrichment structural change from the engine."""

    path: str
    kind: str
    name: str
    qualified_name: str | None
    change: str
    severity: str
    old_sig: str | None
    new_sig: str | None
    is_internal: bool  # True if this is a local variable inside a function
    start_line: int = 0
    end_line: int = 0
    old_name: str | None = None  # For renames


@dataclass
class RawDiffResult:
    """Result from the engine layer, before enrichment."""

    changes: list[RawStructuralChange]
    non_structural_files: list[str]
    files_analyzed: int


@dataclass
class SemanticDiffResult:
    """Final enriched semantic diff result."""

    structural_changes: list[StructuralChange]
    non_structural_changes: list[str]
    summary: str
    breaking_summary: str | None
    files_analyzed: int
    base_description: str
    target_description: str
