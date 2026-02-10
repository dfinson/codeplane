"""Scope resolution utilities for search context.

Provides functionality to find enclosing scopes (function, class, block)
for any line in a file, using the indexed ScopeFact data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlmodel import col, select

from codeplane.index.models import File, ScopeFact, ScopeKind

if TYPE_CHECKING:
    from sqlmodel import Session


@dataclass
class ScopeRegion:
    """A resolved scope region with content."""

    start_line: int
    end_line: int
    kind: str  # ScopeKind value: function, class, block, file, etc. or "lines" for fallback
    resolved: bool  # True if structural, False if fallback


def find_enclosing_scope(
    session: Session,
    file_id: int,
    line: int,
    preference: Literal["function", "class", "block"] = "function",
) -> ScopeFact | None:
    """Find the enclosing scope for a line in a file.

    Args:
        session: Database session.
        file_id: File ID to search in.
        line: 1-indexed line number.
        preference: Preferred scope kind to return.

    Returns:
        The smallest enclosing scope of the preferred kind, or the smallest
        enclosing scope of any kind if preferred kind not found.
        Returns None if no scope contains the line.
    """
    # Get all scopes in the file that contain this line
    # Order by start_line desc, end_line asc to get smallest enclosing scope first
    stmt = (
        select(ScopeFact)
        .where(
            ScopeFact.file_id == file_id,
            ScopeFact.start_line <= line,
            ScopeFact.end_line >= line,
        )
        .order_by(col(ScopeFact.start_line).desc(), col(ScopeFact.end_line).asc())
    )
    scopes = list(session.exec(stmt).all())

    if not scopes:
        return None

    # Map preference to ScopeKind values
    pref_kinds = {
        "function": [ScopeKind.FUNCTION.value, ScopeKind.LAMBDA.value],
        "class": [ScopeKind.CLASS.value],
        "block": [ScopeKind.BLOCK.value, ScopeKind.COMPREHENSION.value],
    }

    # Try to find a scope of the preferred kind (smallest first)
    preferred_kinds = pref_kinds.get(preference, [])
    for scope in scopes:
        if scope.kind in preferred_kinds:
            return scope

    # Fall back to smallest enclosing scope (but not file-level)
    for scope in scopes:
        if scope.kind != ScopeKind.FILE.value:
            return scope

    # Last resort: file-level scope
    return scopes[0] if scopes else None


def resolve_scope_region(
    session: Session,
    repo_root: Path,
    file_id: int,
    line: int,
    preference: Literal["function", "class", "block"] = "function",
    fallback_lines: int = 25,
) -> tuple[ScopeRegion, str]:
    """Resolve the scope region and content for a line.

    Args:
        session: Database session.
        repo_root: Repository root path.
        file_id: File ID.
        line: 1-indexed line number of the match.
        preference: Preferred scope kind.
        fallback_lines: Lines of context for fallback.

    Returns:
        Tuple of (ScopeRegion, content_string).
    """
    # Get file path
    file = session.get(File, file_id)
    if file is None:
        # Can't resolve without file - return empty fallback
        return (
            ScopeRegion(
                start_line=line,
                end_line=line,
                kind="lines",
                resolved=False,
            ),
            "",
        )

    file_path = repo_root / file.path

    # Try to read file content
    try:
        content = file_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        total_lines = len(lines)
    except (OSError, UnicodeDecodeError):
        return (
            ScopeRegion(
                start_line=line,
                end_line=line,
                kind="lines",
                resolved=False,
            ),
            "",
        )

    # Try structural scope resolution
    scope = find_enclosing_scope(session, file_id, line, preference)

    if scope is not None and scope.kind != ScopeKind.FILE.value:
        # Successfully resolved to structural scope
        start = scope.start_line
        end = scope.end_line
        kind = scope.kind
        resolved = True
    else:
        # Fall back to line-based context
        start = max(1, line - fallback_lines)
        end = min(total_lines, line + fallback_lines)
        kind = "lines"
        resolved = False

    # Extract content (1-indexed to 0-indexed)
    content_lines = lines[start - 1 : end]
    content_str = "\n".join(content_lines)

    return (
        ScopeRegion(
            start_line=start,
            end_line=end,
            kind=kind,
            resolved=resolved,
        ),
        content_str,
    )


def resolve_scope_region_for_path(
    session: Session,
    repo_root: Path,
    file_path: str,
    line: int,
    preference: Literal["function", "class", "block"] = "function",
    fallback_lines: int = 25,
) -> tuple[ScopeRegion, str]:
    """Resolve scope region by file path (convenience wrapper).

    Args:
        session: Database session.
        repo_root: Repository root path.
        file_path: Relative file path.
        line: 1-indexed line number.
        preference: Preferred scope kind.
        fallback_lines: Lines of context for fallback.

    Returns:
        Tuple of (ScopeRegion, content_string).
    """
    # Look up file by path
    stmt = select(File).where(File.path == file_path)
    file = session.exec(stmt).first()

    if file is None or file.id is None:
        # File not in index - use line-based fallback
        full_path = repo_root / file_path
        try:
            content = full_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            total_lines = len(lines)
            start = max(1, line - fallback_lines)
            end = min(total_lines, line + fallback_lines)
            content_str = "\n".join(lines[start - 1 : end])
            return (
                ScopeRegion(
                    start_line=start,
                    end_line=end,
                    kind="lines",
                    resolved=False,
                ),
                content_str,
            )
        except (OSError, UnicodeDecodeError):
            return (
                ScopeRegion(
                    start_line=line,
                    end_line=line,
                    kind="lines",
                    resolved=False,
                ),
                "",
            )

    return resolve_scope_region(session, repo_root, file.id, line, preference, fallback_lines)
