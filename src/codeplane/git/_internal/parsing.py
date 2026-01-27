"""String parsing helpers for git ref names and messages."""

from __future__ import annotations

_REFS_HEADS_PREFIX = "refs/heads/"
_REFS_TAGS_PREFIX = "refs/tags/"


def extract_local_branch_from_remote(remote_ref: str) -> str:
    """Extract local branch name from remote ref (e.g., 'origin/main' -> 'main')."""
    return remote_ref.split("/", 1)[-1]


def extract_tag_name(refname: str) -> str | None:
    """Extract tag name from full ref (e.g., 'refs/tags/v1.0' -> 'v1.0')."""
    if refname.startswith(_REFS_TAGS_PREFIX):
        return refname[len(_REFS_TAGS_PREFIX) :]
    return None


def extract_branch_name(refname: str) -> str | None:
    """Extract branch name from full ref (e.g., 'refs/heads/main' -> 'main')."""
    if refname.startswith(_REFS_HEADS_PREFIX):
        return refname[len(_REFS_HEADS_PREFIX) :]
    return None


def first_line(text: str) -> str:
    """Get first line of text."""
    return text.splitlines()[0] if text else ""


def make_tag_ref(name: str) -> str:
    """Create full tag ref from name."""
    return f"{_REFS_TAGS_PREFIX}{name}"


def make_branch_ref(name: str) -> str:
    """Create full branch ref from name."""
    return f"{_REFS_HEADS_PREFIX}{name}"
