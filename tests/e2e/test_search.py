"""E2E Test Scenario 4: Lexical Search Quality.

Validates search returns expected results using anchor-based assertions
per E2E_TEST_PROPOSALS.md.

NOTE: These tests require a `cpl search` CLI command which is not yet
implemented. Tests are marked as xfail until the CLI is available.
Database queries are used as a workaround for basic validation.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import InitResult


@pytest.mark.e2e
@pytest.mark.slow
class TestSearchQuality:
    """Scenario 4: Lexical Search Quality."""

    @pytest.mark.xfail(reason="cpl search CLI not yet implemented")
    def test_anchor_search_queries(self, initialized_repo: InitResult) -> None:
        """Verify search queries return expected files.

        Requires: cpl search <query>
        """
        repo = initialized_repo.repo
        anchors = repo.anchors

        if not anchors.search_queries:
            pytest.skip("No search queries defined for this repo")

        failures = []

        for sq in anchors.search_queries:
            result, _ = repo.env.run_cpl(
                ["search", sq.query],
                cwd=repo.path,
            )

            if not result.success:
                failures.append(f"Query '{sq.query}' failed: {result.stderr}")
                continue

            # Check if expected path is in output
            if sq.expected_path_contains not in result.stdout:
                failures.append(
                    f"Query '{sq.query}': expected path containing "
                    f"'{sq.expected_path_contains}' not in output"
                )

        assert not failures, "Search query failures:\n" + "\n".join(failures)

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
