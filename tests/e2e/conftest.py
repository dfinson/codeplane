"""E2E test fixtures and configuration.

Implements true E2E testing per E2E_TEST_PROPOSALS.md:
- Isolated venv with cpl installed from source
- Real repository cloning and caching
- CLI-based operations via subprocess
- Truth-based anchor validation via SQLite queries
- Performance budget enforcement
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.anchors_loader import RepoAnchors, load_anchors
from tests.e2e.budgets_loader import Budget, get_budget
from tests.e2e.cli_runner import CLIResult, IsolatedEnv, create_isolated_env
from tests.e2e.repo_cache import RepoCase, materialize_repo

# =============================================================================
# Repository Cases - All Tiers per E2E_TEST_PROPOSALS.md
# =============================================================================

# Tier 1: Small Single-Language Repos (1K–10K LOC)
TIER_1_CASES = [
    RepoCase(owner="pallets", name="click", commit="8.1.8", tier=1),
    RepoCase(owner="psf", name="requests", commit="v2.32.3", tier=1),
    RepoCase(owner="python-attrs", name="attrs", commit="24.2.0", tier=1),
    RepoCase(owner="more-itertools", name="more-itertools", commit="v10.5.0", tier=1),
]

# Tier 2: Medium Single-Language Repos (10K–50K LOC)
TIER_2_CASES = [
    RepoCase(owner="pallets", name="flask", commit="3.1.0", tier=2),
    RepoCase(owner="pydantic", name="pydantic", commit="v2.10.0", tier=2),
    RepoCase(owner="fastapi", name="fastapi", commit="0.115.0", tier=2),
]

# Tier 3: Polyglot / Multi-Context Repos
# TODO: Add polyglot repos with anchor specs


# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class E2ERepo:
    """An E2E test repository with isolated environment."""

    path: Path
    env: IsolatedEnv
    case: RepoCase
    anchors: RepoAnchors
    budget: Budget

    @property
    def db_path(self) -> Path:
        return self.path / ".codeplane" / "index.db"

    @property
    def tantivy_path(self) -> Path:
        return self.path / ".codeplane" / "tantivy"

    @property
    def config_path(self) -> Path:
        return self.path / ".codeplane" / "config.yaml"

    @property
    def cplignore_path(self) -> Path:
        return self.path / ".codeplane" / ".cplignore"

    def query_db(self, sql: str, params: tuple = ()) -> list[Any]:
        """Execute SQL query against the index database."""
        if not self.db_path.exists():
            raise FileNotFoundError(f"Index database not found: {self.db_path}")
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(sql, params)
            return cursor.fetchall()
        finally:
            conn.close()

    def count_files(self) -> int:
        """Count indexed files."""
        rows = self.query_db("SELECT COUNT(*) FROM files")
        return rows[0][0] if rows else 0

    def count_defs(self) -> int:
        """Count extracted definitions."""
        rows = self.query_db("SELECT COUNT(*) FROM def_facts")
        return rows[0][0] if rows else 0

    def count_contexts(self) -> int:
        """Count discovered contexts."""
        rows = self.query_db("SELECT COUNT(*) FROM contexts")
        return rows[0][0] if rows else 0

    def get_def_by_name(self, name: str) -> list[tuple]:
        """Get definitions by name."""
        return self.query_db(
            """
            SELECT d.name, d.kind, d.start_line, f.path
            FROM def_facts d
            JOIN files f ON d.file_id = f.id
            WHERE d.name = ?
            """,
            (name,),
        )


@dataclass
class InitResult:
    """Result from cpl init."""

    cli_result: CLIResult
    repo: E2ERepo
    duration_seconds: float
    peak_rss_mb: float


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def isolated_env(tmp_path_factory: pytest.TempPathFactory) -> Generator[IsolatedEnv, None, None]:
    """Session-scoped isolated environment with cpl installed.

    Creating the venv and installing deps is slow (~60s), so we reuse across tests.
    """
    base_path = tmp_path_factory.mktemp("e2e_env")
    env = create_isolated_env(base_path, "cpl_test")

    # Verify cpl is installed
    result, _ = env.run_cpl(["--version"])
    assert result.success, f"cpl not installed correctly: {result.stderr}"

    yield env


@pytest.fixture(params=TIER_1_CASES, ids=lambda c: c.key)
def tier1_case(request: pytest.FixtureRequest) -> RepoCase:
    """Parametrized fixture for Tier 1 repos."""
    return request.param


@pytest.fixture(params=TIER_2_CASES, ids=lambda c: c.key)
def tier2_case(request: pytest.FixtureRequest) -> RepoCase:
    """Parametrized fixture for Tier 2 repos."""
    return request.param


@pytest.fixture(params=TIER_1_CASES + TIER_2_CASES, ids=lambda c: c.key)
def repo_case(request: pytest.FixtureRequest) -> RepoCase:
    """Parametrized fixture for all repos (Tier 1 + Tier 2)."""
    return request.param


@pytest.fixture
def e2e_repo(
    tier1_case: RepoCase,
    isolated_env: IsolatedEnv,
    tmp_path: Path,
) -> E2ERepo:
    """Materialize a Tier 1 repo for E2E testing (not yet initialized)."""
    repo_path = materialize_repo(tier1_case, tmp_path)
    anchors = load_anchors(tier1_case.key)
    budget = get_budget(tier1_case.key)

    return E2ERepo(
        path=repo_path,
        env=isolated_env,
        case=tier1_case,
        anchors=anchors,
        budget=budget,
    )


@pytest.fixture
def initialized_repo(e2e_repo: E2ERepo) -> InitResult:
    """E2E repo that has been initialized via cpl init.

    Runs `cpl init` and returns the result with metrics.
    """
    result, peak_rss = e2e_repo.env.run_cpl(
        ["init"],
        cwd=e2e_repo.path,
        track_rss=True,
    )
    result.check()

    return InitResult(
        cli_result=result,
        repo=e2e_repo,
        duration_seconds=result.duration_seconds,
        peak_rss_mb=peak_rss,
    )


# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "e2e: mark test as end-to-end (real repos)")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "tier1: mark test for Tier 1 repos only")
    config.addinivalue_line("markers", "tier2: mark test for Tier 2 repos only")
    config.addinivalue_line("markers", "nightly: mark test for nightly runs only")
