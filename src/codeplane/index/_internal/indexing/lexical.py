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
    fallback_reason: str | None = None  # Set if query syntax error triggered literal fallback


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
        except (OSError, ValueError):
            # OSError: filesystem errors during commit
            # ValueError: tantivy index corruption or schema mismatch
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

    def _escape_query(self, query: str) -> str:
        r"""Escape special Tantivy query syntax characters for literal search.

        Escapes: + - && || ! ( ) { } [ ] ^ " ~ * ? : \ /
        """
        special_chars = r'+-&|!(){}[]^"~*?:\/ '
        escaped = []
        for char in query:
            if char in special_chars:
                escaped.append(f"\\{char}")
            else:
                escaped.append(char)
        return "".join(escaped)

    def search(
        self,
        query: str,
        limit: int = 20,
        context_id: int | None = None,
        context_lines: int = 1,
    ) -> SearchResults:
        """
        Search the index.

        Args:
            query: Search query (supports Tantivy query syntax)
            limit: Maximum results to return (applies after line expansion)
            context_id: Optional context to filter by
            context_lines: Lines of context before/after each match (default 1)

        Returns:
            SearchResults with matching lines (one result per line occurrence).
            If query syntax is invalid, falls back to literal search
            and sets fallback_reason.
        """
        self._ensure_initialized()
        start = time.monotonic()

        results = SearchResults()
        fallback_reason: str | None = None

        # Build query
        full_query = f"({query}) AND context_id:{context_id}" if context_id is not None else query

        searcher = self._index.searcher()

        # Try to parse query; on syntax error, fall back to escaped literal search
        try:
            parsed = self._index.parse_query(full_query, ["content", "symbols", "path"])
        except ValueError as e:
            # Tantivy raises ValueError on syntax errors
            error_msg = str(e)
            fallback_reason = f"query syntax error: {error_msg[:50]}"

            # Escape the original query and retry
            escaped_query = self._escape_query(query)
            escaped_full = (
                f"({escaped_query}) AND context_id:{context_id}"
                if context_id is not None
                else escaped_query
            )
            try:
                parsed = self._index.parse_query(escaped_full, ["content", "symbols", "path"])
            except ValueError:
                # Even escaped query failed - return empty results
                results.query_time_ms = int((time.monotonic() - start) * 1000)
                results.fallback_reason = "query could not be parsed even after escaping"
                return results

        # Search - fetch more docs than limit since we expand to lines
        # Tantivy returns 1 doc per file, we expand to N lines per file
        doc_limit = min(limit, 500)  # Cap doc fetch to avoid memory issues
        top_docs = searcher.search(parsed, doc_limit).hits
        results.total_hits = len(top_docs)

        for score, doc_addr in top_docs:
            doc = searcher.doc(doc_addr)
            file_path = doc.get_first("path") or ""
            content = doc.get_first("content") or ""
            ctx_id = doc.get_first("context_id")

            # Extract ALL matching lines from this file
            for snippet, line_num in self._extract_all_snippets(content, query, context_lines):
                if len(results.results) >= limit:
                    break
                results.results.append(
                    SearchResult(
                        file_path=file_path,
                        line=line_num,
                        column=0,
                        snippet=snippet,
                        score=score,
                        context_id=ctx_id,
                    )
                )

            if len(results.results) >= limit:
                break

        results.query_time_ms = int((time.monotonic() - start) * 1000)
        results.fallback_reason = fallback_reason
        return results

    def search_symbols(
        self,
        query: str,
        limit: int = 20,
        context_id: int | None = None,
        context_lines: int = 1,
    ) -> SearchResults:
        """Search only in symbol names."""
        self._ensure_initialized()

        symbol_query = f"symbols:{query}"
        return self.search(symbol_query, limit, context_id, context_lines)

    def search_path(
        self,
        pattern: str,
        limit: int = 20,
        context_id: int | None = None,
        context_lines: int = 1,
    ) -> SearchResults:
        """Search in file paths."""
        self._ensure_initialized()

        path_query = f"path:{pattern}"
        return self.search(path_query, limit, context_id, context_lines)

    def _extract_search_terms(self, query: str) -> list[str]:
        """Extract actual search terms from query, removing field prefixes and operators."""
        query_lower = query.lower()
        search_terms = []
        for term in query_lower.split():
            if ":" in term:
                # Field-prefixed term - take the value part
                _, value = term.split(":", 1)
                if value:
                    search_terms.append(value)
            elif term not in ("and", "or", "not"):
                search_terms.append(term)
        return search_terms

    def _extract_all_snippets(
        self,
        content: str,
        query: str,
        context_lines: int = 1,
    ) -> list[tuple[str, int]]:
        """Extract snippets for ALL lines matching the query.

        Args:
            content: File content
            query: Search query
            context_lines: Lines of context before and after match (default 1)

        Returns:
            List of (snippet_text, line_number) tuples where line_number is 1-indexed.
            If no match found, returns [(first lines, 1)].
        """
        lines = content.split("\n")
        search_terms = self._extract_search_terms(query)

        if not search_terms:
            # No valid search terms, return first lines
            snippet_size = 1 + 2 * context_lines
            return [("\n".join(lines[:snippet_size]), 1)]

        # Find ALL lines matching any search term
        matches: list[tuple[str, int]] = []
        for i, line in enumerate(lines):
            line_lower = line.lower()
            for term in search_terms:
                if term in line_lower:
                    # Build context snippet
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    snippet = "\n".join(lines[start:end])
                    matches.append((snippet, i + 1))  # 1-indexed
                    break  # Don't double-count if multiple terms match same line

        if not matches:
            snippet_size = 1 + 2 * context_lines
            return [("\n".join(lines[:snippet_size]), 1)]

        return matches

    def _extract_snippet(
        self,
        content: str,
        query: str,
        context_lines: int = 1,
    ) -> tuple[str, int]:
        """Extract first snippet matching the query (legacy compatibility).

        Returns:
            Tuple of (snippet_text, line_number) where line_number is 1-indexed.
            If no match found, returns (first lines, 1).
        """
        matches = self._extract_all_snippets(content, query, context_lines)
        return matches[0]

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
