"""E2E Test Scenario 2: Incremental Update Isolated.

Validates that single-file edits only reindex affected files
per E2E_TEST_PROPOSALS.md.

NOTE: These tests require a `cpl reindex` CLI command which is not yet
implemented. Tests are marked as xfail until the CLI is available.
For now, we validate the database state after a full re-init.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import InitResult


@pytest.mark.e2e
@pytest.mark.slow
class TestIncrementalUpdate:
    """Scenario 2: Incremental Update Isolated."""

    @pytest.mark.xfail(reason="cpl reindex CLI not yet implemented")
    def test_incremental_reindex_updates_only_changed_file(
        self, initialized_repo: InitResult
    ) -> None:
        """Verify incremental reindex only touches the changed file.

        Requires: cpl reindex <path>
        """
        repo = initialized_repo.repo

        # Find a Python file to edit
        rows = repo.query_db("SELECT path FROM files WHERE path LIKE '%.py' LIMIT 1")
        if not rows:
            pytest.skip("No Python files found")

        target_path = repo.path / rows[0][0]

        # Get def count before
        before_count = repo.count_defs()

        # Edit the file - append a new function
        original_content = target_path.read_text()
        new_content = original_content + "\n\ndef _injected_e2e_test_func():\n    pass\n"
        target_path.write_text(new_content)

        try:
            # Trigger incremental reindex (requires cpl reindex <path>)
            result, _ = repo.env.run_cpl(
                ["reindex", str(target_path.relative_to(repo.path))],
                cwd=repo.path,
            )
            result.check()

            # Verify the injected function exists
            matches = repo.query_db(
                "SELECT name FROM def_facts WHERE name = ?",
                ("_injected_e2e_test_func",),
            )
            assert len(matches) > 0, "Injected function not found after reindex"

            # Verify def count increased by 1
            after_count = repo.count_defs()
            assert after_count == before_count + 1, (
                f"Expected 1 new def, got {after_count - before_count}"
            )

        finally:
            # Restore original content
            target_path.write_text(original_content)

    def test_reinit_includes_new_file(self, initialized_repo: InitResult) -> None:
        """Verify re-running init picks up new files."""
        repo = initialized_repo.repo

        # Create a new Python file
        new_file = repo.path / "e2e_test_new_module.py"
        new_file.write_text('''"""E2E test module."""

def e2e_test_new_function():
    """A new function for testing."""
    return 42
''')

        try:
            # Re-run init with --force
            result, _ = repo.env.run_cpl(["init", "--force"], cwd=repo.path)
            result.check()

            # Verify new function is indexed
            matches = repo.query_db(
                "SELECT name FROM def_facts WHERE name = ?",
                ("e2e_test_new_function",),
            )
            assert len(matches) > 0, "New function not found after re-init"

        finally:
            if new_file.exists():
                new_file.unlink()

    def test_reinit_removes_deleted_file(self, initialized_repo: InitResult) -> None:
        """Verify re-running init removes deleted files from index."""
        repo = initialized_repo.repo

        # Create and index a file
        temp_file = repo.path / "e2e_temp_to_delete.py"
        temp_file.write_text("def temp_func_to_delete(): pass\n")

        # First init with the file
        result1, _ = repo.env.run_cpl(["init", "--force"], cwd=repo.path)
        result1.check()

        # Verify it's indexed
        matches_before = repo.query_db(
            "SELECT name FROM def_facts WHERE name = ?",
            ("temp_func_to_delete",),
        )
        assert len(matches_before) > 0, "Temp function should be indexed"

        # Delete the file
        temp_file.unlink()

        # Re-init
        result2, _ = repo.env.run_cpl(["init", "--force"], cwd=repo.path)
        result2.check()

        # Verify it's removed
        matches_after = repo.query_db(
            "SELECT name FROM def_facts WHERE name = ?",
            ("temp_func_to_delete",),
        )
        assert len(matches_after) == 0, "Temp function should be removed after re-init"
