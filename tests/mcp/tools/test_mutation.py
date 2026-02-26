"""Tests for mutation helpers (EditParam model).

Verifies EditParam validation logic.
"""

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.mutation import EditParam


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
            expected_content="old content",
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
