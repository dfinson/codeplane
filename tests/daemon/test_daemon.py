"""Tests for daemon components."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

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

    def test_given_started_indexer_when_queue_paths_then_paths_are_queued(
        self,
    ) -> None:
        """Queuing paths adds them to pending set."""
        # Given
        coordinator = MagicMock()
        indexer = BackgroundIndexer(coordinator=coordinator, debounce_seconds=10.0)
        indexer.start()

        # When
        indexer.queue_paths([Path("a.py"), Path("b.py")])

        # Then
        assert indexer.status.queue_size == 2

        # Cleanup
        indexer._executor.shutdown(wait=False)

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


class TestDaemonLifecycle:
    """Tests for daemon lifecycle management."""

    def test_given_no_pid_file_when_is_running_then_false(self, tmp_path: Path) -> None:
        """No PID file means daemon is not running."""
        from codeplane.daemon.lifecycle import is_daemon_running

        # Given
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()

        # When
        result = is_daemon_running(codeplane_dir)

        # Then
        assert result is False

    def test_given_pid_file_with_dead_process_when_is_running_then_false(
        self, tmp_path: Path
    ) -> None:
        """PID file with non-existent process means not running."""
        from codeplane.daemon.lifecycle import is_daemon_running

        # Given
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / "daemon.pid").write_text("999999")  # Non-existent PID
        (codeplane_dir / "daemon.port").write_text("7654")

        # When
        result = is_daemon_running(codeplane_dir)

        # Then
        assert result is False
        # Stale files should be cleaned up
        assert not (codeplane_dir / "daemon.pid").exists()

    def test_given_valid_info_when_write_pid_file_then_files_created(self, tmp_path: Path) -> None:
        """Writing PID file creates both pid and port files."""
        from codeplane.daemon.lifecycle import read_daemon_info, write_pid_file

        # Given
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()

        # When
        write_pid_file(codeplane_dir, port=8080)

        # Then
        info = read_daemon_info(codeplane_dir)
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
