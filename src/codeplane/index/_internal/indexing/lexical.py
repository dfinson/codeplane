"""Lexical index for full-text search via Tantivy.

This module provides full-text search capabilities using Tantivy,
a fast Rust-based search engine. It supports:
- File content indexing
- Symbol name search
- Code snippet retrieval
- Fuzzy matching
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tantivy

if TYPE_CHECKING:
    pass


@dataclass
class SearchResult:
    """A single search result."""

    file_path: str
    line: int
    column: int
    snippet: str
    score: float
    context_id: int | None = None


@dataclass
class SearchResults:
    """Collection of search results."""

    results: list[SearchResult] = field(default_factory=list)
    total_hits: int = 0
    query_time_ms: int = 0


class LexicalIndex:
    """
    Full-text search index using Tantivy.

    Provides fuzzy search over:
    - File contents (for grep-like search)
    - Symbol names (for quick navigation)
    - Documentation strings

    Supports staged writes for epoch atomicity:
    - stage_file() / stage_remove() buffer changes in memory
    - commit_staged() commits all staged changes atomically
    - discard_staged() discards uncommitted changes

    Usage::

        index = LexicalIndex(index_path)

        # Staged writes (for epoch atomicity)
        index.stage_file("src/foo.py", content, context_id=1)
        index.stage_file("src/bar.py", content, context_id=1)
        index.commit_staged()  # Single atomic commit

        # Or direct writes (backward compatible)
        index.add_file("src/foo.py", content, context_id=1)

        # Search
        results = index.search("class MyClass", limit=10)
    """

    def __init__(self, index_path: Path | str):
        """
        Initialize the lexical index.

        Args:
            index_path: Directory to store Tantivy index files
        """
        self.index_path = Path(index_path)
        self._index: Any = None
        self._writer: Any = None
        self._schema: Any = None
        self._initialized = False
        # Staging buffer for atomic epoch commits
        self._staged_adds: list[dict[str, Any]] = []
        self._staged_removes: list[str] = []

    def _ensure_initialized(self) -> None:
        """Lazily initialize the Tantivy index."""
        if self._initialized:
            return

        # Build schema
        schema_builder = tantivy.SchemaBuilder()
        # Use default tokenizer for path to allow partial matching (e.g., "utils" matches "src/utils.py")
        schema_builder.add_text_field("path", stored=True, tokenizer_name="default")
        # Use raw tokenizer for exact path matching (used for deletion)
        schema_builder.add_text_field("path_exact", stored=False, tokenizer_name="raw")
        schema_builder.add_text_field("content", stored=True, tokenizer_name="default")
        schema_builder.add_text_field("symbols", stored=True, tokenizer_name="default")
        schema_builder.add_integer_field("context_id", stored=True, indexed=True)
        schema_builder.add_integer_field("file_id", stored=True, indexed=True)
        self._schema = schema_builder.build()

        # Create or open index
        self.index_path.mkdir(parents=True, exist_ok=True)
        self._index = tantivy.Index(self._schema, path=str(self.index_path))
        self._initialized = True

    def add_file(
        self,
        file_path: str,
        content: str,
        context_id: int,
        file_id: int = 0,
        symbols: list[str] | None = None,
    ) -> None:
        """
        Add or update a file in the index.

        Args:
            file_path: Relative file path
            content: File content as string
            context_id: Context this file belongs to
            file_id: Database file ID
            symbols: List of symbol names in this file
        """
        self._ensure_initialized()

        writer = self._index.writer()
        try:
            # Delete existing document for this path (use path_exact for exact matching)
            writer.delete_documents("path_exact", file_path)

            # Add new document
            doc = tantivy.Document()
            doc.add_text("path", file_path)
            doc.add_text("path_exact", file_path)  # For exact match deletion
            doc.add_text("content", content)
            doc.add_text("symbols", " ".join(symbols) if symbols else "")
            doc.add_integer("context_id", context_id)
            doc.add_integer("file_id", file_id)
            writer.add_document(doc)
            writer.commit()
        finally:
            pass  # Writer is cleaned up automatically

    def add_files_batch(
        self,
        files: list[dict[str, Any]],
    ) -> int:
        """
        Add multiple files in a batch.

        Args:
            files: List of dicts with keys: path, content, context_id, file_id, symbols

        Returns:
            Number of files indexed.
        """
        self._ensure_initialized()

        writer = self._index.writer()
        count = 0
        try:
            for f in files:
                # Delete existing (use path_exact for exact matching)
                writer.delete_documents("path_exact", f["path"])

                # Add new
                doc = tantivy.Document()
                doc.add_text("path", f["path"])
                doc.add_text("path_exact", f["path"])  # For exact match deletion
                doc.add_text("content", f.get("content", ""))
                doc.add_text("symbols", " ".join(f.get("symbols", [])))
                doc.add_integer("context_id", f.get("context_id", 0))
                doc.add_integer("file_id", f.get("file_id", 0))
                writer.add_document(doc)
                count += 1
            writer.commit()
        finally:
            pass

        return count

    def remove_file(self, file_path: str) -> bool:
        """Remove a file from the index (immediate commit)."""
        self._ensure_initialized()

        writer = self._index.writer()
        try:
            # Use path_exact field for exact matching
            deleted = writer.delete_documents("path_exact", file_path)
            writer.commit()
            return bool(deleted > 0)
        finally:
            pass

    # =========================================================================
    # Staged Operations (for epoch atomicity)
    # =========================================================================

    def stage_file(
        self,
        file_path: str,
        content: str,
        context_id: int,
        file_id: int = 0,
        symbols: list[str] | None = None,
    ) -> None:
        """
        Stage a file for later atomic commit.

        Changes are buffered in memory until commit_staged() is called.
        This enables atomic epoch publishing where SQLite and Tantivy
        commits happen together.

        Args:
            file_path: Relative file path
            content: File content as string
            context_id: Context this file belongs to
            file_id: Database file ID
            symbols: List of symbol names in this file
        """
        self._staged_adds.append(
            {
                "path": file_path,
                "content": content,
                "context_id": context_id,
                "file_id": file_id,
                "symbols": symbols or [],
            }
        )

    def stage_remove(self, file_path: str) -> None:
        """
        Stage a file removal for later atomic commit.

        Args:
            file_path: Relative file path to remove
        """
        self._staged_removes.append(file_path)

    def has_staged_changes(self) -> bool:
        """Return True if there are uncommitted staged changes."""
        return bool(self._staged_adds or self._staged_removes)

    def staged_count(self) -> tuple[int, int]:
        """Return (additions, removals) count of staged changes."""
        return len(self._staged_adds), len(self._staged_removes)

    def commit_staged(self) -> int:
        """
        Commit all staged changes atomically.

        This is the Tantivy-side of epoch publishing. Call this
        immediately before committing the SQLite epoch record.

        Returns:
            Number of documents affected (adds + removes)
        """
        if not self.has_staged_changes():
            return 0

        self._ensure_initialized()

        writer = self._index.writer()
        count = 0
        try:
            # Process removals first
            for file_path in self._staged_removes:
                writer.delete_documents("path_exact", file_path)
                count += 1

            # Process additions (which also delete existing)
            for f in self._staged_adds:
                # Delete existing document
                writer.delete_documents("path_exact", f["path"])

                # Add new document
                doc = tantivy.Document()
                doc.add_text("path", f["path"])
                doc.add_text("path_exact", f["path"])
                doc.add_text("content", f.get("content", ""))
                doc.add_text("symbols", " ".join(f.get("symbols", [])))
                doc.add_integer("context_id", f.get("context_id", 0))
                doc.add_integer("file_id", f.get("file_id", 0))
                writer.add_document(doc)
                count += 1

            # Single atomic commit
            writer.commit()
        except Exception:
            # On failure, changes are discarded (Tantivy writer rollback)
            self._staged_adds.clear()
            self._staged_removes.clear()
            raise

        # Clear staging buffers on success
        self._staged_adds.clear()
        self._staged_removes.clear()

        return count

    def discard_staged(self) -> int:
        """
        Discard all staged changes without committing.

        Returns:
            Number of staged changes discarded
        """
        count = len(self._staged_adds) + len(self._staged_removes)
        self._staged_adds.clear()
        self._staged_removes.clear()
        return count

    def search(
        self,
        query: str,
        limit: int = 20,
        context_id: int | None = None,
    ) -> SearchResults:
        """
        Search the index.

        Args:
            query: Search query (supports Tantivy query syntax)
            limit: Maximum results to return
            context_id: Optional context to filter by

        Returns:
            SearchResults with matching documents.
        """
        self._ensure_initialized()
        start = time.monotonic()

        results = SearchResults()

        # Build query
        full_query = f"({query}) AND context_id:{context_id}" if context_id is not None else query

        searcher = self._index.searcher()
        try:
            # Parse and execute query using Index.parse_query
            parsed = self._index.parse_query(full_query, ["content", "symbols", "path"])

            # Search
            top_docs = searcher.search(parsed, limit).hits

            results.total_hits = len(top_docs)

            for score, doc_addr in top_docs:
                doc = searcher.doc(doc_addr)

                # Extract snippet around match and get line number
                content = doc.get_first("content") or ""
                snippet, line_num = self._extract_snippet(content, query, max_lines=3)

                result = SearchResult(
                    file_path=doc.get_first("path") or "",
                    line=line_num,
                    column=0,
                    snippet=snippet,
                    score=score,
                    context_id=doc.get_first("context_id"),
                )
                results.results.append(result)

        finally:
            pass

        results.query_time_ms = int((time.monotonic() - start) * 1000)
        return results

    def search_symbols(
        self,
        query: str,
        limit: int = 20,
        context_id: int | None = None,
    ) -> SearchResults:
        """Search only in symbol names."""
        self._ensure_initialized()

        symbol_query = f"symbols:{query}"
        return self.search(symbol_query, limit, context_id)

    def search_path(
        self,
        pattern: str,
        limit: int = 20,
        context_id: int | None = None,
    ) -> SearchResults:
        """Search in file paths."""
        self._ensure_initialized()

        path_query = f"path:{pattern}"
        return self.search(path_query, limit, context_id)

    def _extract_snippet(
        self,
        content: str,
        query: str,
        max_lines: int = 3,
    ) -> tuple[str, int]:
        """Extract a snippet around the query match.

        Returns:
            Tuple of (snippet_text, line_number) where line_number is 1-indexed.
            If no match found, returns (first lines, 1).
        """
        lines = content.split("\n")
        query_lower = query.lower()

        # Handle query syntax - extract actual search terms
        # Remove field prefixes like "symbols:" or "path:"
        search_terms = []
        for term in query_lower.split():
            if ":" in term:
                # Field-prefixed term - take the value part
                _, value = term.split(":", 1)
                if value:
                    search_terms.append(value)
            else:
                # Skip boolean operators
                if term not in ("and", "or", "not"):
                    search_terms.append(term)

        # Find first line matching any search term
        for i, line in enumerate(lines):
            line_lower = line.lower()
            for term in search_terms:
                if term in line_lower:
                    # Return context around match (1-indexed line number)
                    start = max(0, i - 1)
                    end = min(len(lines), i + max_lines)
                    return "\n".join(lines[start:end]), i + 1

        # No match found, return first lines
        return "\n".join(lines[:max_lines]), 1

    def clear(self) -> None:
        """Clear all documents from the index."""
        self._ensure_initialized()

        writer = self._index.writer()
        try:
            writer.delete_all_documents()
            writer.commit()
        finally:
            pass

    def reload(self) -> None:
        """Reload the index to see latest changes."""
        if self._index:
            self._index.reload()

    def doc_count(self) -> int:
        """Return number of documents in the index."""
        self._ensure_initialized()

        searcher = self._index.searcher()
        return int(searcher.num_docs)


def create_index(index_path: Path | str) -> LexicalIndex:
    """Create a new lexical index."""
    return LexicalIndex(index_path)
