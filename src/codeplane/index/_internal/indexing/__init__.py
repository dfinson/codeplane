"""Indexing layers: lexical (Tier 0), structural (Tier 1), resolution (Tier 1.5)."""

from codeplane.index._internal.indexing.graph import FactQueries
from codeplane.index._internal.indexing.lexical import (
    LexicalIndex,
    SearchResult,
    SearchResults,
    create_index,
)
from codeplane.index._internal.indexing.resolver import (
    ReferenceResolver,
    ResolutionStats,
    resolve_references,
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
    # Type-Traced Resolution (Pass 3)
    "TypeTracedResolver",
    "TypeTracedStats",
    "resolve_type_traced",
    # Fact Queries
    "FactQueries",
]
