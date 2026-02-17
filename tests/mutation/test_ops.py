"""Tests for mutation operations - write_files tool.

Covers:
- Create/update/delete operations
- Dry run mode
- Reindex callback
- Delta computation
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from codeplane.mutation.ops import (
    Edit,
    FileDelta,
    MutationDelta,
    MutationOps,
    MutationResult,
    _hash_content,
)

if TYPE_CHECKING:
    pass


class TestHashContent:
    """Tests for _hash_content function."""

    def test_hash_returns_12_chars(self) -> None:
        """Hash should be 12 character hex string."""
        result = _hash_content("hello world")
        assert len(result) == 12
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_is_deterministic(self) -> None:
        """Same content produces same hash."""
        assert _hash_content("test") == _hash_content("test")

    def test_hash_different_for_different_content(self) -> None:
        """Different content produces different hash."""
        assert _hash_content("a") != _hash_content("b")

    def test_hash_empty_string(self) -> None:
        """Empty string has valid hash."""
        result = _hash_content("")
        assert len(result) == 12

    def test_hash_unicode(self) -> None:
        """Unicode content hashes correctly."""
        result = _hash_content("héllo wörld 🎉")
        assert len(result) == 12


class TestEdit:
    """Tests for Edit dataclass."""

    def test_create_action(self) -> None:
        """Edit with create action."""
        edit = Edit(path="test.py", action="create", content="print('hello')")
        assert edit.path == "test.py"
        assert edit.action == "create"
        assert edit.content == "print('hello')"

    def test_update_action_full_content(self) -> None:
        """Edit with update action and full content."""
        edit = Edit(path="test.py", action="update", content="new content")
        assert edit.action == "update"
        assert edit.content == "new content"

    def test_delete_action(self) -> None:
        """Edit with delete action."""
        edit = Edit(path="test.py", action="delete")
        assert edit.action == "delete"
        assert edit.content is None


class TestFileDelta:
    """Tests for FileDelta dataclass."""

    def test_created_delta(self) -> None:
        """FileDelta for created file."""
        delta = FileDelta(
            path="new.py",
            action="created",
            old_hash=None,
            new_hash="abc123def456",
            insertions=10,
            deletions=0,
        )
        assert delta.action == "created"
        assert delta.old_hash is None
        assert delta.insertions == 10

    def test_updated_delta(self) -> None:
        """FileDelta for updated file."""
        delta = FileDelta(
            path="existing.py",
            action="updated",
            old_hash="aaa111",
            new_hash="bbb222",
            insertions=5,
            deletions=3,
        )
        assert delta.action == "updated"
        assert delta.old_hash is not None
        assert delta.new_hash is not None

    def test_deleted_delta(self) -> None:
        """FileDelta for deleted file."""
        delta = FileDelta(
            path="removed.py",
            action="deleted",
            old_hash="ccc333",
            new_hash=None,
            insertions=0,
            deletions=25,
        )
        assert delta.action == "deleted"
        assert delta.new_hash is None
        assert delta.deletions == 25


class TestMutationDelta:
    """Tests for MutationDelta dataclass."""

    def test_empty_delta(self) -> None:
        """Empty mutation delta."""
        delta = MutationDelta(
            mutation_id="test123",
            files_changed=0,
            insertions=0,
            deletions=0,
        )
        assert delta.files == []
        assert delta.files_changed == 0

    def test_delta_with_files(self) -> None:
        """Mutation delta with file list."""
        file_delta = FileDelta(path="a.py", action="created", insertions=10, deletions=0)
        delta = MutationDelta(
            mutation_id="mut001",
            files_changed=1,
            insertions=10,
            deletions=0,
            files=[file_delta],
        )
        assert len(delta.files) == 1
        assert delta.files[0].path == "a.py"


class TestMutationResult:
    """Tests for MutationResult dataclass."""

    def test_applied_result(self) -> None:
        """Result when changes are applied."""
        delta = MutationDelta(mutation_id="abc", files_changed=1, insertions=5, deletions=0)
        result = MutationResult(applied=True, dry_run=False, delta=delta)
        assert result.applied is True
        assert result.dry_run is False

    def test_dry_run_result(self) -> None:
        """Result in dry run mode."""
        delta = MutationDelta(mutation_id="xyz", files_changed=1, insertions=0, deletions=0)
        result = MutationResult(
            applied=False,
            dry_run=True,
            delta=delta,
        )
        assert result.applied is False
        assert result.dry_run is True


class TestMutationOpsCreate:
    """Tests for MutationOps create action."""

    def test_create_new_file(self, tmp_path: Path) -> None:
        """Create a new file."""
        ops = MutationOps(tmp_path)
        content = "print('hello')\n"
        result = ops.write_files([Edit(path="new.py", action="create", content=content)])

        assert result.applied is True
        assert result.dry_run is False
        assert result.delta.files_changed == 1
        assert (tmp_path / "new.py").read_text() == content

    def test_create_file_in_nested_directory(self, tmp_path: Path) -> None:
        """Create file creates parent directories."""
        ops = MutationOps(tmp_path)
        result = ops.write_files([Edit(path="a/b/c/deep.py", action="create", content="# deep")])

        assert result.applied is True
        assert (tmp_path / "a/b/c/deep.py").exists()

    def test_create_existing_file_raises(self, tmp_path: Path) -> None:
        """Create raises FileExistsError if file exists."""
        (tmp_path / "existing.py").write_text("# existing")
        ops = MutationOps(tmp_path)

        with pytest.raises(FileExistsError) as exc_info:
            ops.write_files([Edit(path="existing.py", action="create", content="new")])

        assert "existing.py" in str(exc_info.value)

    def test_create_empty_file(self, tmp_path: Path) -> None:
        """Create with empty content."""
        ops = MutationOps(tmp_path)
        result = ops.write_files([Edit(path="empty.py", action="create")])

        assert (tmp_path / "empty.py").read_text() == ""
        assert result.delta.files[0].insertions == 1  # Empty file = 1 line

    def test_create_file_delta_structure(self, tmp_path: Path) -> None:
        """Create produces correct delta."""
        ops = MutationOps(tmp_path)
        content = "line1\nline2\nline3"
        result = ops.write_files([Edit(path="test.py", action="create", content=content)])

        delta = result.delta.files[0]
        assert delta.path == "test.py"
        assert delta.action == "created"
        assert delta.old_hash is None
        assert delta.new_hash is not None
        assert delta.insertions == 3
        assert delta.deletions == 0


class TestMutationOpsDelete:
    """Tests for MutationOps delete action."""

    def test_delete_existing_file(self, tmp_path: Path) -> None:
        """Delete removes existing file."""
        test_file = tmp_path / "to_delete.py"
        test_file.write_text("# will be deleted")

        ops = MutationOps(tmp_path)
        result = ops.write_files([Edit(path="to_delete.py", action="delete")])

        assert result.applied is True
        assert not test_file.exists()

    def test_delete_nonexistent_file_raises(self, tmp_path: Path) -> None:
        """Delete raises FileNotFoundError if file doesn't exist."""
        ops = MutationOps(tmp_path)

        with pytest.raises(FileNotFoundError) as exc_info:
            ops.write_files([Edit(path="ghost.py", action="delete")])

        assert "ghost.py" in str(exc_info.value)

    def test_delete_delta_structure(self, tmp_path: Path) -> None:
        """Delete produces correct delta."""
        test_file = tmp_path / "del.py"
        test_file.write_text("line1\nline2\nline3\nline4")

        ops = MutationOps(tmp_path)
        result = ops.write_files([Edit(path="del.py", action="delete")])

        delta = result.delta.files[0]
        assert delta.action == "deleted"
        assert delta.old_hash is not None
        assert delta.new_hash is None
        assert delta.insertions == 0
        assert delta.deletions == 4


class TestMutationOpsUpdateFullContent:
    """Tests for MutationOps update action with full content."""

    def test_update_full_content(self, tmp_path: Path) -> None:
        """Update replaces entire file content."""
        test_file = tmp_path / "test.py"
        test_file.write_text("old content")

        ops = MutationOps(tmp_path)
        result = ops.write_files([Edit(path="test.py", action="update", content="new content")])

        assert test_file.read_text() == "new content"
        assert result.applied is True

    def test_update_nonexistent_file_raises(self, tmp_path: Path) -> None:
        """Update raises FileNotFoundError if file doesn't exist."""
        ops = MutationOps(tmp_path)

        with pytest.raises(FileNotFoundError) as exc_info:
            ops.write_files([Edit(path="missing.py", action="update", content="x")])

        assert "missing.py" in str(exc_info.value)

    def test_update_delta_structure(self, tmp_path: Path) -> None:
        """Update produces correct delta."""
        test_file = tmp_path / "test.py"
        test_file.write_text("a\nb")

        ops = MutationOps(tmp_path)
        result = ops.write_files([Edit(path="test.py", action="update", content="x\ny\nz")])

        delta = result.delta.files[0]
        assert delta.action == "updated"
        assert delta.old_hash is not None
        assert delta.new_hash is not None
        # 2 lines -> 3 lines = 1 insertion
        assert delta.insertions == 1
        assert delta.deletions == 0


class TestMutationOpsDryRun:
    """Tests for MutationOps dry run mode."""

    def test_dry_run_does_not_create(self, tmp_path: Path) -> None:
        """Dry run doesn't create file."""
        ops = MutationOps(tmp_path)
        result = ops.write_files(
            [Edit(path="new.py", action="create", content="x")],
            dry_run=True,
        )

        assert result.applied is False
        assert result.dry_run is True
        assert not (tmp_path / "new.py").exists()

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        """Dry run doesn't delete file."""
        test_file = tmp_path / "keep.py"
        test_file.write_text("keep me")

        ops = MutationOps(tmp_path)
        result = ops.write_files(
            [Edit(path="keep.py", action="delete")],
            dry_run=True,
        )

        assert result.applied is False
        assert test_file.exists()

    def test_dry_run_does_not_update(self, tmp_path: Path) -> None:
        """Dry run doesn't update file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("original")

        ops = MutationOps(tmp_path)
        ops.write_files(
            [Edit(path="test.py", action="update", content="modified")],
            dry_run=True,
        )

        assert test_file.read_text() == "original"

    def test_dry_run_returns_delta(self, tmp_path: Path) -> None:
        """Dry run still computes delta."""
        ops = MutationOps(tmp_path)
        result = ops.write_files(
            [Edit(path="new.py", action="create", content="a\nb\nc")],
            dry_run=True,
        )

        assert result.delta.files_changed == 1
        assert result.delta.files[0].insertions == 3


class TestMutationOpsCallback:
    """Tests for MutationOps reindex callback."""

    def test_callback_invoked_on_mutation(self, tmp_path: Path) -> None:
        """on_mutation callback is invoked with changed paths."""
        callback = MagicMock()
        ops = MutationOps(tmp_path, on_mutation=callback)

        ops.write_files([Edit(path="new.py", action="create", content="x")])

        callback.assert_called_once()
        paths = callback.call_args[0][0]
        assert len(paths) == 1
        assert paths[0] == tmp_path / "new.py"

    def test_callback_not_invoked_on_dry_run(self, tmp_path: Path) -> None:
        """on_mutation callback not invoked during dry run."""
        callback = MagicMock()
        ops = MutationOps(tmp_path, on_mutation=callback)

        ops.write_files(
            [Edit(path="new.py", action="create", content="x")],
            dry_run=True,
        )

        callback.assert_not_called()

    def test_callback_receives_multiple_paths(self, tmp_path: Path) -> None:
        """on_mutation callback receives all changed paths."""
        callback = MagicMock()
        ops = MutationOps(tmp_path)
        ops._on_mutation = callback

        (tmp_path / "existing.py").write_text("x")

        ops.write_files(
            [
                Edit(path="a.py", action="create", content="a"),
                Edit(path="b.py", action="create", content="b"),
                Edit(path="existing.py", action="delete"),
            ]
        )

        callback.assert_called_once()
        paths = callback.call_args[0][0]
        assert len(paths) == 3

    def test_no_callback_if_not_set(self, tmp_path: Path) -> None:
        """Works without callback."""
        ops = MutationOps(tmp_path)  # No callback
        result = ops.write_files([Edit(path="test.py", action="create", content="x")])
        assert result.applied is True


class TestMutationOpsMultipleEdits:
    """Tests for MutationOps with multiple edits."""

    def test_multiple_creates(self, tmp_path: Path) -> None:
        """Multiple create operations."""
        ops = MutationOps(tmp_path)
        result = ops.write_files(
            [
                Edit(path="a.py", action="create", content="# a"),
                Edit(path="b.py", action="create", content="# b"),
                Edit(path="c.py", action="create", content="# c"),
            ]
        )

        assert result.delta.files_changed == 3
        assert (tmp_path / "a.py").exists()
        assert (tmp_path / "b.py").exists()
        assert (tmp_path / "c.py").exists()

    def test_mixed_operations(self, tmp_path: Path) -> None:
        """Mix of create, update, delete."""
        (tmp_path / "update_me.py").write_text("old")
        (tmp_path / "delete_me.py").write_text("bye")

        ops = MutationOps(tmp_path)
        result = ops.write_files(
            [
                Edit(path="new.py", action="create", content="created"),
                Edit(path="update_me.py", action="update", content="updated"),
                Edit(path="delete_me.py", action="delete"),
            ]
        )

        assert result.delta.files_changed == 3
        assert (tmp_path / "new.py").read_text() == "created"
        assert (tmp_path / "update_me.py").read_text() == "updated"
        assert not (tmp_path / "delete_me.py").exists()

    def test_validation_before_any_changes(self, tmp_path: Path) -> None:
        """Validation errors raised before any changes applied."""
        (tmp_path / "good.py").write_text("x")

        ops = MutationOps(tmp_path)

        with pytest.raises(FileNotFoundError):
            ops.write_files(
                [
                    Edit(path="good.py", action="update", content="y"),
                    Edit(path="missing.py", action="update", content="z"),  # Should fail
                ]
            )

        # First file should NOT have been changed
        assert (tmp_path / "good.py").read_text() == "x"

    def test_delta_aggregates_stats(self, tmp_path: Path) -> None:
        """Delta aggregates insertions/deletions."""
        (tmp_path / "a.py").write_text("1\n2\n3")  # 3 lines
        (tmp_path / "b.py").write_text("x")  # 1 line

        ops = MutationOps(tmp_path)
        result = ops.write_files(
            [
                Edit(path="new.py", action="create", content="a\nb\nc\nd\ne"),  # +5 lines
                Edit(path="a.py", action="delete"),  # -3 lines
                Edit(path="b.py", action="update", content="y\nz"),  # +1 line (1->2)
            ]
        )

        # Total: 5 + 1 insertions, 3 deletions
        assert result.delta.insertions >= 5
        assert result.delta.deletions >= 3


class TestMutationOpsMutationId:
    """Tests for mutation ID generation."""

    def test_mutation_id_is_8_chars(self, tmp_path: Path) -> None:
        """Mutation ID is 8 character UUID prefix."""
        ops = MutationOps(tmp_path)
        result = ops.write_files([Edit(path="test.py", action="create", content="x")])
        assert len(result.delta.mutation_id) == 8

    def test_mutation_ids_are_unique(self, tmp_path: Path) -> None:
        """Each mutation gets unique ID."""
        ops = MutationOps(tmp_path)

        result1 = ops.write_files([Edit(path="a.py", action="create", content="a")])
        result2 = ops.write_files([Edit(path="b.py", action="create", content="b")])

        assert result1.delta.mutation_id != result2.delta.mutation_id
