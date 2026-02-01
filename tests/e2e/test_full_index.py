"""E2E Test Scenario 1: Full Index from Scratch (Truth-Based).

Validates correctness and performance of full repository indexing
using anchor symbol validation per E2E_TEST_PROPOSALS.md.

Tests run via actual CLI (subprocess cpl init) for true E2E coverage.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import InitResult


@pytest.mark.e2e
@pytest.mark.slow
class TestFullIndexTruthBased:
    """Scenario 1: Full Index from Scratch."""

    def test_full_index_within_budget(self, initialized_repo: InitResult) -> None:
        """Index repo and validate performance budget."""
        result = initialized_repo
        budget = result.repo.budget

        # Validate success
        assert result.cli_result.success, (
            f"cpl init failed:\nstdout: {result.cli_result.stdout}\n"
            f"stderr: {result.cli_result.stderr}"
        )

        # Validate performance budget
        assert result.duration_seconds <= budget.full_index_seconds, (
            f"Full index took {result.duration_seconds:.1f}s, "
            f"budget is {budget.full_index_seconds}s"
        )

        # RSS tracking only works with psutil
        if result.peak_rss_mb > 0:
            assert result.peak_rss_mb <= budget.max_rss_mb, (
                f"Peak RSS {result.peak_rss_mb:.0f}MB exceeds budget {budget.max_rss_mb}MB"
            )

    def test_contexts_discovered(self, initialized_repo: InitResult) -> None:
        """Verify expected contexts are discovered."""
        repo = initialized_repo.repo
        anchors = repo.anchors

        # Query contexts via SQLite
        rows = repo.query_db("SELECT language_family FROM contexts")
        actual_languages = {row[0] for row in rows}

        # Should have at least one context per expected language
        expected_languages = {ctx.language for ctx in anchors.contexts}

        for lang in expected_languages:
            assert lang in actual_languages, (
                f"Expected {lang} context not found. Found: {actual_languages}"
            )

    def test_anchor_symbols_present(self, initialized_repo: InitResult) -> None:
        """Verify all anchor symbols exist in the index."""
        repo = initialized_repo.repo
        anchors = repo.anchors
        missing = []

        for ctx in anchors.contexts:
            for anchor in ctx.anchors:
                # Query for this specific anchor
                matches = repo.query_db(
                    """
                    SELECT d.name, d.start_line, f.path
                    FROM def_facts d
                    JOIN files f ON d.file_id = f.id
                    WHERE d.name = ?
                    AND f.path LIKE ?
                    """,
                    (anchor.name, f"%{anchor.file}"),
                )

                if not matches:
                    missing.append(f"{anchor.file}:{anchor.name}")
                    continue

                # Check at least one match is within line range
                in_range = any(
                    anchor.line_range[0] <= row[1] <= anchor.line_range[1] for row in matches
                )
                if not in_range:
                    lines = [row[1] for row in matches]
                    missing.append(
                        f"{anchor.file}:{anchor.name} (lines {lines} not in {anchor.line_range})"
                    )

        assert not missing, "Missing/misplaced anchor symbols:\n" + "\n".join(missing)

    def test_files_indexed(self, initialized_repo: InitResult) -> None:
        """Verify files were indexed."""
        repo = initialized_repo.repo
        file_count = repo.count_files()
        assert file_count > 0, "No files were indexed"

    def test_defs_extracted(self, initialized_repo: InitResult) -> None:
        """Verify definitions were extracted."""
        repo = initialized_repo.repo
        def_count = repo.count_defs()
        assert def_count > 0, "No definitions were extracted"

    def test_database_created(self, initialized_repo: InitResult) -> None:
        """Verify SQLite database was created."""
        repo = initialized_repo.repo
        assert repo.db_path.exists(), f"Database not created at {repo.db_path}"

    def test_tantivy_index_created(self, initialized_repo: InitResult) -> None:
        """Verify Tantivy index directory was created."""
        repo = initialized_repo.repo
        assert repo.tantivy_path.exists(), f"Tantivy index not created at {repo.tantivy_path}"

    def test_config_created(self, initialized_repo: InitResult) -> None:
        """Verify config file was created."""
        repo = initialized_repo.repo
        assert repo.config_path.exists(), f"Config not created at {repo.config_path}"

    def test_cplignore_created(self, initialized_repo: InitResult) -> None:
        """Verify .cplignore file was created."""
        repo = initialized_repo.repo
        assert repo.cplignore_path.exists(), f".cplignore not created at {repo.cplignore_path}"
