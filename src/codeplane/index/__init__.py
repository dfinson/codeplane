"""Index module - hybrid two-layer indexing engine.

This module provides:
- Syntactic layer: Tree-sitter parsing, Tantivy full-text search
- Semantic layer: SCIP batch indexer integration
- Context discovery: Automatic project boundary detection
- File state tracking: Freshness × Certainty for mutation gate

Key classes:
- Database: Connection manager with WAL mode
- BulkWriter: High-performance bulk inserts
- IndexCoordinator: High-level orchestration

See DESIGN.md for architecture details.
"""

from codeplane.index.db import BulkWriter, Database
from codeplane.index.indexes import create_additional_indexes
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
from codeplane.index.reconcile import ChangedFile, Reconciler, ReconcileResult

__all__ = [
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
