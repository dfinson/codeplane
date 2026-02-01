"""E2E Test Scenario 2: Incremental Update.

Validates incremental indexing behavior per E2E_TEST_PROPOSALS.md.

Incremental reindexing happens automatically via the daemon/watcher.
These tests validate reinit behavior for file additions/deletions.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import InitResult


@pytest.mark.e2e
@pytest.mark.slow
class TestIncrementalUpdate:
    """Scenario 2: Incremental Update."""

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
