"""Tests for file watcher and background indexer."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from codeplane.index._internal.watcher.watcher import (
    BackgroundIndexer,
    FileChangeEvent,
    FileChangeKind,
    FileWatcher,
    WatcherConfig,
    WatcherQueue,
)


class TestFileChangeKind:
    """Tests for FileChangeKind enum."""

    def test_has_expected_values(self) -> None:
        """FileChangeKind has expected values."""
        assert FileChangeKind.CREATED.value == "created"
        assert FileChangeKind.MODIFIED.value == "modified"
        assert FileChangeKind.DELETED.value == "deleted"


class TestFileChangeEvent:
    """Tests for FileChangeEvent dataclass."""

    def test_construction(self) -> None:
        """FileChangeEvent stores event details."""
        event = FileChangeEvent(
            path=Path("src/test.py"),
            kind=FileChangeKind.MODIFIED,
            timestamp=1234567890.0,
        )
        assert event.path == Path("src/test.py")
        assert event.kind == FileChangeKind.MODIFIED
        assert event.timestamp == 1234567890.0

    def test_relative_path_property(self) -> None:
        """relative_path returns string representation."""
        event = FileChangeEvent(
            path=Path("src/module.py"),
            kind=FileChangeKind.CREATED,
            timestamp=0.0,
        )
        assert event.relative_path == "src/module.py"


class TestWatcherConfig:
    """Tests for WatcherConfig dataclass."""

    def test_construction(self) -> None:
        """WatcherConfig stores configuration."""
        config = WatcherConfig(root=Path("/test/repo"))
        assert config.root == Path("/test/repo")
        assert config.debounce_seconds == 0.5
        assert config.max_queue_size == 10000

    def test_custom_debounce(self) -> None:
        """WatcherConfig accepts custom debounce."""
        config = WatcherConfig(root=Path("/test"), debounce_seconds=1.0)
        assert config.debounce_seconds == 1.0

    def test_default_ignore_patterns(self) -> None:
        """WatcherConfig has default ignore patterns."""
        config = WatcherConfig(root=Path("/test"))
        assert len(config.ignore_patterns) > 0
        assert ".git/**" in config.ignore_patterns
        assert "__pycache__/**" in config.ignore_patterns
        assert "node_modules/**" in config.ignore_patterns

    def test_custom_ignore_patterns(self) -> None:
        """WatcherConfig accepts custom ignore patterns."""
        config = WatcherConfig(
            root=Path("/test"),
            ignore_patterns=["custom/**", "*.log"],
        )
        assert "custom/**" in config.ignore_patterns
        assert "*.log" in config.ignore_patterns


class TestFileWatcher:
    """Tests for FileWatcher."""

    def test_init_stores_config(self) -> None:
        """FileWatcher stores configuration."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            watcher = FileWatcher(config)
            assert watcher._config is config

    def test_root_property(self) -> None:
        """root property returns config root."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = WatcherConfig(root=root)
            watcher = FileWatcher(config)
            assert watcher.root == root

    def test_is_running_initially_false(self) -> None:
        """is_running is False before watch starts."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            watcher = FileWatcher(config)
            assert watcher.is_running is False

    def test_stop_sets_event(self) -> None:
        """stop() signals the watcher to stop."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            watcher = FileWatcher(config)
            watcher._stop_event = asyncio.Event()
            watcher.stop()
            assert watcher._stop_event.is_set()

    def test_should_ignore_delegates_to_checker(self) -> None:
        """should_ignore delegates to IgnoreChecker."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            watcher = FileWatcher(config)

            # .git should be ignored by default patterns
            git_path = Path(tmp) / ".git" / "config"
            assert watcher.should_ignore(git_path)

    def test_scan_mtimes_finds_files(self) -> None:
        """_scan_mtimes collects file modification times."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Create test file
            test_file = root / "test.py"
            test_file.write_text("# test")

            config = WatcherConfig(root=root)
            watcher = FileWatcher(config)
            mtimes = watcher._scan_mtimes()

            assert test_file in mtimes
            assert mtimes[test_file] > 0

    def test_scan_mtimes_ignores_patterns(self) -> None:
        """_scan_mtimes respects ignore patterns."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Create .git directory
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text("[core]")

            config = WatcherConfig(root=root)
            watcher = FileWatcher(config)
            mtimes = watcher._scan_mtimes()

            # .git files should not be in mtimes
            for path in mtimes:
                assert ".git" not in str(path)


class TestWatcherQueue:
    """Tests for WatcherQueue."""

    def test_init_creates_queue(self) -> None:
        """WatcherQueue initializes with async queue."""
        queue = WatcherQueue(max_size=100)
        assert queue._queue.maxsize == 100
        assert queue._dropped == 0

    def test_dropped_count_initially_zero(self) -> None:
        """dropped_count is 0 initially."""
        queue = WatcherQueue()
        assert queue.dropped_count == 0

    def test_empty_initially(self) -> None:
        """Queue is empty initially."""
        queue = WatcherQueue()
        assert queue.empty() is True

    @pytest.mark.asyncio
    async def test_put_adds_events(self) -> None:
        """put adds events to queue."""
        queue = WatcherQueue()
        events = [
            FileChangeEvent(
                path=Path("test.py"),
                kind=FileChangeKind.MODIFIED,
                timestamp=0.0,
            )
        ]
        result = await queue.put(events)
        assert result is True
        assert queue.empty() is False

    @pytest.mark.asyncio
    async def test_get_retrieves_events(self) -> None:
        """get retrieves events from queue."""
        queue = WatcherQueue()
        events = [
            FileChangeEvent(
                path=Path("test.py"),
                kind=FileChangeKind.CREATED,
                timestamp=0.0,
            )
        ]
        await queue.put(events)
        retrieved = await queue.get()
        assert retrieved == events

    @pytest.mark.asyncio
    async def test_put_returns_false_when_full(self) -> None:
        """put returns False when queue is full."""
        queue = WatcherQueue(max_size=1)
        events = [
            FileChangeEvent(
                path=Path("test.py"),
                kind=FileChangeKind.MODIFIED,
                timestamp=0.0,
            )
        ]
        # First put succeeds
        await queue.put(events)
        # Second put fails (queue full)
        result = await queue.put(events)
        assert result is False
        assert queue.dropped_count == 1


class TestBackgroundIndexer:
    """Tests for BackgroundIndexer."""

    def test_init_stores_dependencies(self) -> None:
        """BackgroundIndexer stores config and callback."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            callback = AsyncMock()
            indexer = BackgroundIndexer(config, callback)
            assert indexer._config is config
            assert indexer._index_callback is callback

    def test_is_running_initially_false(self) -> None:
        """is_running is False before start."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            callback = AsyncMock()
            indexer = BackgroundIndexer(config, callback)
            assert indexer.is_running is False

    def test_queue_dropped_initially_zero(self) -> None:
        """queue_dropped is 0 initially."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            callback = AsyncMock()
            indexer = BackgroundIndexer(config, callback)
            assert indexer.queue_dropped == 0

    @pytest.mark.asyncio
    async def test_start_sets_running(self) -> None:
        """start() sets is_running to True."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            callback = AsyncMock()
            indexer = BackgroundIndexer(config, callback)

            await indexer.start()
            assert indexer.is_running is True

            # Cleanup
            await indexer.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self) -> None:
        """stop() sets is_running to False."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            callback = AsyncMock()
            indexer = BackgroundIndexer(config, callback)

            await indexer.start()
            await indexer.stop()
            assert indexer.is_running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        """Multiple start calls are idempotent."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            callback = AsyncMock()
            indexer = BackgroundIndexer(config, callback)

            await indexer.start()
            await indexer.start()  # Second call should be no-op
            assert indexer.is_running is True

            await indexer.stop()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self) -> None:
        """Multiple stop calls are idempotent."""
        with tempfile.TemporaryDirectory() as tmp:
            config = WatcherConfig(root=Path(tmp))
            callback = AsyncMock()
            indexer = BackgroundIndexer(config, callback)

            # Stop without start should be no-op
            await indexer.stop()
            await indexer.stop()
            assert indexer.is_running is False
