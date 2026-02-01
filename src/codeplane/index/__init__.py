"""Index module - Full Stacked Index (Tier 0 + Tier 1).

Architecture (see SPEC.md §7):
- Tier 0: Tantivy lexical index (always-on, candidate discovery)
- Tier 1: Tree-sitter/SQLite structural facts (defs, refs, scopes, binds, imports, exports)

This index provides syntactic facts only. No semantic resolution, no call graph,
no type information.
"""

from codeplane.index._internal.db import BulkWriter, Database, Reconciler, create_additional_indexes
from codeplane.index._internal.db.reconcile import ChangedFile, ReconcileResult
from codeplane.index.models import (
    # Fact tables
    AnchorGroup,
    # Enums
    BindReasonCode,
    BindTargetKind,
    # Data transfer models
    CandidateContext,
    Certainty,
    Context,
    ContextMarker,
    DefFact,
    DynamicAccessPattern,
    DynamicAccessSite,
    Epoch,
    ExportEntry,
    ExportSurface,
    ExportThunk,
    ExportThunkMode,
    File,
    FileState,
    Freshness,
    ImportFact,
    ImportKind,
    LanguageFamily,
    LexicalHit,
    LocalBindFact,
    MarkerTier,
    ProbeStatus,
    RefFact,
    RefTier,
    RepoState,
    Role,
    ScopeFact,
    ScopeKind,
)
from codeplane.index.ops import IndexCoordinator, IndexStats, InitResult, SearchMode, SearchResult

__all__ = [
    # Public API
    "IndexCoordinator",
    "IndexStats",
    "InitResult",
    "SearchMode",
    "SearchResult",
    # Database
    "Database",
    "BulkWriter",
    "create_additional_indexes",
    "Reconciler",
    "ReconcileResult",
    "ChangedFile",
    # Enums
    "LanguageFamily",
    "Freshness",
    "Certainty",
    "RefTier",
    "Role",
    "ScopeKind",
    "BindTargetKind",
    "BindReasonCode",
    "ImportKind",
    "ExportThunkMode",
    "DynamicAccessPattern",
    "ProbeStatus",
    "MarkerTier",
    # Fact tables
    "File",
    "Context",
    "ContextMarker",
    "DefFact",
    "RefFact",
    "ScopeFact",
    "LocalBindFact",
    "ImportFact",
    "ExportSurface",
    "ExportEntry",
    "ExportThunk",
    "AnchorGroup",
    "DynamicAccessSite",
    "RepoState",
    "Epoch",
    # Data transfer models
    "FileState",
    "CandidateContext",
    "LexicalHit",
]
