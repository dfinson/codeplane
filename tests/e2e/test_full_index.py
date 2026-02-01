"""E2E Test Scenario 1: Full Index from Scratch (Truth-Based).

Validates correctness and performance of full repository indexing
using anchor symbol validation per E2E_TEST_PROPOSALS.md.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from sqlmodel import select

from codeplane.index.models import Context, DefFact, File
from codeplane.index.ops import IndexCoordinator
from tests.e2e.budgets_loader import get_budget, rss_monitor
from tests.e2e.conftest import IndexedRepo
from tests.e2e.repo_cache import RepoCase, materialize_repo


@pytest.mark.e2e
@pytest.mark.slow
class TestFullIndexTruthBased:
    """Scenario 1: Full Index from Scratch."""

    def test_full_index_within_budget(self, repo_case: RepoCase, tmp_path: Path) -> None:
        """Index repo and validate performance budget."""
        repo_path = materialize_repo(repo_case, tmp_path)
        budget = get_budget(repo_case.key)

        # Setup paths
        codeplane_dir = repo_path / ".codeplane"
        db_path = codeplane_dir / "index.db"
        tantivy_path = codeplane_dir / "tantivy"
        tantivy_path.mkdir(exist_ok=True)

        with rss_monitor() as rss:
            t0 = time.perf_counter()

            coord = IndexCoordinator(
                repo_root=repo_path,
                db_path=db_path,
                tantivy_path=tantivy_path,
            )

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(coord.initialize())
            finally:
                loop.close()

            elapsed = time.perf_counter() - t0

        # Validate no errors
        assert result.errors == [], f"Initialization errors: {result.errors}"

        # Validate performance budget
        assert elapsed <= budget.full_index_seconds, (
            f"Full index took {elapsed:.1f}s, budget is {budget.full_index_seconds}s"
        )
        assert rss.peak_mb <= budget.max_rss_mb, (
            f"Peak RSS {rss.peak_mb:.0f}MB exceeds budget {budget.max_rss_mb}MB"
        )

    def test_contexts_discovered(self, indexed_repo: IndexedRepo) -> None:
        """Verify expected contexts are discovered."""
        anchors = indexed_repo.anchors

        with indexed_repo.db.session() as session:
            contexts = list(session.exec(select(Context)).all())

        # Should have at least one context per expected language
        expected_languages = {ctx.language for ctx in anchors.contexts}
        actual_languages = {c.language_family for c in contexts}

        for lang in expected_languages:
            assert lang in actual_languages, (
                f"Expected {lang} context not found. Found: {actual_languages}"
            )

    def test_anchor_symbols_present(self, indexed_repo: IndexedRepo) -> None:
        """Verify all anchor symbols exist in the index."""
        anchors = indexed_repo.anchors
        missing = []

        with indexed_repo.db.session() as session:
            # Build file path lookup
            files = {f.id: f.path for f in session.exec(select(File)).all()}

            # Get all defs
            defs = list(session.exec(select(DefFact)).all())

            # Build def lookup by (name, file_suffix)
            def_lookup: dict[tuple[str, str], list[DefFact]] = {}
            for d in defs:
                file_path = files.get(d.file_id, "")
                for anchor_ctx in anchors.contexts:
                    for anchor in anchor_ctx.anchors:
                        if file_path.endswith(anchor.file):
                            key = (d.name, anchor.file)
                            if key not in def_lookup:
                                def_lookup[key] = []
                            def_lookup[key].append(d)

            # Validate each anchor
            for ctx in anchors.contexts:
                for anchor in ctx.anchors:
                    key = (anchor.name, anchor.file)
                    matches = def_lookup.get(key, [])

                    if not matches:
                        missing.append(f"{anchor.file}:{anchor.name}")
                        continue

                    # Check at least one match is within line range
                    in_range = any(
                        anchor.line_range[0] <= d.start_line <= anchor.line_range[1]
                        for d in matches
                    )
                    if not in_range:
                        lines = [d.start_line for d in matches]
                        missing.append(
                            f"{anchor.file}:{anchor.name} "
                            f"(lines {lines} not in {anchor.line_range})"
                        )

        assert not missing, "Missing/misplaced anchor symbols:\n" + "\n".join(missing)

    def test_epoch_published(self, indexed_repo: IndexedRepo) -> None:
        """Verify epoch is published after initialization."""
        epoch_mgr = indexed_repo.coordinator._epoch_manager
        assert epoch_mgr is not None

        current = epoch_mgr.get_current_epoch()
        assert current >= 1, f"Epoch not published: {current}"

    def test_await_epoch_works(self, indexed_repo: IndexedRepo) -> None:
        """Verify await_epoch completes for current epoch."""
        epoch_mgr = indexed_repo.coordinator._epoch_manager
        assert epoch_mgr is not None

        current = epoch_mgr.get_current_epoch()
        ok = epoch_mgr.await_epoch(current, timeout_seconds=1.0)
        assert ok, "await_epoch failed for current epoch"
