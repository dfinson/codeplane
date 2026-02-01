"""E2E test fixtures and configuration.

Implements per E2E_TEST_PROPOSALS.md:
- Real repository cloning and caching
- Truth-based anchor validation
- Performance budget enforcement
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.e2e.anchors_loader import RepoAnchors, load_anchors
from tests.e2e.budgets_loader import Budget, get_budget
from tests.e2e.repo_cache import RepoCase, materialize_repo

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database
    from codeplane.index.ops import IndexCoordinator


# =============================================================================
# Repository Cases (Tier 1 for PR CI)
# =============================================================================

TIER_1_CASES = [
    RepoCase(owner="pallets", name="click", commit="8.1.8", tier=1),
    RepoCase(owner="psf", name="requests", commit="v2.32.3", tier=1),
]

TIER_2_CASES = [
    # Add Tier 2 repos when ready
]


# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class IndexedRepo:
    """An indexed real-world repository."""

    path: Path
    coordinator: IndexCoordinator
    db: Database
    case: RepoCase
    anchors: RepoAnchors
    budget: Budget


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(params=TIER_1_CASES, ids=lambda c: c.key)
def repo_case(request: pytest.FixtureRequest) -> RepoCase:
    """Parametrized fixture providing each Tier 1 repo case."""
    return request.param


@pytest.fixture
def materialized_repo(repo_case: RepoCase, tmp_path: Path) -> Path:
    """Materialize a repo to tmp for testing."""
    return materialize_repo(repo_case, tmp_path)


@pytest.fixture
def indexed_repo(repo_case: RepoCase, tmp_path: Path) -> IndexedRepo:
    """Initialize and index a real repository.

    This is the main fixture for E2E tests.
    """
    from codeplane.index.ops import IndexCoordinator

    # Materialize repo
    repo_path = materialize_repo(repo_case, tmp_path)

    # Setup paths
    codeplane_dir = repo_path / ".codeplane"
    db_path = codeplane_dir / "index.db"
    tantivy_path = codeplane_dir / "tantivy"
    tantivy_path.mkdir(exist_ok=True)

    # Create coordinator
    coord = IndexCoordinator(
        repo_root=repo_path,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )

    # Initialize (blocking)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coord.initialize())
    finally:
        loop.close()

    # Load anchors and budget
    anchors = load_anchors(repo_case.key)
    budget = get_budget(repo_case.key)

    return IndexedRepo(
        path=repo_path,
        coordinator=coord,
        db=coord.db,
        case=repo_case,
        anchors=anchors,
        budget=budget,
    )


# =============================================================================
# Pytest Markers
# =============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "e2e: mark test as end-to-end (real repos)")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "nightly: mark test for nightly runs only")
