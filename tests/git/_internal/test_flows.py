"""Tests for git/_internal/flows.py module.

Covers:
- ConflictCheckResult dataclass
- WriteFlows class
- Transactional patterns for git operations
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codeplane.git._internal.flows import ConflictCheckResult, WriteFlows


class TestConflictCheckResult:
    """Tests for ConflictCheckResult dataclass."""

    def test_create_no_conflicts(self) -> None:
        """Create result with no conflicts."""
        result = ConflictCheckResult(has_conflicts=False, conflict_paths=())
        assert result.has_conflicts is False
        assert result.conflict_paths == ()

    def test_create_with_conflicts(self) -> None:
        """Create result with conflicts."""
        result = ConflictCheckResult(
            has_conflicts=True,
            conflict_paths=("file1.py", "file2.py"),
        )
        assert result.has_conflicts is True
        assert result.conflict_paths == ("file1.py", "file2.py")

    def test_is_frozen(self) -> None:
        """Result is immutable."""
        result = ConflictCheckResult(has_conflicts=False, conflict_paths=())
        with pytest.raises(AttributeError):
            result.has_conflicts = True  # type: ignore[misc]

    def test_paths_are_tuple(self) -> None:
        """Conflict paths are stored as tuple."""
        result = ConflictCheckResult(
            has_conflicts=True,
            conflict_paths=("a.py", "b.py"),
        )
        assert isinstance(result.conflict_paths, tuple)


class TestWriteFlows:
    """Tests for WriteFlows class."""

    @pytest.fixture
    def mock_access(self) -> MagicMock:
        """Create mock RepoAccess."""
        access = MagicMock()
        access.index = MagicMock()
        access.index.conflicts = None
        access.default_signature = MagicMock()
        access.is_unborn = False
        access.head_target = MagicMock()
        return access

    @pytest.fixture
    def flows(self, mock_access: MagicMock) -> WriteFlows:
        """Create WriteFlows instance."""
        return WriteFlows(mock_access)

    def test_extract_conflict_paths_empty(self, flows: WriteFlows, mock_access: MagicMock) -> None:
        """Returns empty tuple when no conflicts."""
        mock_access.index.conflicts = None
        result = flows.extract_conflict_paths()
        assert result == ()

    def test_extract_conflict_paths_with_conflicts(
        self, flows: WriteFlows, mock_access: MagicMock
    ) -> None:
        """Extracts unique paths from conflict entries."""
        # Mock conflict structure: (ancestor, ours, theirs) tuples
        entry1 = MagicMock(path="file1.py")
        entry2 = MagicMock(path="file2.py")
        entry3 = MagicMock(path="file1.py")  # Duplicate

        mock_access.index.conflicts = [
            (entry1, entry2, None),
            (None, entry3, entry2),
        ]

        result = flows.extract_conflict_paths()
        assert set(result) == {"file1.py", "file2.py"}
        assert isinstance(result, tuple)

    def test_check_conflicts_no_conflicts(self, flows: WriteFlows, mock_access: MagicMock) -> None:
        """Returns no conflicts when index is clean."""
        mock_access.index.conflicts = None
        result = flows.check_conflicts()
        assert result.has_conflicts is False
        assert result.conflict_paths == ()

    def test_check_conflicts_with_conflicts(
        self, flows: WriteFlows, mock_access: MagicMock
    ) -> None:
        """Returns conflicts when index has them."""
        entry = MagicMock(path="conflict.py")
        mock_access.index.conflicts = [(entry, entry, entry)]

        result = flows.check_conflicts()
        assert result.has_conflicts is True
        assert "conflict.py" in result.conflict_paths

    def test_write_tree_and_commit(self, flows: WriteFlows, mock_access: MagicMock) -> None:
        """Creates commit from index tree."""
        mock_access.index.write_tree.return_value = "tree-oid"
        mock_access.create_commit.return_value = "commit-oid"

        sha = flows.write_tree_and_commit("test message", [])

        mock_access.index.write_tree.assert_called_once()
        mock_access.create_commit.assert_called_once()
        assert sha == "commit-oid"

    def test_write_tree_and_commit_with_author(
        self, flows: WriteFlows, mock_access: MagicMock
    ) -> None:
        """Uses provided author signature."""
        custom_author = MagicMock()
        mock_access.index.write_tree.return_value = "tree-oid"
        mock_access.create_commit.return_value = "commit-oid"

        flows.write_tree_and_commit("message", [], author=custom_author)

        # Verify custom author was used (second arg to create_commit)
        call_args = mock_access.create_commit.call_args
        assert call_args[0][1] == custom_author

    def test_commit_from_index_unborn(self, flows: WriteFlows, mock_access: MagicMock) -> None:
        """Creates initial commit with no parents."""
        mock_access.is_unborn = True
        mock_access.index.write_tree.return_value = "tree-oid"
        mock_access.create_commit.return_value = "commit-oid"

        sha = flows.commit_from_index("initial commit")

        # Should have empty parents list for initial commit
        # create_commit args: ref, author, committer, message, tree_id, parents
        call_args = mock_access.create_commit.call_args
        assert call_args[0][5] == []  # parents argument
        assert sha == "commit-oid"

    def test_commit_from_index_with_head(self, flows: WriteFlows, mock_access: MagicMock) -> None:
        """Creates commit with HEAD as parent."""
        mock_access.is_unborn = False
        mock_access.head_target = "head-oid"
        mock_access.index.write_tree.return_value = "tree-oid"
        mock_access.create_commit.return_value = "commit-oid"

        sha = flows.commit_from_index("subsequent commit")

        # Should have HEAD as parent
        # create_commit args: ref, author, committer, message, tree_id, parents
        call_args = mock_access.create_commit.call_args
        assert call_args[0][5] == ["head-oid"]
        assert sha == "commit-oid"

    def test_stateful_op_cleanup_on_success(
        self, flows: WriteFlows, mock_access: MagicMock
    ) -> None:
        """Calls state_cleanup after successful operation."""
        with flows.stateful_op():
            pass  # Successful operation

        mock_access.state_cleanup.assert_called_once()

    def test_stateful_op_cleanup_on_exception(
        self, flows: WriteFlows, mock_access: MagicMock
    ) -> None:
        """Calls state_cleanup even when operation raises."""
        with pytest.raises(ValueError), flows.stateful_op():
            raise ValueError("test error")

        mock_access.state_cleanup.assert_called_once()

    def test_run_merge_like_operation_success(
        self, flows: WriteFlows, mock_access: MagicMock
    ) -> None:
        """Returns success when no conflicts."""
        mock_access.index.conflicts = None
        mock_access.index.write_tree.return_value = "tree-oid"
        mock_access.create_commit.return_value = "commit-oid"

        operation = MagicMock()
        success, sha, conflicts = flows.run_merge_like_operation(operation, "merge commit", [])

        assert success is True
        assert sha == "commit-oid"
        assert conflicts == ()
        operation.assert_called_once()

    def test_run_merge_like_operation_with_conflicts(
        self, flows: WriteFlows, mock_access: MagicMock
    ) -> None:
        """Returns failure when conflicts exist."""
        entry = MagicMock(path="conflict.py")
        mock_access.index.conflicts = [(entry, entry, entry)]

        operation = MagicMock()
        success, sha, conflicts = flows.run_merge_like_operation(operation, "merge commit", [])

        assert success is False
        assert sha is None
        assert "conflict.py" in conflicts
        operation.assert_called_once()

    def test_run_merge_like_operation_cleanup_always(
        self, flows: WriteFlows, mock_access: MagicMock
    ) -> None:
        """Always cleans up state after operation."""
        mock_access.index.conflicts = None
        mock_access.index.write_tree.return_value = "tree-oid"
        mock_access.create_commit.return_value = "commit-oid"

        flows.run_merge_like_operation(MagicMock(), "msg", [])
        mock_access.state_cleanup.assert_called()
