"""Index module - hybrid two-layer indexing engine.

This module provides:
- Syntactic layer: Tree-sitter parsing, Tantivy full-text search
- Semantic layer: SCIP batch indexer integration
- Context discovery: Automatic project boundary detection
- File state tracking: Freshness × Certainty for mutation gate

Public API is in `codeplane.index.ops`:
- IndexCoordinator: High-level orchestration
- IndexStats, InitResult, SearchResult: Result types

Internal implementations are in `codeplane.index._internal/`.

See DESIGN.md for architecture details.
"""

from codeplane.index._internal.db import BulkWriter, Database, Reconciler, create_additional_indexes
from codeplane.index._internal.db.reconcile import ChangedFile, ReconcileResult
from codeplane.index.models import (
    CandidateContext,
    Certainty,
    Context,
    ContextMarker,
    DecisionCache,
    Edge,
    Export,
    File,
    FileSemanticFacts,
    FileState,
    Freshness,
    JobStatus,
    LanguageFamily,
    Layer,
    MarkerTier,
    Occurrence,
    ProbeStatus,
    RefreshJob,
    RefreshScope,
    RepoState,
    Role,
    Symbol,
)
from codeplane.index.ops import IndexCoordinator, IndexStats, InitResult, SearchMode, SearchResult

__all__ = [
    # Public API (ops.py)
    "IndexCoordinator",
    "IndexStats",
    "InitResult",
    "SearchMode",
    "SearchResult",
    # Database
    "Database",
    "BulkWriter",
    "create_additional_indexes",
    # Enums
    "LanguageFamily",
    "Freshness",
    "Certainty",
    "Layer",
    "Role",
    "JobStatus",
    "ProbeStatus",
    "MarkerTier",
    # Table models
    "File",
    "Context",
    "ContextMarker",
    "Symbol",
    "Occurrence",
    "Export",
    "Edge",
    "FileSemanticFacts",
    "RefreshJob",
    "RepoState",
    "DecisionCache",
    # Data transfer models
    "FileState",
    "RefreshScope",
    "CandidateContext",
    # Reconciler
    "Reconciler",
    "ReconcileResult",
    "ChangedFile",
]
