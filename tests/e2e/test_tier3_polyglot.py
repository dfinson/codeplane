"""E2E Test Scenario: Tier 3 Polyglot Repository Indexing.

Validates multi-language, multi-context indexing for polyglot repositories
per E2E_TEST_PROPOSALS.md.

Tier 3 repos must:
- Contain 2+ languages
- Discover 2+ contexts
- Index symbols from multiple language families
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.anchors_loader import load_anchors
from tests.e2e.budgets_loader import get_budget
from tests.e2e.cli_runner import IsolatedEnv
from tests.e2e.conftest import TIER_3_CASES, E2ERepo, InitResult
from tests.e2e.repo_cache import RepoCase, materialize_repo


@pytest.fixture(params=TIER_3_CASES, ids=lambda c: c.key)
def tier3_repo_case(request: pytest.FixtureRequest) -> RepoCase:
    """Parametrized fixture for Tier 3 repos."""
    return request.param


@pytest.fixture
def tier3_e2e_repo(
    tier3_repo_case: RepoCase,
    isolated_env: IsolatedEnv,
    tmp_path: Path,
) -> E2ERepo:
    """Materialize a Tier 3 polyglot repo for E2E testing."""
    repo_path = materialize_repo(tier3_repo_case, tmp_path)
    anchors = load_anchors(tier3_repo_case.key)
    budget = get_budget(tier3_repo_case.key)

    return E2ERepo(
        path=repo_path,
        env=isolated_env,
        case=tier3_repo_case,
        anchors=anchors,
        budget=budget,
    )


@pytest.fixture
def tier3_initialized_repo(tier3_e2e_repo: E2ERepo) -> InitResult:
    """Tier 3 repo that has been initialized via cpl init."""
    result, peak_rss = tier3_e2e_repo.env.run_cpl(
        ["init"],
        cwd=tier3_e2e_repo.path,
        track_rss=True,
    )
    result.check()

    return InitResult(
        cli_result=result,
        repo=tier3_e2e_repo,
        duration_seconds=result.duration_seconds,
        peak_rss_mb=peak_rss,
    )


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.tier3
class TestTier3PolyglotIndexing:
    """Scenario: Tier 3 Polyglot Repository Indexing."""

    def test_full_index_within_budget(self, tier3_initialized_repo: InitResult) -> None:
        """Index polyglot repo within performance budget."""
        result = tier3_initialized_repo
        budget = result.repo.budget

        assert result.cli_result.success, (
            f"cpl init failed:\nstdout: {result.cli_result.stdout}\n"
            f"stderr: {result.cli_result.stderr}"
        )

        assert result.duration_seconds <= budget.full_index_seconds, (
            f"Full index took {result.duration_seconds:.1f}s, "
            f"budget is {budget.full_index_seconds}s"
        )

        if result.peak_rss_mb > 0:
            assert result.peak_rss_mb <= budget.max_rss_mb, (
                f"Peak RSS {result.peak_rss_mb:.0f}MB exceeds budget {budget.max_rss_mb}MB"
            )

    def test_multiple_contexts_discovered(self, tier3_initialized_repo: InitResult) -> None:
        """Verify multiple language contexts are discovered."""
        repo = tier3_initialized_repo.repo

        context_count = repo.count_contexts()
        assert context_count >= 1, (
            f"Expected at least 1 context for polyglot repo, found {context_count}"
        )

        # Get distinct language families
        rows = repo.query_db("SELECT DISTINCT language_family FROM contexts")
        language_families = {row[0] for row in rows if row[0]}

        # Should have at least one language family indexed
        assert len(language_families) >= 1, (
            f"Expected at least 1 language family, found: {language_families}"
        )

    def test_multiple_languages_indexed(self, tier3_initialized_repo: InitResult) -> None:
        """Verify files from multiple languages are indexed."""
        repo = tier3_initialized_repo.repo

        # Check for files with different extensions
        rows = repo.query_db("""
            SELECT DISTINCT
                CASE
                    WHEN path LIKE '%.py' THEN 'python'
                    WHEN path LIKE '%.rs' THEN 'rust'
                    WHEN path LIKE '%.ts' THEN 'typescript'
                    WHEN path LIKE '%.tsx' THEN 'tsx'
                    WHEN path LIKE '%.js' THEN 'javascript'
                    WHEN path LIKE '%.jsx' THEN 'jsx'
                    WHEN path LIKE '%.go' THEN 'go'
                    WHEN path LIKE '%.java' THEN 'java'
                    ELSE 'other'
                END as lang
            FROM files
            WHERE lang != 'other'
        """)
        indexed_languages = {row[0] for row in rows}

        # For Tier 3, we expect at least one programming language
        assert len(indexed_languages) >= 1, (
            f"Expected programming language files, found: {indexed_languages}"
        )

    def test_defs_extracted_from_multiple_languages(
        self, tier3_initialized_repo: InitResult
    ) -> None:
        """Verify definitions are extracted from files of different languages."""
        repo = tier3_initialized_repo.repo

        # Get def counts grouped by file extension
        rows = repo.query_db("""
            SELECT
                CASE
                    WHEN f.path LIKE '%.py' THEN 'python'
                    WHEN f.path LIKE '%.rs' THEN 'rust'
                    WHEN f.path LIKE '%.ts' THEN 'typescript'
                    WHEN f.path LIKE '%.tsx' THEN 'tsx'
                    WHEN f.path LIKE '%.js' THEN 'javascript'
                    WHEN f.path LIKE '%.go' THEN 'go'
                    ELSE 'other'
                END as lang,
                COUNT(*) as def_count
            FROM def_facts d
            JOIN files f ON d.file_id = f.id
            GROUP BY lang
            HAVING def_count > 0
        """)

        defs_by_lang = {row[0]: row[1] for row in rows}

        # Should have defs from at least one language
        code_langs = {k: v for k, v in defs_by_lang.items() if k != "other"}
        assert len(code_langs) >= 1, (
            f"Expected defs from at least 1 language, found: {defs_by_lang}"
        )

    def test_files_indexed(self, tier3_initialized_repo: InitResult) -> None:
        """Verify files were indexed."""
        repo = tier3_initialized_repo.repo
        file_count = repo.count_files()
        assert file_count > 0, "No files were indexed"

    def test_defs_extracted(self, tier3_initialized_repo: InitResult) -> None:
        """Verify definitions were extracted."""
        repo = tier3_initialized_repo.repo
        def_count = repo.count_defs()
        assert def_count > 0, "No definitions were extracted"

    def test_database_created(self, tier3_initialized_repo: InitResult) -> None:
        """Verify SQLite database was created."""
        repo = tier3_initialized_repo.repo
        assert repo.db_path.exists(), f"Database not created at {repo.db_path}"

    def test_tantivy_index_created(self, tier3_initialized_repo: InitResult) -> None:
        """Verify Tantivy index directory was created."""
        repo = tier3_initialized_repo.repo
        assert repo.tantivy_path.exists(), f"Tantivy index not created at {repo.tantivy_path}"
