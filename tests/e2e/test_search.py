"""E2E Test Scenario 4: Search Quality via Database.

Validates search/index quality using direct database queries
per E2E_TEST_PROPOSALS.md.

Search is exposed via MCP tools only, not CLI. These tests validate
the underlying indexed data that powers search.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import InitResult


@pytest.mark.e2e
@pytest.mark.slow
class TestSearchQuality:
    """Scenario 4: Search Quality via Database."""

    def test_anchor_symbols_in_database(self, initialized_repo: InitResult) -> None:
        """Verify anchor symbols are in the database (workaround for search)."""
        repo = initialized_repo.repo
        anchors = repo.anchors
        failures = []

        # Check first few anchor symbols exist in def_facts
        for ctx in anchors.contexts:
            for anchor in ctx.anchors[:5]:  # First 5 per context
                matches = repo.query_db(
                    """
                    SELECT d.name, f.path
                    FROM def_facts d
                    JOIN files f ON d.file_id = f.id
                    WHERE d.name = ?
                    """,
                    (anchor.name,),
                )

                if not matches:
                    failures.append(f"Symbol '{anchor.name}' not in def_facts")
                    continue

                # Check expected file is among results
                paths = [row[1] for row in matches]
                if not any(anchor.file in p for p in paths):
                    failures.append(
                        f"Symbol '{anchor.name}': expected file '{anchor.file}' "
                        f"not in results: {paths[:5]}"
                    )

        assert not failures, "Symbol lookup failures:\n" + "\n".join(failures)

    def test_def_facts_populated(self, initialized_repo: InitResult) -> None:
        """Verify def_facts table has content."""
        repo = initialized_repo.repo
        count = repo.count_defs()
        assert count > 0, "def_facts table is empty"

    def test_files_table_populated(self, initialized_repo: InitResult) -> None:
        """Verify files table has content."""
        repo = initialized_repo.repo
        count = repo.count_files()
        assert count > 0, "files table is empty"


@pytest.mark.e2e
class TestQueryPerformance:
    """Scenario 5: Query Performance Micro-Budget."""

    def test_def_query_under_budget(self, initialized_repo: InitResult) -> None:
        """20 symbol lookups complete under 1s."""
        import time

        repo = initialized_repo.repo
        anchors = repo.anchors

        # Collect up to 20 anchor symbol names
        symbols = [a.name for ctx in anchors.contexts for a in ctx.anchors][:20]

        if len(symbols) < 5:
            pytest.skip("Not enough anchor symbols for query performance test")

        t0 = time.perf_counter()
        for name in symbols:
            repo.query_db(
                "SELECT name FROM def_facts WHERE name = ?",
                (name,),
            )
        elapsed = time.perf_counter() - t0

        assert elapsed < 1.0, f"{len(symbols)} queries took {elapsed:.2f}s (budget: 1s)"

    def test_file_listing_under_budget(self, initialized_repo: InitResult) -> None:
        """File listing query completes under 500ms."""
        import time

        repo = initialized_repo.repo

        t0 = time.perf_counter()
        repo.query_db("SELECT path, line_count FROM files LIMIT 1000")
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.5, f"File listing took {elapsed:.2f}s (budget: 0.5s)"

    def test_def_count_by_kind_under_budget(self, initialized_repo: InitResult) -> None:
        """Grouped count query completes under 500ms."""
        import time

        repo = initialized_repo.repo

        t0 = time.perf_counter()
        repo.query_db("SELECT kind, COUNT(*) FROM def_facts GROUP BY kind")
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.5, f"Grouped count took {elapsed:.2f}s (budget: 0.5s)"
