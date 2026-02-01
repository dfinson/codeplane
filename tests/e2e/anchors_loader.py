"""Anchor symbol loader for truth-based E2E validation.

Loads per-repo anchor definitions from tests/e2e/anchors/<repo>.yaml
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class AnchorSymbol:
    """Expected symbol definition."""

    name: str
    kind: str
    file: str
    line_range: tuple[int, int]


@dataclass
class ContextAnchors:
    """Anchors for a single context."""

    root: str
    language: str
    anchors: list[AnchorSymbol] = field(default_factory=list)


@dataclass
class SearchQuery:
    """Expected search result."""

    query: str
    expected_path_contains: str


@dataclass
class RepoAnchors:
    """Complete anchor spec for a repository."""

    repo: str
    commit: str
    contexts: list[ContextAnchors] = field(default_factory=list)
    search_queries: list[SearchQuery] = field(default_factory=list)


ANCHORS_DIR = Path(__file__).parent / "anchors"


def load_anchors(repo_key: str) -> RepoAnchors:
    """Load anchor spec for a repository.

    Args:
        repo_key: Repository key like "pallets/click"

    Returns:
        RepoAnchors with expected symbols and queries
    """
    # Convert owner/name to owner_name.yaml
    slug = repo_key.replace("/", "_")
    yaml_path = ANCHORS_DIR / f"{slug}.yaml"

    if not yaml_path.exists():
        msg = f"Anchor spec not found: {yaml_path}"
        raise FileNotFoundError(msg)

    with yaml_path.open() as f:
        data = yaml.safe_load(f)

    contexts = []
    for ctx_data in data.get("contexts", []):
        anchors = []
        for a in ctx_data.get("anchors", []):
            anchors.append(
                AnchorSymbol(
                    name=a["name"],
                    kind=a["kind"],
                    file=a["file"],
                    line_range=tuple(a["line_range"]),
                )
            )
        contexts.append(
            ContextAnchors(
                root=ctx_data["root"],
                language=ctx_data["language"],
                anchors=anchors,
            )
        )

    queries = []
    for q in data.get("search_queries", []):
        queries.append(
            SearchQuery(
                query=q["query"],
                expected_path_contains=q["expected_path_contains"],
            )
        )

    return RepoAnchors(
        repo=data["repo"],
        commit=data["commit"],
        contexts=contexts,
        search_queries=queries,
    )
