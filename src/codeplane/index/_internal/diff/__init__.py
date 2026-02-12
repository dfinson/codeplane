"""Semantic diff package — structural change summary from index facts.

Public API re-exports for the diff subpackage.
"""

from codeplane.index._internal.diff.engine import compute_structural_diff
from codeplane.index._internal.diff.enrichment import enrich_diff
from codeplane.index._internal.diff.models import (
    ChangedFile,
    DefSnapshot,
    ImpactInfo,
    RawDiffResult,
    RawStructuralChange,
    SemanticDiffResult,
    StructuralChange,
)
from codeplane.index._internal.diff.sources import (
    snapshots_from_blob,
    snapshots_from_epoch,
    snapshots_from_index,
)

__all__ = [
    "ChangedFile",
    "DefSnapshot",
    "ImpactInfo",
    "RawDiffResult",
    "RawStructuralChange",
    "SemanticDiffResult",
    "StructuralChange",
    "compute_structural_diff",
    "enrich_diff",
    "snapshots_from_blob",
    "snapshots_from_epoch",
    "snapshots_from_index",
]
