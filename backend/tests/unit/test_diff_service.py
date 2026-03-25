"""Tests for DiffService — diff parsing, throttle, and event publishing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.models.api_schemas import DiffFileStatus, DiffLineType
from backend.services.diff_service import DiffService


@pytest.fixture
def mock_git() -> AsyncMock:
    git = AsyncMock()
    # Default: no merge in-progress so existing tests use the normal diff path.
    git.is_merge_in_progress.return_value = False
    return git


@pytest.fixture
def mock_event_bus() -> AsyncMock:
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def diff_service(mock_git: AsyncMock, mock_event_bus: AsyncMock) -> DiffService:
    return DiffService(git_service=mock_git, event_bus=mock_event_bus)


# --- Parse unified diff ---

SIMPLE_DIFF = """\
diff --git a/hello.py b/hello.py
index abc1234..def5678 100644
--- a/hello.py
+++ b/hello.py
@@ -1,3 +1,4 @@
 import sys
+import os
\x20
 def main():
"""


class TestParseUnifiedDiff:
    def test_simple_modification(self, diff_service: DiffService) -> None:
        result = DiffService._parse_unified_diff(SIMPLE_DIFF)
        assert len(result) == 1
        f = result[0]
        assert f.path == "hello.py"
        assert f.status == DiffFileStatus.modified
        assert f.additions == 1
        assert f.deletions == 0
        assert len(f.hunks) == 1
        hunk = f.hunks[0]
        assert hunk.old_start == 1
        assert hunk.new_start == 1
        # Check line types
        types = [dl.type for dl in hunk.lines]
        assert DiffLineType.addition in types
        assert DiffLineType.context in types

    def test_new_file(self) -> None:
        diff = """\
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
"""
        result = DiffService._parse_unified_diff(diff)
        assert len(result) == 1
        assert result[0].status == DiffFileStatus.added
        assert result[0].additions == 2

    def test_deleted_file(self) -> None:
        diff = """\
diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-goodbye
-world
"""
        result = DiffService._parse_unified_diff(diff)
        assert len(result) == 1
        assert result[0].status == DiffFileStatus.deleted
        assert result[0].path == "old.txt"
        assert result[0].deletions == 2

    def test_renamed_file(self) -> None:
        diff = """\
diff --git a/old_name.py b/new_name.py
similarity index 100%
rename from old_name.py
rename to new_name.py
"""
        result = DiffService._parse_unified_diff(diff)
        assert len(result) == 1
        assert result[0].status == DiffFileStatus.renamed

    def test_multiple_files(self) -> None:
        diff = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old
+new
diff --git a/b.py b/b.py
new file mode 100644
--- /dev/null
+++ b/b.py
@@ -0,0 +1 @@
+content
"""
        result = DiffService._parse_unified_diff(diff)
        assert len(result) == 2
        assert result[0].path == "a.py"
        assert result[1].path == "b.py"

    def test_empty_diff(self) -> None:
        result = DiffService._parse_unified_diff("")
        assert result == []

    def test_no_newline_marker(self) -> None:
        diff = """\
diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +1 @@
-old
\\ No newline at end of file
+new
\\ No newline at end of file
"""
        result = DiffService._parse_unified_diff(diff)
        assert len(result) == 1
        assert result[0].additions == 1
        assert result[0].deletions == 1

    def test_multiple_hunks(self) -> None:
        diff = """\
diff --git a/big.py b/big.py
--- a/big.py
+++ b/big.py
@@ -1,3 +1,3 @@
 line1
-old2
+new2
 line3
@@ -10,3 +10,3 @@
 line10
-old11
+new11
 line12
"""
        result = DiffService._parse_unified_diff(diff)
        assert len(result) == 1
        assert len(result[0].hunks) == 2
        assert result[0].hunks[0].old_start == 1
        assert result[0].hunks[1].old_start == 10


# --- Throttle behavior ---


class TestThrottle:
    @pytest.mark.asyncio
    async def test_throttle_skips_rapid_calls(self, diff_service: DiffService, mock_git: AsyncMock) -> None:
        mock_git.diff.return_value = SIMPLE_DIFF
        await diff_service.on_worktree_file_modified("job-1", "/work", "main")
        await diff_service.on_worktree_file_modified("job-1", "/work", "main")
        # Only 1 diff calculation despite 2 calls (throttle window)
        assert mock_git.diff.call_count == 1

    @pytest.mark.asyncio
    async def test_finalize_ignores_throttle(self, diff_service: DiffService, mock_git: AsyncMock) -> None:
        mock_git.diff.return_value = SIMPLE_DIFF
        await diff_service.on_worktree_file_modified("job-1", "/work", "main")
        assert mock_git.diff.call_count == 1
        await diff_service.finalize("job-1", "/work", "main")
        assert mock_git.diff.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_removes_tracking(self, diff_service: DiffService) -> None:
        diff_service._last_diff_at["job-1"] = 1.0
        diff_service._locks["job-1"] = asyncio.Lock()
        diff_service.cleanup("job-1")
        assert "job-1" not in diff_service._last_diff_at
        assert "job-1" not in diff_service._locks


# --- Event publishing ---


class TestEventPublishing:
    @pytest.mark.asyncio
    async def test_publishes_diff_event(
        self, diff_service: DiffService, mock_git: AsyncMock, mock_event_bus: AsyncMock
    ) -> None:
        mock_git.diff.return_value = SIMPLE_DIFF
        await diff_service.on_worktree_file_modified("job-1", "/work", "main")
        mock_event_bus.publish.assert_called_once()
        event = mock_event_bus.publish.call_args[0][0]
        assert event.kind.value == "DiffUpdated"
        assert event.job_id == "job-1"

    @pytest.mark.asyncio
    async def test_empty_diff_still_publishes(
        self, diff_service: DiffService, mock_git: AsyncMock, mock_event_bus: AsyncMock
    ) -> None:
        mock_git.diff.return_value = ""
        await diff_service.on_worktree_file_modified("job-1", "/work", "main")
        mock_event_bus.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_git_failure_returns_empty(self, diff_service: DiffService, mock_git: AsyncMock) -> None:
        mock_git.diff.side_effect = Exception("git failed")
        files = await diff_service.calculate_diff("/work", "main")
        assert files == []


# --- Merge-in-progress guard ---


class TestMergeInProgress:
    """Diff computed while MERGE_HEAD exists must not include the other branch's changes."""

    @pytest.mark.asyncio
    async def test_uses_diff_range_not_working_tree_when_merge_in_progress(
        self, diff_service: DiffService, mock_git: AsyncMock
    ) -> None:
        mock_git.is_merge_in_progress.return_value = True
        mock_git.merge_base.return_value = "abc123"
        mock_git.diff_range.return_value = SIMPLE_DIFF
        files = await diff_service.calculate_diff("/work", "main")
        # Must use diff_range (commit-to-commit), not diff (working-tree)
        mock_git.diff_range.assert_called_once_with("abc123", "HEAD", cwd="/work")
        mock_git.diff.assert_not_called()
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_skips_intent_to_add_when_merge_in_progress(
        self, diff_service: DiffService, mock_git: AsyncMock
    ) -> None:
        mock_git.is_merge_in_progress.return_value = True
        mock_git.diff_range.return_value = ""
        await diff_service.calculate_diff("/work", "main")
        mock_git.add_intent_to_add.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_path_uses_working_tree_diff(self, diff_service: DiffService, mock_git: AsyncMock) -> None:
        mock_git.is_merge_in_progress.return_value = False
        mock_git.merge_base.return_value = "abc123"
        mock_git.diff.return_value = SIMPLE_DIFF
        files = await diff_service.calculate_diff("/work", "main")
        mock_git.add_intent_to_add.assert_called_once()
        mock_git.diff.assert_called_once_with("abc123", cwd="/work")
        mock_git.diff_range.assert_not_called()
        assert len(files) == 1
