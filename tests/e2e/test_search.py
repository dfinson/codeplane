"""E2E Test Scenario 4: Lexical Search Quality.

Validates search returns expected results using anchor-based assertions
per E2E_TEST_PROPOSALS.md.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.conftest import IndexedRepo


@pytest.mark.e2e
@pytest.mark.slow
class TestSearchQuality:
    """Scenario 4: Lexical Search Quality."""

    def test_anchor_search_queries(self, indexed_repo: IndexedRepo) -> None:
        """Verify search queries return expected files."""
        anchors = indexed_repo.anchors

        if not anchors.search_queries:
            pytest.skip("No search queries defined for this repo")

        loop = asyncio.new_event_loop()
        failures = []

        try:
            for sq in anchors.search_queries:
                results = loop.run_until_complete(
                    indexed_repo.coordinator.search(sq.query, mode="text", limit=20)
                )

                if not results:
                    failures.append(f"Query '{sq.query}' returned no results")
                    continue

                # Check if expected path is in top results
                paths = [r.path for r in results]
                found = any(sq.expected_path_contains in p for p in paths)

                if not found:
                    failures.append(
                        f"Query '{sq.query}': expected path containing "
                        f"'{sq.expected_path_contains}' not in results: {paths[:5]}"
                    )
        finally:
            loop.close()

        assert not failures, "Search query failures:\n" + "\n".join(failures)

    def test_symbol_search_finds_anchors(self, indexed_repo: IndexedRepo) -> None:
        """Verify symbol search finds anchor symbol names."""
        anchors = indexed_repo.anchors
        loop = asyncio.new_event_loop()
        failures = []

        try:
            # Pick first few anchor symbols to search
            symbols_to_search = []
            for ctx in anchors.contexts:
                for anchor in ctx.anchors[:3]:  # First 3 per context
                    symbols_to_search.append((anchor.name, anchor.file))

            for name, expected_file in symbols_to_search:
                results = loop.run_until_complete(
                    indexed_repo.coordinator.search(name, mode="symbol", limit=20)
                )

                if not results:
                    failures.append(f"Symbol '{name}' not found")
                    continue

                # Check expected file is in results
                paths = [r.path for r in results]
                found = any(expected_file in p for p in paths)

                if not found:
                    failures.append(
                        f"Symbol '{name}': expected file '{expected_file}' "
                        f"not in results: {paths[:5]}"
                    )
        finally:
            loop.close()

        assert not failures, "Symbol search failures:\n" + "\n".join(failures)
