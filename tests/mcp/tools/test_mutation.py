"""Tests for MCP mutation tools (write_files).

Verifies EditParam, WriteFilesParams, and summary helpers.
"""

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.mutation import (
    EditParam,
    WriteFilesParams,
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

    def test_update_with_content(self) -> None:
        """Should create an update with full content."""
        edit = EditParam(
            path="file.py",
            action="update",
            content="new content",
        )
        assert edit.action == "update"

    def test_update_with_old_new(self) -> None:
        """Should create an update with old/new content."""
        edit = EditParam(
            path="file.py",
            action="update",
            old_content="old code",
            new_content="new code",
        )
        assert edit.old_content == "old code"
        assert edit.new_content == "new code"

    def test_delete_file(self) -> None:
        """Should create a delete edit."""
        edit = EditParam(
            path="file.py",
            action="delete",
        )
        assert edit.action == "delete"
        assert edit.content is None

    def test_expected_occurrences_default(self) -> None:
        """Should default to 1 occurrence."""
        edit = EditParam(
            path="file.py",
            action="update",
            old_content="x",
            new_content="y",
        )
        assert edit.expected_occurrences == 1

    def test_expected_occurrences_custom(self) -> None:
        """Should accept custom occurrence count."""
        edit = EditParam(
            path="file.py",
            action="update",
            old_content="x",
            new_content="y",
            expected_occurrences=5,
        )
        assert edit.expected_occurrences == 5

    def test_rejects_extra_fields(self) -> None:
        """Should reject unknown fields."""
        with pytest.raises(ValidationError):
            EditParam(
                path="file.py",
                action="create",
                content="test",
                unknown_field="bad",  # type: ignore
            )


class TestWriteFilesParams:
    """Tests for WriteFilesParams model."""

    def test_minimal_params(self) -> None:
        """Should accept minimal params."""
        params = WriteFilesParams(
            edits=[EditParam(path="file.py", action="create", content="test")]
        )
        assert len(params.edits) == 1
        assert params.dry_run is False

    def test_multiple_edits(self) -> None:
        """Should accept multiple edits."""
        params = WriteFilesParams(
            edits=[
                EditParam(path="a.py", action="create", content="a"),
                EditParam(path="b.py", action="create", content="b"),
                EditParam(path="c.py", action="delete"),
            ]
        )
        assert len(params.edits) == 3

    def test_dry_run(self) -> None:
        """Should accept dry_run flag."""
        params = WriteFilesParams(
            edits=[EditParam(path="file.py", action="delete")],
            dry_run=True,
        )
        assert params.dry_run is True


class TestSummarizeWrite:
    """Tests for _summarize_write helper."""

    def test_no_changes(self) -> None:
        """Should handle no changes."""
        summary = _summarize_write(0, 0, 0, False)
        assert "no changes" in summary

    def test_with_changes(self) -> None:
        """Should show file count and diff."""
        summary = _summarize_write(3, 100, 50, False)
        assert "3 files" in summary
        assert "+100" in summary
        assert "-50" in summary

    def test_dry_run_prefix(self) -> None:
        """Should prefix with (dry-run)."""
        summary = _summarize_write(1, 10, 5, True)
        assert "dry-run" in summary.lower()


class TestDisplayWrite:
    """Tests for _display_write helper."""

    def test_no_files(self) -> None:
        """Should handle no files."""
        display = _display_write([], False)
        assert "No changes" in display

    def test_no_files_dry_run(self) -> None:
        """Should handle dry run with no files."""
        display = _display_write([], True)
        assert "Dry run" in display

    def test_with_created_files(self) -> None:
        """Should count created files."""
        files = [{"action": "created"}, {"action": "created"}]
        display = _display_write(files, False)
        assert "2 created" in display

    def test_with_mixed_actions(self) -> None:
        """Should count all action types."""
        files = [
            {"action": "created"},
            {"action": "updated"},
            {"action": "updated"},
            {"action": "deleted"},
        ]
        display = _display_write(files, False)
        assert "1 created" in display
        assert "2 updated" in display
        assert "1 deleted" in display

    def test_dry_run_prefix(self) -> None:
        """Should prefix with 'Dry run: would have'."""
        files = [{"action": "created"}]
        display = _display_write(files, True)
        assert "Dry run: would have" in display
