"""Summary formatting utilities for consistent terminal output.

Design principles:
- Every summary fits on one line (~80 chars max)
- Paths compressed for deep nesting
- Grammatically correct (1 file vs 2 files)
"""

from __future__ import annotations


def compress_path(path: str, max_len: int = 30) -> str:
    """Compress path to fit within max_len.

    Examples:
        src/codeplane/mcp/tools/mutation.py -> src/.../mutation.py
        short/path.py -> short/path.py (unchanged)
        very_long_filename_that_exceeds.py -> very_long_filename_that_exceeds.py
    """
    if len(path) <= max_len:
        return path

    parts = path.split("/")
    if len(parts) <= 2:
        return path  # Can't compress further

    # Keep first and last, replace middle with ...
    compressed = f"{parts[0]}/.../{parts[-1]}"
    if len(compressed) <= max_len:
        return compressed

    # Even that's too long, just use filename
    return parts[-1]


def format_path_list(
    paths: list[str],
    *,
    max_total: int = 50,
    max_shown: int = 3,
    compress: bool = True,
) -> str:
    """Format a list of paths, compressing as needed.

    Args:
        paths: List of file paths
        max_total: Maximum total string length
        max_shown: Maximum paths to show before "+N more"
        compress: Whether to compress individual paths

    Examples:
        ["a.py"] -> "a.py"
        ["a.py", "b.py"] -> "a.py, b.py"
        ["a.py", "b.py", "c.py", "d.py"] -> "a.py, b.py, +2 more"
    """
    if not paths:
        return ""

    # Compress paths if requested
    display_paths = [compress_path(p, 25) if compress else p for p in paths]

    if len(display_paths) == 1:
        return display_paths[0]

    # Show up to max_shown paths
    shown = display_paths[:max_shown]
    result = ", ".join(shown)

    if len(display_paths) > max_shown:
        result = ", ".join(display_paths[:2]) + f", +{len(display_paths) - 2} more"

    # If still too long, reduce further
    if len(result) > max_total:
        result = f"{display_paths[0]}, +{len(display_paths) - 1} more"

    if len(result) > max_total:
        return f"{len(paths)} files"

    return result


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Return grammatically correct singular/plural form.

    Args:
        count: The number of items
        singular: Singular form (e.g., "file")
        plural: Plural form (default: singular + "s")

    Returns:
        Formatted string like "1 file" or "3 files"
    """
    if plural is None:
        plural = singular + "s"
    word = singular if count == 1 else plural
    return f"{count} {word}"


def truncate_at_word(text: str, max_len: int = 40, suffix: str = "...") -> str:
    """Truncate text at word boundary.

    Args:
        text: Text to truncate
        max_len: Maximum length including suffix
        suffix: Suffix to append if truncated

    Examples:
        "fix: update parser to handle edge cases" -> "fix: update parser to..."
    """
    if len(text) <= max_len:
        return text

    # Find last space before max_len - suffix length
    cut_at = max_len - len(suffix)
    if cut_at <= 0:
        return suffix

    # Find last space
    space_idx = text.rfind(" ", 0, cut_at)
    if space_idx > 0:
        return text[:space_idx] + suffix

    # No space found, hard cut
    return text[:cut_at] + suffix


def truncate_query(query: str, max_len: int = 20) -> str:
    """Truncate a search query for display.

    Args:
        query: Search query string
        max_len: Maximum length including ellipsis

    Examples:
        "def _summarize_write" -> "def _summarize_wri..."
        "short" -> "short"
    """
    if len(query) <= max_len:
        return query
    return query[: max_len - 3] + "..."


def format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string.

    Args:
        seconds: Duration in seconds (must be non-negative)

    Returns:
        Formatted string like "0.3s", "1.5s", "2m 30s", "1h 5m"

    Examples:
        0.345 -> "0.3s"
        1.0 -> "1.0s"
        90.0 -> "1m 30s"
        3661.0 -> "1h 1m"
    """
    if seconds < 0:
        raise ValueError("Duration must be non-negative")

    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes = int(seconds // 60)
    remaining_secs = int(seconds % 60)

    if minutes < 60:
        return f"{minutes}m {remaining_secs}s"

    hours = minutes // 60
    remaining_mins = minutes % 60
    return f"{hours}h {remaining_mins}m"
