"""Indexing layers: lexical (T0), structural (T1), semantic (T2)."""

from codeplane.index._internal.indexing.graph import (
    CallPath,
    SymbolGraph,
    SymbolNode,
)
from codeplane.index._internal.indexing.lexical import (
    LexicalIndex,
    SearchResult,
    SearchResults,
    create_index,
)
from codeplane.index._internal.indexing.scip import (
    IndexerResult,
    PopulateResult,
    SCIPDocument,
    SCIPIndex,
    SCIPOccurrence,
    SCIPParser,
    SCIPPopulator,
    SCIPRelation,
    SCIPRunner,
    SCIPSymbol,
    is_scip_available,
)
from codeplane.index._internal.indexing.structural import (
    BatchResult,
    ExtractionResult,
    StructuralIndexer,
    index_context,
)

__all__ = [
    # Lexical
    "LexicalIndex",
    "SearchResult",
    "SearchResults",
    "create_index",
    # Structural
    "StructuralIndexer",
    "ExtractionResult",
    "BatchResult",
    "index_context",
    # Graph
    "SymbolGraph",
    "SymbolNode",
    "CallPath",
    # SCIP
    "SCIPParser",
    "SCIPRunner",
    "SCIPPopulator",
    "SCIPIndex",
    "SCIPDocument",
    "SCIPSymbol",
    "SCIPOccurrence",
    "SCIPRelation",
    "IndexerResult",
    "PopulateResult",
    "is_scip_available",
]
