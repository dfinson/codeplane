"""File operations - read_files, list_files tool implementation.

Pure filesystem I/O. No index dependency.
Per SPEC.md §23.7 read_files tool specification.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from codeplane.core.languages import EXTENSION_TO_NAME
from codeplane.mcp.errors import MCPError, MCPErrorCode


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


@dataclass
class FileEntry:
    """A single file or directory entry."""

    name: str
    path: str  # Relative to repo root
    type: Literal["file", "directory"]
    size: int | None = None
    modified_at: int | None = None


@dataclass
class ListFilesResult:
    """Result of list_files operation."""

    path: str  # Directory listed (relative to repo root)
    entries: list[FileEntry] = field(default_factory=list)
    total: int = 0
    truncated: bool = False


def validate_path_in_repo(repo_root: Path, user_path: str) -> Path:
    """Validate that user_path is within repo_root, preventing traversal attacks.

    Args:
        repo_root: Repository root directory
        user_path: User-provided path (may be relative or absolute)

    Returns:
        Resolved absolute path if valid

    Raises:
        MCPError(PERMISSION_DENIED): If path escapes repo_root
    """
    resolved_root = repo_root.resolve()
    full_path = (repo_root / user_path).resolve()

    if not full_path.is_relative_to(resolved_root):
        raise MCPError(
            code=MCPErrorCode.PERMISSION_DENIED,
            message=f"Path '{user_path}' escapes repository root",
            remediation="Use paths relative to the repository root. Do not use '..' to escape the repo.",
            path=user_path,
            repo_root=str(resolved_root),
        )

    return full_path


class FileOps:
    """File operations for read_files and list_files tools."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    def list_files(
        self,
        path: str | None = None,
        *,
        pattern: str | None = None,
        recursive: bool = False,
        include_hidden: bool = False,
        include_metadata: bool = False,
        file_type: Literal["all", "file", "directory"] = "all",
        limit: int = 200,
    ) -> ListFilesResult:
        """List files in a directory with optional filtering.

        Args:
            path: Directory path relative to repo root (default: repo root)
            pattern: Glob pattern to filter (e.g., "*.py", "**/*.ts")
            recursive: Recurse into subdirectories
            include_hidden: Include dotfiles/dotdirs
            include_metadata: Include size and mtime
            file_type: Filter by type - "all", "file", or "directory"
            limit: Maximum entries to return

        Returns:
            ListFilesResult with matching entries
        """
        # Resolve directory with traversal validation
        if path:
            target_dir = validate_path_in_repo(self._repo_root, path)
            rel_base = path.rstrip("/")
        else:
            target_dir = self._repo_root
            rel_base = ""

        if not target_dir.is_dir():
            return ListFilesResult(path=rel_base or ".", entries=[], total=0)

        entries: list[FileEntry] = []
        total_count = 0

        # Choose iteration method
        if recursive or (pattern and "**" in pattern):
            iterator = target_dir.rglob("*")
        else:
            iterator = target_dir.iterdir()

        for item in iterator:
            # Skip hidden unless requested
            if not include_hidden and item.name.startswith("."):
                continue

            # Skip hidden parent directories in recursive mode
            if not include_hidden:
                try:
                    rel = item.relative_to(target_dir)
                    if any(part.startswith(".") for part in rel.parts[:-1]):
                        continue
                except ValueError:
                    continue

            # Apply file_type filter
            if file_type == "file" and not item.is_file():
                continue
            if file_type == "directory" and not item.is_dir():
                continue

            # Apply pattern filter
            if pattern:
                try:
                    rel_path = item.relative_to(target_dir)
                    rel_str = str(rel_path)
                except ValueError:
                    continue

                if not fnmatch.fnmatch(rel_str, pattern) and not fnmatch.fnmatch(
                    item.name, pattern
                ):
                    continue

            total_count += 1

            # Only collect up to limit
            if len(entries) < limit:
                try:
                    full_rel = item.relative_to(self._repo_root)
                    rel_path_str = str(full_rel)
                except ValueError:
                    rel_path_str = item.name

                entry = FileEntry(
                    name=item.name,
                    path=rel_path_str,
                    type="directory" if item.is_dir() else "file",
                )

                if include_metadata and item.is_file():
                    try:
                        stat = item.stat()
                        entry.size = stat.st_size
                        entry.modified_at = int(stat.st_mtime)
                    except OSError:
                        pass

                entries.append(entry)

        # Sort: directories first, then alphabetically
        entries.sort(key=lambda e: (e.type != "directory", e.name.lower()))

        return ListFilesResult(
            path=rel_base or ".",
            entries=entries,
            total=total_count,
            truncated=total_count > limit,
        )

    def read_files(
        self,
        paths: str | list[str],
        *,
        ranges: list[dict[str, Any]] | None = None,
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

        # Build range lookup - expects 'start' and 'end' keys (1-indexed)
        range_map: dict[str, tuple[int, int]] = {}
        if ranges:
            for r in ranges:
                path_key = str(r.get("path", ""))
                start = r.get("start")
                end = r.get("end")
                if start is not None and end is not None:
                    range_map[path_key] = (int(start), int(end))

        results: list[FileResult] = []
        for rel_path in paths:
            # Validate path doesn't escape repo root
            try:
                full_path = validate_path_in_repo(self._repo_root, rel_path)
            except MCPError:
                # Skip paths that escape repo root (silent skip matches existing not-found behavior)
                continue

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
            lang = EXTENSION_TO_NAME.get(full_path.suffix.lower(), "unknown")

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
