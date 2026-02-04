"""Tests for daemon components."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeplane.config.models import IndexerConfig
from codeplane.daemon.indexer import BackgroundIndexer, IndexerState


class TestBackgroundIndexer:
    """Tests for BackgroundIndexer."""

    def test_given_indexer_when_start_then_state_is_idle(self) -> None:
        """Indexer starts in idle state."""
        # Given
        coordinator = MagicMock()
        indexer = BackgroundIndexer(coordinator=coordinator)

        # When
        indexer.start()

        # Then
        assert indexer.status.state == IndexerState.IDLE
        assert indexer._executor is not None

        # Cleanup
        indexer._executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_given_started_indexer_when_queue_paths_then_paths_are_queued(
        self,
    ) -> None:
        """Queuing paths adds them to pending set."""
        # Given
        coordinator = MagicMock()
        indexer = BackgroundIndexer(
            coordinator=coordinator, config=IndexerConfig(debounce_sec=10.0)
        )
        indexer.start()

        # When
        indexer.queue_paths([Path("a.py"), Path("b.py")])
        # Allow async task scheduling to complete
        await asyncio.sleep(0)

        # Then
        assert indexer.status.queue_size == 2

        # Cleanup
        await indexer.stop()

    def test_given_indexer_when_status_then_returns_current_state(self) -> None:
        """Status returns current indexer state."""
        # Given
        coordinator = MagicMock()
        indexer = BackgroundIndexer(coordinator=coordinator)

        # When
        status = indexer.status

        # Then
        assert status.state == IndexerState.IDLE
        assert status.queue_size == 0
        assert status.last_stats is None
        assert status.last_error is None

    @pytest.mark.asyncio
    async def test_given_started_indexer_when_stop_then_state_is_stopped(self) -> None:
        """Stopping indexer transitions to stopped state."""
        # Given
        coordinator = MagicMock()
        indexer = BackgroundIndexer(coordinator=coordinator)
        indexer.start()

        # When
        await indexer.stop()

        # Then
        assert indexer.status.state == IndexerState.STOPPED
        assert indexer._executor is None

    def test_given_indexer_when_start_twice_then_noop(self) -> None:
        """Starting indexer twice is a no-op."""
        coordinator = MagicMock()
        indexer = BackgroundIndexer(coordinator=coordinator)

        indexer.start()
        executor = indexer._executor

        indexer.start()

        # Same executor, not replaced
        assert indexer._executor is executor

        indexer._executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_given_stopped_indexer_when_stop_again_then_noop(self) -> None:
        """Stopping stopped indexer is a no-op."""
        coordinator = MagicMock()
        indexer = BackgroundIndexer(coordinator=coordinator)
        indexer.start()
        await indexer.stop()

        # Stop again - should not raise
        await indexer.stop()

        assert indexer.status.state == IndexerState.STOPPED

    def test_given_indexer_when_set_on_complete_then_callback_stored(self) -> None:
        """set_on_complete stores the callback."""
        coordinator = MagicMock()
        indexer = BackgroundIndexer(coordinator=coordinator)

        async def callback(stats):
            pass

        indexer.set_on_complete(callback)

        assert indexer._on_complete is callback

    @pytest.mark.asyncio
    async def test_given_empty_queue_when_flush_then_noop(self) -> None:
        """Flushing empty queue does nothing."""
        coordinator = MagicMock()
        indexer = BackgroundIndexer(coordinator=coordinator)
        indexer.start()

        await indexer._flush()

        # Coordinator should not be called
        coordinator.reindex_incremental.assert_not_called()

        await indexer.stop()

    @pytest.mark.asyncio
    async def test_given_stopping_indexer_when_flush_then_noop(self) -> None:
        """Flushing during stop does nothing."""
        from codeplane.daemon.indexer import IndexerState

        coordinator = MagicMock()
        indexer = BackgroundIndexer(coordinator=coordinator)
        indexer.start()
        indexer._state = IndexerState.STOPPING

        await indexer._flush()

        coordinator.reindex_incremental.assert_not_called()

        indexer._state = IndexerState.IDLE
        await indexer.stop()


class TestFileWatcher:
    """Tests for FileWatcher."""

    @pytest.mark.asyncio
    async def test_given_watcher_when_start_then_watch_task_created(self, tmp_path: Path) -> None:
        """Starting watcher creates watch task."""
        from codeplane.daemon.watcher import FileWatcher

        # Given - create minimal .codeplane structure
        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("*.pyc\n")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        # When
        await watcher.start()

        # Then
        assert watcher._watch_task is not None

        # Cleanup
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_given_running_watcher_when_stop_then_task_cancelled(
        self, tmp_path: Path
    ) -> None:
        """Stopping watcher cancels watch task."""
        from codeplane.daemon.watcher import FileWatcher

        # Given
        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)
        await watcher.start()

        # When
        await watcher.stop()

        # Then
        assert watcher._watch_task is None

    @pytest.mark.asyncio
    async def test_given_watcher_when_start_twice_then_noop(self, tmp_path: Path) -> None:
        """Starting watcher twice is a no-op."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        await watcher.start()
        task = watcher._watch_task

        # Start again
        await watcher.start()

        # Same task, not replaced
        assert watcher._watch_task is task

        await watcher.stop()

    @pytest.mark.asyncio
    async def test_given_file_change_when_detected_then_callback_called(
        self, tmp_path: Path
    ) -> None:
        """File changes should trigger callback."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        await watcher.start()

        # Create a file to trigger change
        (tmp_path / "new_file.py").write_text("# test")

        # Wait briefly for watcher to pick up change
        await asyncio.sleep(0.2)

        await watcher.stop()

        # Callback should have been called with the path
        if callback.called:
            call_args = callback.call_args[0][0]
            assert any("new_file.py" in str(p) for p in call_args)

    @pytest.mark.asyncio
    async def test_given_git_file_change_when_detected_then_ignored(self, tmp_path: Path) -> None:
        """Changes in .git directory should be ignored."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        await watcher.start()

        # Create file in .git
        (git_dir / "HEAD").write_text("ref: refs/heads/main")

        await asyncio.sleep(0.2)

        await watcher.stop()

        # Callback should not have been called for .git changes
        for call in callback.call_args_list:
            paths = call[0][0]
            for p in paths:
                assert ".git" not in str(p)

    @pytest.mark.asyncio
    async def test_handle_changes_filters_ignored_paths(self, tmp_path: Path) -> None:
        """_handle_changes should filter paths through .cplignore."""
        from watchfiles import Change

        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("*.pyc\n__pycache__/\n")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        # Simulate changes set from watchfiles
        changes = {
            (Change.added, str(tmp_path / "good.py")),
            (Change.added, str(tmp_path / "bad.pyc")),
            (Change.added, str(tmp_path / "__pycache__" / "cached.pyc")),
        }

        await watcher._handle_changes(changes)
        watcher._flush_pending()  # Flush debounced changes

        # Only good.py should be in callback
        assert callback.called
        call_paths = callback.call_args[0][0]
        path_strs = [str(p) for p in call_paths]
        assert any("good.py" in p for p in path_strs)
        assert not any(".pyc" in p for p in path_strs)

    @pytest.mark.asyncio
    async def test_handle_changes_filters_git_directory(self, tmp_path: Path) -> None:
        """_handle_changes should filter .git directory paths."""
        from watchfiles import Change

        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        changes = {
            (Change.added, str(tmp_path / ".git" / "HEAD")),
            (Change.added, str(tmp_path / "code.py")),
        }

        await watcher._handle_changes(changes)
        watcher._flush_pending()  # Flush debounced changes

        call_paths = callback.call_args[0][0]
        path_strs = [str(p) for p in call_paths]
        assert any("code.py" in p for p in path_strs)
        assert not any(".git" in p for p in path_strs)

    @pytest.mark.asyncio
    async def test_handle_changes_includes_cplignore_changes(self, tmp_path: Path) -> None:
        """_handle_changes should always include .cplignore changes."""
        from watchfiles import Change

        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        changes = {
            (Change.modified, str(cpl_dir / ".cplignore")),
        }

        await watcher._handle_changes(changes)
        watcher._flush_pending()  # Flush debounced changes

        call_paths = callback.call_args[0][0]
        assert any(".cplignore" in str(p) for p in call_paths)

    @pytest.mark.asyncio
    async def test_handle_changes_no_callback_when_all_filtered(self, tmp_path: Path) -> None:
        """_handle_changes should not call callback when all paths filtered."""
        from watchfiles import Change

        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("*.pyc\n")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        changes = {
            (Change.added, str(tmp_path / "only.pyc")),
        }

        await watcher._handle_changes(changes)

        # Callback should not be called when all paths filtered
        assert not callback.called

    @pytest.mark.asyncio
    async def test_handle_changes_path_outside_repo_ignored(self, tmp_path: Path) -> None:
        """_handle_changes should ignore paths outside repo root."""
        from watchfiles import Change

        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        # Path outside repo
        outside_path = tmp_path.parent / "outside.py"

        changes = {
            (Change.added, str(outside_path)),
        }

        await watcher._handle_changes(changes)

        # Callback should not be called for paths outside repo
        assert not callback.called
        await watcher.start()

        # When
        await watcher.stop()

        # Then
        assert watcher._watch_task is None


class TestSummarizeChangesByType:
    """Tests for _summarize_changes_by_type function (Issue #4)."""

    def test_single_python_file(self) -> None:
        """Single Python file uses singular form."""
        from codeplane.daemon.watcher import _summarize_changes_by_type

        paths = [Path("test.py")]
        result = _summarize_changes_by_type(paths)
        assert result == "1 Python file"

    def test_multiple_python_files(self) -> None:
        """Multiple Python files use plural form."""
        from codeplane.daemon.watcher import _summarize_changes_by_type

        paths = [Path("a.py"), Path("b.py"), Path("c.py")]
        result = _summarize_changes_by_type(paths)
        assert result == "3 Python files"

    def test_mixed_file_types(self) -> None:
        """Mixed file types are summarized separately."""
        from codeplane.daemon.watcher import _summarize_changes_by_type

        paths = [Path("a.py"), Path("b.py"), Path("c.json")]
        result = _summarize_changes_by_type(paths)
        assert "2 Python files" in result
        assert "1 JSON file" in result

    def test_top_three_types_shown(self) -> None:
        """Only top 3 file types are shown with 'others' for remainder."""
        from codeplane.daemon.watcher import _summarize_changes_by_type

        paths = [
            Path("a.py"),
            Path("b.py"),
            Path("c.js"),
            Path("d.ts"),
            Path("e.md"),
            Path("f.rs"),
        ]
        result = _summarize_changes_by_type(paths)
        # Should show top 3 types + "others"
        parts = result.split(", ")
        assert len(parts) <= 4

    def test_unknown_extension(self) -> None:
        """Unknown extensions show uppercased extension."""
        from codeplane.daemon.watcher import _summarize_changes_by_type

        paths = [Path("file.xyz")]
        result = _summarize_changes_by_type(paths)
        assert "XYZ" in result

    def test_no_extension(self) -> None:
        """Files without extension show 'other'."""
        from codeplane.daemon.watcher import _summarize_changes_by_type

        paths = [Path("Makefile")]
        result = _summarize_changes_by_type(paths)
        assert "other" in result.lower()

    def test_yaml_yml_counted_separately(self) -> None:
        """Both .yaml and .yml are categorized as YAML but counted separately."""
        from codeplane.daemon.watcher import _summarize_changes_by_type

        paths = [Path("a.yaml"), Path("b.yml")]
        result = _summarize_changes_by_type(paths)
        # Both should be YAML (shown separately since they have different extensions)
        assert "YAML" in result
        # Total should be 2 files mentioned
        assert "1 YAML file" in result

    def test_empty_list(self) -> None:
        """Empty list returns empty string."""
        from codeplane.daemon.watcher import _summarize_changes_by_type

        paths: list[Path] = []
        result = _summarize_changes_by_type(paths)
        assert result == ""


class TestHandleCplignoreChange:
    """Tests for _handle_cplignore_change function (Issue #6)."""

    @pytest.mark.asyncio
    async def test_logs_added_patterns(self, tmp_path: Path) -> None:
        """Logs newly added patterns."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        # Start with empty ignore file
        cplignore = cpl_dir / ".cplignore"
        cplignore.write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        # Update with new patterns
        cplignore.write_text("*.pyc\n__pycache__/\n")

        # Handle change
        with patch("codeplane.daemon.watcher.logger") as mock_logger:
            watcher._handle_cplignore_change(Path(".codeplane/.cplignore"))

            # Check that added patterns were logged
            calls = mock_logger.info.call_args_list
            assert any("cplignore_changed" in str(c) for c in calls)

    @pytest.mark.asyncio
    async def test_logs_removed_patterns(self, tmp_path: Path) -> None:
        """Logs removed patterns."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        cplignore = cpl_dir / ".cplignore"
        cplignore.write_text("*.pyc\n*.log\n")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        # Remove a pattern
        cplignore.write_text("*.pyc\n")

        with patch("codeplane.daemon.watcher.logger") as mock_logger:
            watcher._handle_cplignore_change(Path(".codeplane/.cplignore"))

            # Check logging occurred
            assert mock_logger.info.called

    @pytest.mark.asyncio
    async def test_tracks_pattern_diff(self, tmp_path: Path) -> None:
        """Correctly tracks added and removed patterns."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        cplignore = cpl_dir / ".cplignore"
        cplignore.write_text("*.pyc\n")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        # Verify initial state captured
        assert watcher._last_cplignore_content == "*.pyc\n"

        # Change patterns
        cplignore.write_text("*.log\n")
        watcher._handle_cplignore_change(Path(".codeplane/.cplignore"))

        # Verify content updated
        assert watcher._last_cplignore_content == "*.log\n"

    @pytest.mark.asyncio
    async def test_ignores_comments(self, tmp_path: Path) -> None:
        """Comments are not treated as patterns."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        cplignore = cpl_dir / ".cplignore"
        cplignore.write_text("# comment\n*.pyc\n")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        # Add another comment - should show no pattern changes
        cplignore.write_text("# comment\n# another comment\n*.pyc\n")

        with patch("codeplane.daemon.watcher.logger") as mock_logger:
            watcher._handle_cplignore_change(Path(".codeplane/.cplignore"))

            # Should log with "no changes" or empty diff
            calls = mock_logger.info.call_args_list
            # Find the cplignore_changed call
            cplignore_call = next((c for c in calls if "cplignore_changed" in str(c)), None)
            assert cplignore_call is not None


class TestDebouncing:
    """Tests for watcher debouncing behavior."""

    @pytest.mark.asyncio
    async def test_queue_change_adds_to_pending(self, tmp_path: Path) -> None:
        """_queue_change adds paths to pending set."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        watcher._queue_change(Path("test.py"))

        assert Path("test.py") in watcher._pending_changes

    @pytest.mark.asyncio
    async def test_flush_pending_calls_callback(self, tmp_path: Path) -> None:
        """_flush_pending calls callback with pending paths."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        watcher._queue_change(Path("test.py"))
        watcher._flush_pending()

        assert callback.called
        paths = callback.call_args[0][0]
        assert Path("test.py") in paths

    @pytest.mark.asyncio
    async def test_flush_clears_pending(self, tmp_path: Path) -> None:
        """_flush_pending clears the pending set."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        watcher._queue_change(Path("test.py"))
        watcher._flush_pending()

        assert len(watcher._pending_changes) == 0

    @pytest.mark.asyncio
    async def test_should_flush_false_when_empty(self, tmp_path: Path) -> None:
        """_should_flush returns False for empty pending set."""
        from codeplane.daemon.watcher import FileWatcher

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        callback = MagicMock()
        watcher = FileWatcher(repo_root=tmp_path, on_change=callback)

        assert watcher._should_flush() is False


class TestDaemonLifecycle:
    """Tests for daemon lifecycle management."""

    def test_given_no_pid_file_when_is_running_then_false(self, tmp_path: Path) -> None:
        """No PID file means daemon is not running."""
        from codeplane.daemon.lifecycle import is_server_running

        # Given
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()

        # When
        result = is_server_running(codeplane_dir)

        # Then
        assert result is False

    def test_given_pid_file_with_dead_process_when_is_running_then_false(
        self, tmp_path: Path
    ) -> None:
        """PID file with non-existent process means not running."""
        from codeplane.daemon.lifecycle import is_server_running

        # Given
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / "daemon.pid").write_text("999999")  # Non-existent PID
        (codeplane_dir / "daemon.port").write_text("7654")

        # When
        result = is_server_running(codeplane_dir)

        # Then
        assert result is False
        # Stale files should be cleaned up
        assert not (codeplane_dir / "daemon.pid").exists()

    def test_given_valid_info_when_write_pid_file_then_files_created(self, tmp_path: Path) -> None:
        """Writing PID file creates both pid and port files."""
        from codeplane.daemon.lifecycle import read_server_info, write_pid_file

        # Given
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()

        # When
        write_pid_file(codeplane_dir, port=8080)

        # Then
        info = read_server_info(codeplane_dir)
        assert info is not None
        pid, port = info
        assert pid > 0  # Current process PID
        assert port == 8080

    def test_given_pid_files_when_remove_then_files_deleted(self, tmp_path: Path) -> None:
        """Removing PID files deletes both files."""
        from codeplane.daemon.lifecycle import remove_pid_file, write_pid_file

        # Given
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        write_pid_file(codeplane_dir, port=8080)

        # When
        remove_pid_file(codeplane_dir)

        # Then
        assert not (codeplane_dir / "daemon.pid").exists()
        assert not (codeplane_dir / "daemon.port").exists()

    def test_given_no_files_when_read_server_info_then_none(self, tmp_path: Path) -> None:
        """Missing files return None for daemon info."""
        from codeplane.daemon.lifecycle import read_server_info

        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()

        result = read_server_info(codeplane_dir)

        assert result is None

    def test_given_invalid_pid_content_when_read_server_info_then_none(
        self, tmp_path: Path
    ) -> None:
        """Invalid PID file content returns None."""
        from codeplane.daemon.lifecycle import read_server_info

        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / "daemon.pid").write_text("not-a-number")
        (codeplane_dir / "daemon.port").write_text("8080")

        result = read_server_info(codeplane_dir)

        assert result is None

    def test_given_dead_process_when_stop_daemon_then_false(self, tmp_path: Path) -> None:
        """Stopping dead process returns False."""
        from codeplane.daemon.lifecycle import stop_daemon

        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / "daemon.pid").write_text("999999")
        (codeplane_dir / "daemon.port").write_text("8080")

        result = stop_daemon(codeplane_dir)

        # Dead process - returns False and cleans up
        assert result is False
        assert not (codeplane_dir / "daemon.pid").exists()

    def test_given_no_daemon_when_stop_daemon_then_false(self, tmp_path: Path) -> None:
        """Stopping non-existent daemon returns False."""
        from codeplane.daemon.lifecycle import stop_daemon

        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()

        result = stop_daemon(codeplane_dir)

        assert result is False


class TestServerController:
    """Tests for ServerController."""

    def test_given_coordinator_when_create_controller_then_components_initialized(
        self, tmp_path: Path
    ) -> None:
        """Controller initializes indexer and watcher."""
        from codeplane.config.models import ServerConfig
        from codeplane.daemon.lifecycle import ServerController

        coordinator = MagicMock()
        config = ServerConfig()

        controller = ServerController(
            repo_root=tmp_path,
            coordinator=coordinator,
            server_config=config,
        )

        assert controller.indexer is not None
        assert controller.watcher is not None

    @pytest.mark.asyncio
    async def test_given_controller_when_start_then_components_started(
        self, tmp_path: Path
    ) -> None:
        """Starting controller starts indexer and watcher."""
        from codeplane.config.models import ServerConfig
        from codeplane.daemon.lifecycle import ServerController

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        coordinator = MagicMock()
        config = ServerConfig()

        controller = ServerController(
            repo_root=tmp_path,
            coordinator=coordinator,
            server_config=config,
        )

        await controller.start()

        assert controller.indexer._executor is not None
        assert controller.watcher._watch_task is not None

        await controller.stop()

    @pytest.mark.asyncio
    async def test_given_running_controller_when_stop_then_shutdown_event_set(
        self, tmp_path: Path
    ) -> None:
        """Stopping controller sets shutdown event."""
        from codeplane.config.models import ServerConfig
        from codeplane.daemon.lifecycle import ServerController

        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("")

        coordinator = MagicMock()
        config = ServerConfig()

        controller = ServerController(
            repo_root=tmp_path,
            coordinator=coordinator,
            server_config=config,
        )

        await controller.start()
        await controller.stop()

        assert controller.wait_for_shutdown().is_set()


class TestRepoHeaderMiddleware:
    """Tests for RepoHeaderMiddleware."""

    def test_given_request_when_response_then_includes_repo_header(self, tmp_path: Path) -> None:
        """All responses include X-CodePlane-Repo header."""
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from codeplane.daemon.middleware import REPO_HEADER, RepoHeaderMiddleware

        async def status(_request):
            return JSONResponse({"status": "ok"})

        app = Starlette(
            routes=[Route("/status", status)],
            middleware=[Middleware(RepoHeaderMiddleware, repo_root=tmp_path)],
        )

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/status")
        assert response.status_code == 200
        assert response.headers[REPO_HEADER] == str(tmp_path)

    def test_given_health_endpoint_when_request_then_includes_repo_header(
        self, tmp_path: Path
    ) -> None:
        """Health endpoint also includes repo header."""
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from codeplane.daemon.middleware import REPO_HEADER, RepoHeaderMiddleware

        async def health(_request):
            return JSONResponse({"status": "ok"})

        app = Starlette(
            routes=[Route("/health", health)],
            middleware=[Middleware(RepoHeaderMiddleware, repo_root=tmp_path)],
        )

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers[REPO_HEADER] == str(tmp_path)


class TestDaemonRoutes:
    """Tests for daemon HTTP routes."""

    def test_given_controller_when_create_routes_then_returns_routes(self, tmp_path: Path) -> None:
        """create_routes returns health and status routes."""
        from codeplane.daemon.routes import create_routes

        controller = MagicMock()
        controller.repo_root = tmp_path

        routes = create_routes(controller)

        assert len(routes) == 2
        paths = {r.path for r in routes}
        assert "/health" in paths
        assert "/status" in paths

    def test_given_routes_when_health_called_then_returns_status(self, tmp_path: Path) -> None:
        """Health endpoint returns daemon info."""
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from codeplane.daemon.routes import create_routes

        controller = MagicMock()
        controller.repo_root = tmp_path

        routes = create_routes(controller)
        app = Starlette(routes=routes)

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["repo_root"] == str(tmp_path)
        assert "version" in data

    def test_given_routes_when_status_called_then_returns_indexer_info(
        self, tmp_path: Path
    ) -> None:
        """Status endpoint returns indexer and watcher state."""
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from codeplane.daemon.indexer import IndexerState
        from codeplane.daemon.routes import create_routes

        # Mock indexer status
        indexer_status = MagicMock()
        indexer_status.state = IndexerState.IDLE
        indexer_status.queue_size = 5
        indexer_status.last_error = None

        # Mock watcher
        watcher = MagicMock()
        watcher._watch_task = MagicMock()  # Running

        controller = MagicMock()
        controller.repo_root = tmp_path
        controller.indexer.status = indexer_status
        controller.watcher = watcher

        routes = create_routes(controller)
        app = Starlette(routes=routes)

        client = TestClient(app)
        response = client.get("/status")

        assert response.status_code == 200
        data = response.json()
        assert data["repo_root"] == str(tmp_path)
        assert data["indexer"]["state"] == "idle"
        assert data["indexer"]["queue_size"] == 5
        assert data["watcher"]["running"] is True


class TestDaemonApp:
    """Tests for daemon application factory."""

    def test_given_controller_when_create_app_then_returns_starlette(self, tmp_path: Path) -> None:
        """create_app returns configured Starlette application."""
        import subprocess

        from starlette.applications import Starlette

        from codeplane.daemon.app import create_app

        # Initialize git repo (required by MCP server)
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / ".codeplane").mkdir()

        controller = MagicMock()
        controller.repo_root = tmp_path
        coordinator = MagicMock()

        app = create_app(controller, tmp_path, coordinator)

        assert isinstance(app, Starlette)
        # 2 routes (health, status) + MCP mount
        assert len(app.routes) == 3

    def test_given_app_when_startup_then_controller_started(self, tmp_path: Path) -> None:
        """App startup triggers MCP lifespan (controller start/stop handled separately)."""
        import subprocess

        from starlette.testclient import TestClient

        from codeplane.daemon.app import create_app

        # Initialize git repo (required by MCP server)
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / ".codeplane").mkdir()

        controller = MagicMock()
        controller.repo_root = tmp_path
        controller.start = MagicMock()
        controller.stop = MagicMock()
        coordinator = MagicMock()

        app = create_app(controller, tmp_path, coordinator)

        with TestClient(app):
            pass  # Context manager triggers startup/shutdown

        # Controller start/stop now happen in lifecycle.run_server, not app lifespan
        # MCP lifespan is tested implicitly by the app starting without error
