"""Lexical index for full-text search via Tantivy.

This module provides full-text search capabilities using Tantivy,
a fast Rust-based search engine. It supports:
- File content indexing
- Symbol name search
- Code snippet retrieval
- Fuzzy matching
"""

from __future__ import annotations

import re
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

    def _build_tantivy_query(self, query: str) -> str:
        """Build Tantivy query with AND semantics and phrase support.

        - Quoted strings (e.g., ``"async def"``) become Tantivy phrase queries.
        - Unquoted terms are joined with AND so all must appear.
        - Field-prefixed terms (e.g., ``symbols:foo``) are passed through.
        - Boolean operators (AND, OR, NOT) are preserved as-is.
        - Tantivy syntax characters in plain tokens are escaped.
        """
        tokens = re.findall(r'"[^"]+"|\S+', query)
        if not tokens:
            return query

        has_explicit_ops = any(
            t.upper() in ("AND", "OR", "NOT") for t in tokens if not t.startswith('"')
        )

        # Characters that are Tantivy query syntax operators
        _syntax_chars = set(r'+-&|!(){}[]^~*?\\/"')

        def _escape_token(token: str) -> str:
            """Escape Tantivy syntax chars in a plain token."""
            if not any(c in _syntax_chars for c in token):
                return token
            escaped: list[str] = []
            for ch in token:
                if ch in _syntax_chars:
                    escaped.append(f"\\{ch}")
                else:
                    escaped.append(ch)
            return "".join(escaped)

        # Known field prefixes that Tantivy should interpret
        _known_fields = frozenset(("content", "symbols", "path", "context_id"))

        parts: list[str] = []
        for token in tokens:
            if token.startswith('"') and token.endswith('"'):
                parts.append(token)
            elif token.upper() in ("AND", "OR", "NOT"):
                parts.append(token.upper())
            elif ":" in token and token.partition(":")[0] in _known_fields:
                parts.append(token)
            else:
                parts.append(_escape_token(token))

        if has_explicit_ops:
            # User provided explicit operators — preserve their structure
            return " ".join(parts)
        # No explicit operators — join with AND so all terms must match
        return " AND ".join(parts)

    def search(
        self,
        query: str,
        limit: int = 20,  # noqa: ARG002 - kept for API compat; callers handle limiting
        context_id: int | None = None,
        context_lines: int = 1,
        *,
        content_query: str | None = None,
    ) -> SearchResults:
        """
        Search the index.

        Args:
            query: Search query (supports Tantivy query syntax).
                Quoted strings are treated as exact phrases.
                Unquoted multi-term queries use AND semantics (all terms must appear).
            limit: Unused — all matches are returned; callers apply limits.
            context_id: Optional context to filter by
            context_lines: Lines of context before/after each match (default 1)
            content_query: Optional override for line-level content matching.
                When set, _extract_all_snippets uses this instead of `query`.
                Used by search_symbols to pass the original unprefixed terms.

        Returns:
            SearchResults with matching lines (one result per line occurrence),
            ordered by (path, line_number) for deterministic results.
            If query syntax is invalid, falls back to literal search
            and sets fallback_reason.
        """
        self._ensure_initialized()
        start = time.monotonic()

        results = SearchResults()
        fallback_reason: str | None = None
        literal_fallback = False

        # Build query with AND semantics for unquoted multi-term queries
        tantivy_query = self._build_tantivy_query(query)
        full_query = (
            f"({tantivy_query}) AND context_id:{context_id}"
            if context_id is not None
            else tantivy_query
        )

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

            # Fallback succeeded — use the original raw query as a literal
            # content query so _extract_search_terms treats every token
            # as a plain content term (no operator/field interpretation).
            if content_query is None:
                content_query = query
            literal_fallback = True

        # Fetch ALL matching documents — no BM25 doc limit.
        # Tantivy's value is the inverted index for fast token→file lookup;
        # we ignore BM25 scores and use deterministic (path, line) ordering.
        doc_limit = max(searcher.num_docs, 1)
        top_docs = searcher.search(parsed, limit=doc_limit).hits
        results.total_hits = len(top_docs)

        for _score, doc_addr in top_docs:
            doc = searcher.doc(doc_addr)
            file_path = doc.get_first("path") or ""
            content = doc.get_first("content") or ""
            ctx_id = doc.get_first("context_id")

            # Extract ALL matching lines from this file
            snippet_query = content_query if content_query is not None else query
            for snippet, line_num in self._extract_all_snippets(
                content, snippet_query, context_lines, literal=literal_fallback
            ):
                results.results.append(
                    SearchResult(
                        file_path=file_path,
                        line=line_num,
                        column=0,
                        snippet=snippet,
                        score=1.0,
                        context_id=ctx_id,
                    )
                )

        # Sort by (path, line_number) for deterministic ordering
        results.results.sort(key=lambda r: (r.file_path, r.line))

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

        # Prefix each non-operator, non-phrase token with symbols: so
        # _build_tantivy_query AND-joins them correctly as field queries.
        tokens = re.findall(r'"[^"]+"|\S+', query)
        prefixed = []
        for t in tokens:
            if t.startswith('"') or t.upper() in ("AND", "OR", "NOT") or ":" in t:
                prefixed.append(t)
            else:
                prefixed.append(f"symbols:{t}")
        symbol_query = " ".join(prefixed)
        return self.search(symbol_query, limit, context_id, context_lines, content_query=query)

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
        # Path searches match by file path, not content. Pass empty content_query
        # so _extract_all_snippets returns a document-level match at line 1
        # instead of trying (and failing) to match path terms in file content.
        return self.search(path_query, limit, context_id, context_lines, content_query="")

    def _extract_search_terms(
        self, query: str, *, literal: bool = False
    ) -> tuple[list[tuple[list[str], list[str]]], list[str], list[str]]:
        """Extract search terms from query, preserving boolean structure.

        Parses OR-groups, AND semantics within groups, NOT exclusions,
        and quoted phrases.  Field-prefixed terms for non-content fields
        (``path:``, ``symbols:``, ``context_id:``) are excluded;
        ``content:`` values are extracted as content terms.

        Args:
            query: The search query string.
            literal: When True, treat every whitespace-separated token as a
                plain content term (AND'd together).  No operator, field, or
                phrase interpretation.  Used for fallback/escaped queries.

        Returns:
            Tuple of ``(or_groups, negative_terms, negative_phrases)`` where:

            - **or_groups**: list of ``(phrases, terms)`` tuples connected
              by OR.  A line matches if ANY group matches.  Within a group
              ALL phrases and ALL terms must appear (AND semantics).
            - **negative_terms**: individual words that must NOT appear.
            - **negative_phrases**: quoted phrases that must NOT appear.
        """
        query_lower = query.lower()

        # Literal mode: treat every token as a plain content term
        if literal:
            terms = query_lower.split()
            if terms:
                return [([], terms)], [], []
            return [], [], []

        # Tokenise: preserve quoted phrases as single tokens
        tokens = re.findall(r'"[^"]+"|\S+', query_lower)

        # Split tokens into OR-separated groups, tracking NOT
        or_groups: list[tuple[list[str], list[str]]] = []
        negative_terms: list[str] = []
        negative_phrases: list[str] = []

        current_phrases: list[str] = []
        current_terms: list[str] = []
        negate_next = False

        # Content-field prefixes whose values should be treated as content terms
        _content_fields = frozenset(("content",))
        # Non-content field prefixes to skip entirely
        _skip_fields = frozenset(("path", "symbols", "context_id"))

        for token in tokens:
            upper = token.upper()

            if upper == "OR":
                # Flush current group
                if current_phrases or current_terms:
                    or_groups.append((current_phrases, current_terms))
                    current_phrases = []
                    current_terms = []
                negate_next = False
                continue

            if upper == "AND":
                # Implicit anyway — just skip
                continue

            if upper == "NOT":
                negate_next = True
                continue

            # Quoted phrase
            if token.startswith('"') and token.endswith('"') and len(token) > 2:
                phrase = token[1:-1]
                if negate_next:
                    negative_phrases.append(phrase)
                    negate_next = False
                else:
                    current_phrases.append(phrase)
                continue

            # Field-prefixed token
            if ":" in token:
                field, _, value = token.partition(":")
                if field in _content_fields and value:
                    # content:X — the value matches file content
                    if negate_next:
                        negative_terms.append(value)
                    else:
                        current_terms.append(value)
                # Skip non-content fields (path:, symbols:, context_id:)
                elif field in _skip_fields:
                    pass
                else:
                    # Unknown field prefix — treat whole token as literal
                    if negate_next:
                        negative_terms.append(token)
                    else:
                        current_terms.append(token)
                negate_next = False
                continue

            # Plain term
            if negate_next:
                negative_terms.append(token)
                negate_next = False
            else:
                current_terms.append(token)

        # Flush last group
        if current_phrases or current_terms:
            or_groups.append((current_phrases, current_terms))

        return or_groups, negative_terms, negative_phrases

    def _extract_all_snippets(
        self,
        content: str,
        query: str,
        context_lines: int = 1,
        *,
        literal: bool = False,
    ) -> list[tuple[str, int]]:
        """Extract snippets for ALL lines matching the query.

        Evaluates boolean structure: OR-groups are alternatives, NOT terms
        are excluded, and terms within a group are AND'd.

        Args:
            content: File content
            query: Search query
            context_lines: Lines of context before and after match (default 1)
            literal: Treat all tokens as plain literal terms (no operators)

        Returns:
            List of (snippet_text, line_number) tuples where line_number is 1-indexed.
            Returns empty list when no lines match (caller should skip the document).
        """
        lines = content.split("\n")
        or_groups, negative_terms, negative_phrases = self._extract_search_terms(
            query, literal=literal
        )

        if not or_groups and not negative_terms and not negative_phrases:
            # No content-level search terms (e.g., field-only query like path:foo).
            # Tantivy matched this document by a non-content field, so return
            # a document-level match at line 1.
            snippet_size = 1 + 2 * context_lines
            return [("\n".join(lines[:snippet_size]), 1)]

        # Find ALL lines matching the boolean structure
        matches: list[tuple[str, int]] = []
        for i, line in enumerate(lines):
            line_lower = line.lower()

            # Negative terms/phrases: skip line if any are present
            if any(nt in line_lower for nt in negative_terms):
                continue
            if any(np in line_lower for np in negative_phrases):
                continue

            # OR-groups: line matches if ANY group matches.
            # Within a group ALL phrases AND ALL terms must appear.
            matched = False
            if not or_groups:
                # Only negative constraints and no positive terms —
                # every line that survives the negative filter matches.
                matched = True
            else:
                for phrases, terms in or_groups:
                    if all(p in line_lower for p in phrases) and all(
                        t in line_lower for t in terms
                    ):
                        matched = True
                        break

            if not matched:
                continue

            # Build context snippet
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            snippet = "\n".join(lines[start:end])
            matches.append((snippet, i + 1))  # 1-indexed

        return matches

    def _extract_snippet(
        self,
        content: str,
        query: str,
        context_lines: int = 1,
        *,
        literal: bool = False,
    ) -> tuple[str, int]:
        """Extract first snippet matching the query.

        Returns:
            Tuple of (snippet_text, line_number) where line_number is 1-indexed.
            Returns empty snippet at line 1 when no content lines match.
        """
        matches = self._extract_all_snippets(content, query, context_lines, literal=literal)
        if matches:
            return matches[0]
        return ("", 1)

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
