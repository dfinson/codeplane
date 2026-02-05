"""Tests for MCP mutation tool (write_files).

Tests the actual exports:
- EditParam model
- _summarize_write() helper
- _display_write() helper

Handler tests use conftest.py fixtures for integration testing.
"""

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.mutation import (
    EditParam,
    _display_write,
    _summarize_write,
)


class TestEditParam:
    """Tests for EditParam model."""

    def test_create_action(self) -> None:
        """Create action with content."""
        edit = EditParam(path="new.py", action="create", content="print()")
        assert edit.path == "new.py"
        assert edit.action == "create"

    def test_update_action(self) -> None:
        """Update action with old_content/new_content."""
        edit = EditParam(
            path="file.py",
            action="update",
            old_content="old",
            new_content="new",
        )
        assert edit.action == "update"

    def test_delete_action(self) -> None:
        """Delete action only needs path."""
        edit = EditParam(path="remove.py", action="delete")
        assert edit.action == "delete"

    def test_invalid_action(self) -> None:
        """Invalid action is rejected."""
        with pytest.raises(ValidationError):
            EditParam(path="file.py", action="rename")

    def test_expected_occurrences_default(self) -> None:
        """expected_occurrences defaults to 1."""
        edit = EditParam(path="f.py", action="update", old_content="a", new_content="b")
        assert edit.expected_occurrences == 1


class TestSummarizeWrite:
    """Tests for _summarize_write helper.

    Note: _summarize_write takes (delta_files: list, dry_run: bool)
    """

    def test_no_changes(self) -> None:
        """No changes message."""
        result = _summarize_write([], False)
        assert result == "no changes"

    def test_with_changes(self) -> None:
        """Shows file count and deltas."""
        delta_files = [
            {"path": "a.py", "action": "updated", "insertions": 100, "deletions": 50},
            {"path": "b.py", "action": "updated", "insertions": 20, "deletions": 10},
        ]
        result = _summarize_write(delta_files, False)
        assert "2" in result  # 2 files

    def test_dry_run_prefix(self) -> None:
        """Dry run has prefix."""
        delta_files = [{"path": "a.py", "action": "created", "insertions": 10, "deletions": 0}]
        result = _summarize_write(delta_files, True)
        assert result.startswith("(dry-run)")


class TestDisplayWrite:
    """Tests for _display_write helper."""

    def test_no_files(self) -> None:
        """No files message."""
        result = _display_write([], False)
        assert result == "No changes applied."

    def test_created_files(self) -> None:
        """Shows created count."""
        files = [{"action": "created"}, {"action": "created"}]
        result = _display_write(files, False)
        assert "2 created" in result
