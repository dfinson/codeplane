"""Structural index for symbol extraction.

This module provides the T1 (syntactic) indexing pipeline that uses
Tree-sitter to extract symbols from source files. It handles:
- Parallel file processing with worker pools
- Symbol extraction and storage
- Interface hash computation for dependency tracking
"""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database

from codeplane.index._internal.parsing import SyntacticSymbol, TreeSitterParser
from codeplane.index.models import (
    Certainty,
    File,
    Layer,
    Occurrence,
    Role,
    Symbol,
)


@dataclass
class ExtractionResult:
    """Result of extracting symbols from a single file."""

    file_path: str
    symbols: list[SyntacticSymbol] = field(default_factory=list)
    interface_hash: str | None = None
    content_hash: str | None = None
    error: str | None = None
    parse_time_ms: int = 0


@dataclass
class BatchResult:
    """Result of processing a batch of files."""

    files_processed: int = 0
    symbols_extracted: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


def _extract_file(file_path: str, repo_root: str) -> ExtractionResult:
    """
    Extract symbols from a single file (worker function).

    This runs in a separate process for parallelization.
    """
    start = time.monotonic()
    result = ExtractionResult(file_path=file_path)

    try:
        full_path = Path(repo_root) / file_path
        if not full_path.exists():
            result.error = "File not found"
            return result

        # Read content
        content = full_path.read_bytes()
        result.content_hash = hashlib.sha256(content).hexdigest()

        # Parse and extract
        parser = TreeSitterParser()
        try:
            parse_result = parser.parse(full_path, content)
        except ValueError as e:
            # Unsupported file type
            result.error = str(e)
            return result

        # Extract symbols
        result.symbols = parser.extract_symbols(parse_result)

        # Compute interface hash
        result.interface_hash = parser.compute_interface_hash(result.symbols)

        result.parse_time_ms = int((time.monotonic() - start) * 1000)

    except Exception as e:
        result.error = str(e)

    return result


class StructuralIndexer:
    """
    Extracts symbols from source files using Tree-sitter.

    This is the T1 (syntactic) indexing layer. It provides:
    - Function/class/method definitions
    - Interface hashes for dependency tracking
    - Identifier occurrences (not resolved references)

    Usage::

        indexer = StructuralIndexer(db, repo_path)

        # Index specific files
        result = indexer.index_files(file_paths, context_id=1)

        # Index with parallel workers
        result = indexer.index_files(file_paths, context_id=1, workers=4)
    """

    def __init__(self, db: Database, repo_path: Path | str):
        self.db = db
        self.repo_path = Path(repo_path)
        self._parser = TreeSitterParser()

    def index_files(
        self,
        file_paths: list[str],
        context_id: int,
        file_id_map: dict[str, int] | None = None,
        workers: int = 1,
    ) -> BatchResult:
        """
        Index a batch of files.

        Args:
            file_paths: List of relative file paths
            context_id: Context ID for these files
            file_id_map: Optional mapping of paths to file IDs
            workers: Number of parallel workers (1 = sequential)

        Returns:
            BatchResult with statistics.
        """
        start = time.monotonic()
        result = BatchResult()

        if workers > 1:
            extractions = self._parallel_extract(file_paths, workers)
        else:
            extractions = self._sequential_extract(file_paths)

        # Store results in database
        with self.db.bulk_writer() as writer:
            for extraction in extractions:
                result.files_processed += 1

                if extraction.error:
                    result.errors.append(f"{extraction.file_path}: {extraction.error}")
                    continue

                # Get or create file ID
                file_id = file_id_map.get(extraction.file_path) if file_id_map else None
                if file_id is None:
                    # Need to look up or create file record
                    file_id = self._ensure_file_id(
                        extraction.file_path, extraction.content_hash, context_id
                    )

                # Insert symbols and track IDs
                for sym in extraction.symbols:
                    symbol_dict = {
                        "file_id": file_id,
                        "context_id": context_id,
                        "name": sym.name,
                        "kind": sym.kind,
                        "line": sym.line,
                        "column": sym.column,
                        "end_line": sym.end_line,
                        "end_column": sym.end_column,
                        "signature": sym.signature,
                        "layer": Layer.SYNTACTIC.value,
                        "certainty": Certainty.CERTAIN.value,
                    }
                    # Insert symbol and get ID via key lookup
                    symbol_ids = writer.insert_many_returning_ids(
                        Symbol,
                        [symbol_dict],
                        ["file_id", "name", "line", "column"],
                    )
                    result.symbols_extracted += 1

                    # Get the symbol ID we just inserted
                    key = (file_id, sym.name, sym.line, sym.column)
                    symbol_id = symbol_ids.get(key, 0)

                    # Insert definition occurrence
                    occ_dict = {
                        "symbol_id": symbol_id,
                        "file_id": file_id,
                        "context_id": context_id,
                        "start_line": sym.line,
                        "start_col": sym.column,
                        "end_line": sym.end_line,
                        "end_col": sym.end_column,
                        "role": Role.DEFINITION.value,
                        "layer": Layer.SYNTACTIC.value,
                    }
                    writer.insert_many(Occurrence, [occ_dict])

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    def _sequential_extract(self, file_paths: list[str]) -> list[ExtractionResult]:
        """Extract symbols sequentially."""
        results = []
        for path in file_paths:
            result = _extract_file(path, str(self.repo_path))
            results.append(result)
        return results

    def _parallel_extract(self, file_paths: list[str], workers: int) -> list[ExtractionResult]:
        """Extract symbols in parallel using process pool."""
        results = []
        repo_root = str(self.repo_path)

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_extract_file, path, repo_root): path for path in file_paths}

            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    path = futures[future]
                    results.append(ExtractionResult(file_path=path, error=str(e)))

        return results

    def _ensure_file_id(self, file_path: str, content_hash: str | None, _context_id: int) -> int:
        """Ensure file exists in database and return its ID."""
        with self.db.session() as session:
            # Look for existing file
            from sqlmodel import select

            stmt = select(File).where(File.path == file_path)
            existing = session.exec(stmt).first()

            if existing and existing.id is not None:
                return existing.id

            # Create new file record
            file = File(
                path=file_path,
                content_hash=content_hash,
                language_family=self._detect_family(file_path),
            )
            session.add(file)
            session.commit()
            session.refresh(file)
            return file.id if file.id is not None else 0

    def _detect_family(self, file_path: str) -> str | None:
        """Detect language family from file path."""
        ext = Path(file_path).suffix.lower()
        ext_map = {
            ".py": "python",
            ".pyi": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "javascript",
            ".tsx": "javascript",
            ".go": "go",
            ".rs": "rust",
            ".java": "jvm",
            ".kt": "jvm",
            ".scala": "jvm",
            ".cs": "dotnet",
            ".cpp": "cpp",
            ".c": "cpp",
            ".h": "cpp",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
        }
        return ext_map.get(ext)

    def extract_single(self, file_path: str) -> ExtractionResult:
        """Extract symbols from a single file without storing."""
        return _extract_file(file_path, str(self.repo_path))

    def compute_batch_interface_hash(self, file_paths: list[str]) -> str:
        """Compute combined interface hash for multiple files."""
        hashes = []
        for path in sorted(file_paths):
            result = self.extract_single(path)
            if result.interface_hash:
                hashes.append(result.interface_hash)

        combined = "\n".join(hashes)
        return hashlib.sha256(combined.encode()).hexdigest()


def index_context(
    db: Any,
    repo_path: Path | str,
    context_id: int,
    file_paths: list[str],
    workers: int = os.cpu_count() or 1,
) -> BatchResult:
    """
    Convenience function to index all files in a context.

    Args:
        db: Database instance
        repo_path: Repository root path
        context_id: Context ID
        file_paths: List of relative file paths
        workers: Number of parallel workers

    Returns:
        BatchResult with statistics.
    """
    indexer = StructuralIndexer(db, repo_path)
    return indexer.index_files(file_paths, context_id, workers=workers)
