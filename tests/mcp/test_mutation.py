"""Tests for MCP mutation tool (write_files)."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.mutation import (
    EditParam,
    WriteFilesParams,
    write_files,
)


class TestEditParam:
    """Tests for EditParam model."""

    def test_create_action(self):
        """Create action with content."""
        edit = EditParam(path="new.py", action="create", content="print('hello')")
        assert edit.path == "new.py"
        assert edit.action == "create"
        assert edit.content == "print('hello')"

    def test_update_action_with_old_new(self):
        """Update action with old_content/new_content."""
        edit = EditParam(
            path="file.py",
            action="update",
            old_content="old text",
            new_content="new text",
        )
        assert edit.action == "update"
        assert edit.old_content == "old text"
        assert edit.new_content == "new text"

    def test_delete_action(self):
        """Delete action only needs path."""
        edit = EditParam(path="remove.py", action="delete")
        assert edit.action == "delete"

    def test_invalid_action(self):
        """Invalid action is rejected."""
        with pytest.raises(ValidationError):
            EditParam(path="file.py", action="rename")

    def test_expected_occurrences_default(self):
        """expected_occurrences defaults to 1."""
        edit = EditParam(path="f.py", action="update", old_content="a", new_content="b")
        assert edit.expected_occurrences == 1

    def test_expected_occurrences_custom(self):
        """expected_occurrences can be customized."""
        edit = EditParam(
            path="f.py",
            action="update",
            old_content="TODO",
            new_content="DONE",
            expected_occurrences=5,
        )
        assert edit.expected_occurrences == 5

    def test_expected_occurrences_minimum(self):
        """expected_occurrences must be >= 1."""
        with pytest.raises(ValidationError):
            EditParam(
                path="f.py",
                action="update",
                old_content="a",
                new_content="b",
                expected_occurrences=0,
            )


class TestWriteFilesParams:
    """Tests for WriteFilesParams model."""

    def test_edits_required(self):
        """edits is required."""
        with pytest.raises(ValidationError):
            WriteFilesParams()

    def test_single_edit(self):
        """Single edit in list."""
        params = WriteFilesParams(
            edits=[EditParam(path="new.py", action="create", content="# new")]
        )
        assert len(params.edits) == 1

    def test_multiple_edits(self):
        """Multiple edits in list."""
        params = WriteFilesParams(
            edits=[
                EditParam(path="a.py", action="create", content="a"),
                EditParam(path="b.py", action="update", old_content="x", new_content="y"),
                EditParam(path="c.py", action="delete"),
            ]
        )
        assert len(params.edits) == 3

    def test_dry_run_default(self):
        """dry_run defaults to False."""
        params = WriteFilesParams(edits=[EditParam(path="f.py", action="create", content="x")])
        assert params.dry_run is False

    def test_dry_run_true(self):
        """dry_run can be set True."""
        params = WriteFilesParams(
            edits=[EditParam(path="f.py", action="create", content="x")],
            dry_run=True,
        )
        assert params.dry_run is True


class TestWriteFilesHandler:
    """Tests for write_files handler."""

    @pytest.mark.asyncio
    async def test_create_file(self, mock_context: MagicMock):
        """Creates a new file."""
        params = WriteFilesParams(
            edits=[EditParam(path="new.py", action="create", content="print('new')")]
        )
        result = await write_files(mock_context, params)

        mock_context.mutation_ops.atomic_edit_files.assert_called_once()
        assert "applied" in result

    @pytest.mark.asyncio
    async def test_update_file(self, mock_context: MagicMock):
        """Updates existing file."""
        params = WriteFilesParams(
            edits=[
                EditParam(
                    path="existing.py",
                    action="update",
                    old_content="old",
                    new_content="new",
                )
            ]
        )
        await write_files(mock_context, params)

        mock_context.mutation_ops.atomic_edit_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_file(self, mock_context: MagicMock):
        """Deletes a file."""
        params = WriteFilesParams(edits=[EditParam(path="remove.py", action="delete")])
        await write_files(mock_context, params)

        mock_context.mutation_ops.atomic_edit_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_dry_run(self, mock_context: MagicMock):
        """Dry run doesn't apply changes."""
        mock_context.mutation_ops.atomic_edit_files.return_value = MagicMock(
            applied=False,
            dry_run=True,
            delta=MagicMock(
                mutation_id="mut_dry",
                files_changed=1,
                insertions=1,
                deletions=0,
                files=[],
            ),
            dry_run_info=MagicMock(content_hash="abc123"),
        )

        params = WriteFilesParams(
            edits=[EditParam(path="f.py", action="create", content="x")],
            dry_run=True,
        )
        result = await write_files(mock_context, params)

        assert result["dry_run"] is True
        assert result["applied"] is False

    @pytest.mark.asyncio
    async def test_multiple_edits_atomic(self, mock_context: MagicMock):
        """Multiple edits are applied atomically."""
        params = WriteFilesParams(
            edits=[
                EditParam(path="a.py", action="create", content="a"),
                EditParam(path="b.py", action="create", content="b"),
            ]
        )
        await write_files(mock_context, params)

        # Should be single call for atomic operation
        mock_context.mutation_ops.atomic_edit_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_result_includes_delta(self, mock_context: MagicMock):
        """Result includes delta information."""
        mock_context.mutation_ops.atomic_edit_files.return_value = MagicMock(
            applied=True,
            dry_run=False,
            delta=MagicMock(
                mutation_id="mut_123",
                files_changed=2,
                insertions=10,
                deletions=5,
                files=[
                    MagicMock(
                        path="a.py",
                        action="created",
                        old_hash=None,
                        new_hash="abc",
                        insertions=5,
                        deletions=0,
                    ),
                    MagicMock(
                        path="b.py",
                        action="updated",
                        old_hash="old",
                        new_hash="new",
                        insertions=5,
                        deletions=5,
                    ),
                ],
            ),
            dry_run_info=None,
        )

        params = WriteFilesParams(
            edits=[
                EditParam(path="a.py", action="create", content="x"),
                EditParam(path="b.py", action="update", old_content="y", new_content="z"),
            ]
        )
        result = await write_files(mock_context, params)

        assert "delta" in result
        assert result["delta"]["files_changed"] == 2
