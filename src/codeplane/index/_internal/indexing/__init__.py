"""Indexing layers: lexical (Tier 0), structural (Tier 1), resolution (Tier 1.5)."""

from codeplane.index._internal.indexing.graph import FactQueries
from codeplane.index._internal.indexing.lexical import (
    LexicalIndex,
    SearchResult,
    SearchResults,
    create_index,
)
from codeplane.index._internal.indexing.resolver import (
    CrossFileResolutionStats,
    ReferenceResolver,
    ResolutionStats,
    resolve_namespace_refs,
    resolve_references,
    resolve_same_namespace_refs,
    resolve_star_import_refs,
)
from codeplane.index._internal.indexing.structural import (
    BatchResult,
    ExtractionResult,
    StructuralIndexer,
    index_context,
)
from codeplane.index._internal.indexing.type_resolver import (
    TypeTracedResolver,
    TypeTracedStats,
    resolve_type_traced,
)

__all__ = [
    # Lexical (Tier 0)
    "LexicalIndex",
    "SearchResult",
    "SearchResults",
    "create_index",
    # Structural (Tier 1)
    "StructuralIndexer",
    "ExtractionResult",
    "BatchResult",
    "index_context",
    # Reference Resolution (Tier 1.5)
    "ReferenceResolver",
    "ResolutionStats",
    "resolve_references",
    # Cross-file resolution (Pass 1.5 - DB-backed)
    "CrossFileResolutionStats",
    "resolve_namespace_refs",
    "resolve_same_namespace_refs",
    "resolve_star_import_refs",
    # Type-Traced Resolution (Pass 3)
    "TypeTracedResolver",
    "TypeTracedStats",
    "resolve_type_traced",
    # Fact Queries
    "FactQueries",
]
