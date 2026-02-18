"""Tests for MCP mutation tools (write_source).

Verifies EditParam model and summary helpers.
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

    def test_create_file(self) -> None:
        """Should create a create edit."""
        edit = EditParam(
            path="new_file.py",
            action="create",
            content="print('hello')",
        )
        assert edit.path == "new_file.py"
        assert edit.action == "create"
        assert edit.content is not None

    def test_update_with_span_fields(self) -> None:
        """Should create an update with span-based edit fields."""
        edit = EditParam(
            path="file.py",
            action="update",
            start_line=1,
            end_line=5,
            expected_file_sha256="a" * 64,
            new_content="new content",
        )
        assert edit.action == "update"
        assert edit.start_line == 1
        assert edit.end_line == 5

    def test_update_rejects_old_content(self) -> None:
        """Update no longer accepts old_content (span-only mode)."""
        with pytest.raises(ValidationError):
            EditParam(
                path="file.py",
                action="update",
                old_content="old",  # type: ignore[call-arg]
                new_content="new",
            )

    def test_delete(self) -> None:
        """Should create delete edit."""
        edit = EditParam(path="file.py", action="delete")
        assert edit.action == "delete"

    def test_invalid_action(self) -> None:
        """Should reject invalid action."""
        with pytest.raises(ValidationError):
            EditParam(path="file.py", action="invalid")

    def test_extra_fields_forbidden(self) -> None:
        """Extra fields are rejected."""
        with pytest.raises(ValidationError):
            EditParam(path="file.py", action="create", content="x", extra="bad")  # type: ignore

    def test_update_missing_span_fields(self) -> None:
        """Update without all span fields is rejected."""
        with pytest.raises(ValidationError):
            EditParam(
                path="file.py",
                action="update",
                new_content="y",
            )


class TestSummarizeWrite:
    """Tests for _summarize_write helper."""

    def test_no_changes(self) -> None:
        """No changes."""
        result = _summarize_write([], dry_run=False)
        assert "no changes" in result

    def test_no_changes_dry_run(self) -> None:
        """No changes with dry run."""
        result = _summarize_write([], dry_run=True)
        assert "(dry-run)" in result
        assert "no changes" in result

    def test_single_file_created(self) -> None:
        """Single file created."""
        files = [{"path": "new.py", "action": "created"}]
        result = _summarize_write(files, dry_run=False)
        assert "created" in result

    def test_single_file_updated(self) -> None:
        """Single file updated."""
        files = [{"path": "src/main.py", "action": "updated"}]
        result = _summarize_write(files, dry_run=False)
        assert "updated" in result

    def test_single_file_deleted(self) -> None:
        """Single file deleted."""
        files = [{"path": "old.py", "action": "deleted"}]
        result = _summarize_write(files, dry_run=False)
        assert "deleted" in result

    def test_multiple_actions(self) -> None:
        """Multiple files with different actions."""
        files = [
            {"path": "new.py", "action": "created"},
            {"path": "main.py", "action": "updated"},
            {"path": "old.py", "action": "deleted"},
        ]
        result = _summarize_write(files, dry_run=False)
        assert "1 created" in result
        assert "1 updated" in result
        assert "1 deleted" in result

    def test_dry_run_prefix(self) -> None:
        """Dry run shows prefix."""
        files = [{"path": "test.py", "action": "created"}]
        result = _summarize_write(files, dry_run=True)
        assert "(dry-run)" in result


class TestDisplayWrite:
    """Tests for _display_write helper."""

    def test_no_changes(self) -> None:
        """No changes message."""
        result = _display_write([], dry_run=False)
        assert "no changes" in result.lower()

    def test_no_changes_dry_run(self) -> None:
        """Dry run no changes."""
        result = _display_write([], dry_run=True)
        assert "dry run" in result.lower()

    def test_with_created(self) -> None:
        """Created files message."""
        files = [{"path": "new.py", "action": "created"}]
        result = _display_write(files, dry_run=False)
        assert "1 created" in result

    def test_with_multiple_actions(self) -> None:
        """Multiple actions in message."""
        files = [
            {"path": "a.py", "action": "created"},
            {"path": "b.py", "action": "updated"},
        ]
        result = _display_write(files, dry_run=False)
        assert "1 created" in result
        assert "1 updated" in result

    def test_dry_run_wording(self) -> None:
        """Dry run uses 'would have' wording."""
        files = [{"path": "test.py", "action": "created"}]
        result = _display_write(files, dry_run=True)
        assert "would" in result.lower()
