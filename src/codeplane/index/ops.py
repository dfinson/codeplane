"""Public API for the index module.

This module provides the primary entry points for indexing operations.
All public classes and functions should be imported from here.

Usage::

    from codeplane.index.ops import IndexCoordinator, IndexStats, SearchResult

    coordinator = IndexCoordinator(repo_root, db_path, tantivy_path)
    result = await coordinator.initialize()

    # Search
    results = await coordinator.search("query")

    # Reindex
    stats = await coordinator.reindex_incremental([Path("changed.py")])
"""

from __future__ import annotations

from codeplane.index._internal.coordinator import (
    IndexCoordinator,
    IndexStats,
    InitResult,
    SearchMode,
    SearchResult,
)

__all__ = [
    "IndexCoordinator",
    "IndexStats",
    "InitResult",
    "SearchMode",
    "SearchResult",
]
