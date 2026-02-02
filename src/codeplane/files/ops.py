"""File operations - read_files tool implementation.

Pure filesystem I/O. No index dependency.
Per SPEC.md §23.7 read_files tool specification.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileResult:
    """Result for a single file read."""

    path: str
    content: str
    language: str
    line_count: int
    range: tuple[int, int] | None = None  # (start, end) if partial
    metadata: dict[str, int] | None = None


@dataclass
class ReadFilesResult:
    """Result of read_files operation."""

    files: list[FileResult]


class FileOps:
    """File operations for read_files tool."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    def read_files(
        self,
        paths: str | list[str],
        *,
        ranges: list[dict[str, int]] | None = None,
        include_metadata: bool = False,
    ) -> ReadFilesResult:
        """Read file contents with optional line ranges.

        Args:
            paths: Single path or list of paths (relative to repo root)
            ranges: Optional line ranges per file [{"path": str, "start_line": int, "end_line": int}]
            include_metadata: Include file stats (size, mtime, git status)

        Returns:
            ReadFilesResult with file contents
        """
        if isinstance(paths, str):
            paths = [paths]

        # Build range lookup
        range_map: dict[str, tuple[int, int]] = {}
        if ranges:
            for r in ranges:
                path_key = str(r["path"]) if "path" in r else ""
                range_map[path_key] = (int(r["start_line"]), int(r["end_line"]))

        results: list[FileResult] = []
        for rel_path in paths:
            full_path = self._repo_root / rel_path
            if not full_path.is_file():
                continue

            content = full_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines(keepends=True)

            # Apply range if specified
            file_range = range_map.get(rel_path)
            if file_range:
                start, end = file_range
                # Convert to 0-indexed, clamp to bounds
                start_idx = max(0, start - 1)
                end_idx = min(len(lines), end)
                content = "".join(lines[start_idx:end_idx])
                line_count = end_idx - start_idx
            else:
                file_range = None
                line_count = len(lines)

            # Detect language from extension
            lang = _detect_language(full_path.suffix)

            metadata: dict[str, int] | None = None
            if include_metadata:
                stat = full_path.stat()
                metadata = {
                    "size_bytes": stat.st_size,
                    "modified_at": int(stat.st_mtime),
                }

            results.append(
                FileResult(
                    path=rel_path,
                    content=content,
                    language=lang,
                    line_count=line_count,
                    range=file_range,
                    metadata=metadata,
                )
            )

        return ReadFilesResult(files=results)


def _detect_language(suffix: str) -> str:
    """Simple language detection from file extension."""
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".rb": "ruby",
        ".php": "php",
        ".cs": "csharp",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".md": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".sql": "sql",
        ".sh": "shell",
        ".bash": "shell",
    }
    return mapping.get(suffix.lower(), "unknown")
