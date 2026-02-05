"""Tests for file watcher infrastructure."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import pytest

from codeplane.index._internal.watcher import (
    BackgroundIndexer,
    FileChangeEvent,
    FileChangeKind,
    FileWatcher,
    IgnoreChecker,
    WatcherConfig,
    WatcherQueue,
)


class TestWatcherConfig:
    """Tests for WatcherConfig."""

    def test_default_config(self, tmp_path: Path) -> None:
        """Default config has reasonable defaults."""
        config = WatcherConfig(root=tmp_path)

        assert config.root == tmp_path
        assert config.debounce_seconds == 0.5
        assert config.max_queue_size == 10000
        assert len(config.ignore_patterns) > 0

    def test_custom_config(self, tmp_path: Path) -> None:
        """Custom config overrides defaults."""
        config = WatcherConfig(
            root=tmp_path,
            debounce_seconds=1.0,
            ignore_patterns=["*.log"],
            max_queue_size=100,
        )

        assert config.debounce_seconds == 1.0
        assert config.ignore_patterns == ["*.log"]
        assert config.max_queue_size == 100


class TestFileChangeEvent:
    """Tests for FileChangeEvent."""

    def test_created_event(self) -> None:
        """Created event has correct kind."""
        event = FileChangeEvent(
            path=Path("test.py"),
            kind=FileChangeKind.CREATED,
            timestamp=time.time(),
        )

        assert event.kind == FileChangeKind.CREATED
        assert event.relative_path == "test.py"

    def test_modified_event(self) -> None:
        """Modified event has correct kind."""
        event = FileChangeEvent(
            path=Path("src/main.py"),
            kind=FileChangeKind.MODIFIED,
            timestamp=time.time(),
        )

        assert event.kind == FileChangeKind.MODIFIED
        assert event.relative_path == "src/main.py"

    def test_deleted_event(self) -> None:
        """Deleted event has correct kind."""
        event = FileChangeEvent(
            path=Path("old.py"),
            kind=FileChangeKind.DELETED,
            timestamp=time.time(),
        )

        assert event.kind == FileChangeKind.DELETED


class TestIgnoreChecker:
    """Tests for IgnoreChecker."""

    def test_empty_root(self, tmp_path: Path) -> None:
        """Checker works with empty root (no gitignore)."""
        checker = IgnoreChecker(tmp_path)

        # Nothing should be ignored without patterns
        assert not checker.should_ignore(tmp_path / "test.py")

    def test_cplignore_patterns(self, tmp_path: Path) -> None:
        """Checker loads patterns from .codeplane/.cplignore."""
        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        cplignore = cpl_dir / ".cplignore"
        cplignore.write_text("*.pyc\n__pycache__/\n")

        checker = IgnoreChecker(tmp_path)

        assert checker.should_ignore(tmp_path / "test.pyc")
        assert checker.should_ignore(tmp_path / "__pycache__" / "module.pyc")
        assert not checker.should_ignore(tmp_path / "test.py")

    def test_cplignore_directory_patterns(self, tmp_path: Path) -> None:
        """Checker correctly handles directory patterns from .cplignore."""
        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        cplignore = cpl_dir / ".cplignore"
        cplignore.write_text("*.tantivy\nbuild_output/\n")

        checker = IgnoreChecker(tmp_path)

        # Patterns from .codeplane/.cplignore should be applied
        assert checker.should_ignore(tmp_path / "search.tantivy")
        assert checker.should_ignore(tmp_path / "build_output" / "file.txt")

    def test_extra_patterns(self, tmp_path: Path) -> None:
        """Extra patterns are applied."""
        checker = IgnoreChecker(tmp_path, extra_patterns=["*.log", "temp/**"])

        assert checker.should_ignore(tmp_path / "debug.log")
        assert checker.should_ignore(tmp_path / "temp" / "file.txt")
        assert not checker.should_ignore(tmp_path / "main.py")

    def test_comment_lines_ignored(self, tmp_path: Path) -> None:
        """Comment lines in .cplignore are ignored."""
        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        cplignore = cpl_dir / ".cplignore"
        cplignore.write_text("# This is a comment\n*.pyc\n# Another comment\n")

        checker = IgnoreChecker(tmp_path)

        assert checker.should_ignore(tmp_path / "test.pyc")
        # "# This is a comment" should not be treated as a pattern
        assert not checker.should_ignore(tmp_path / "# This is a comment")

    def test_empty_lines_ignored(self, tmp_path: Path) -> None:
        """Empty lines in .cplignore are ignored."""
        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        cplignore = cpl_dir / ".cplignore"
        cplignore.write_text("*.pyc\n\n\n*.log\n")

        checker = IgnoreChecker(tmp_path)

        assert checker.should_ignore(tmp_path / "test.pyc")
        assert checker.should_ignore(tmp_path / "debug.log")

    def test_path_outside_root_ignored(self, tmp_path: Path) -> None:
        """Paths outside root are always ignored."""
        checker = IgnoreChecker(tmp_path)

        # Path outside root
        outside_path = tmp_path.parent / "other_repo" / "file.py"
        assert checker.should_ignore(outside_path)


class TestFileWatcher:
    """Tests for FileWatcher."""

    def test_initialization(self, tmp_path: Path) -> None:
        """Watcher initializes correctly."""
        config = WatcherConfig(root=tmp_path)
        watcher = FileWatcher(config)

        assert watcher.root == tmp_path
        assert not watcher.is_running

    def test_should_ignore(self, tmp_path: Path) -> None:
        """Watcher delegates to ignore checker."""
        config = WatcherConfig(root=tmp_path)
        watcher = FileWatcher(config)

        # Default patterns should ignore .git
        assert watcher.should_ignore(tmp_path / ".git" / "config")
        assert not watcher.should_ignore(tmp_path / "src" / "main.py")

    @pytest.mark.asyncio
    async def test_detects_created_file(self, tmp_path: Path) -> None:
        """Watcher detects newly created files."""
        config = WatcherConfig(root=tmp_path, debounce_seconds=0.1)
        watcher = FileWatcher(config)

        # Start watching in background
        events_received: list[list[FileChangeEvent]] = []

        async def collect_events() -> None:
            async for events in watcher.watch():
                events_received.append(events)
                if len(events_received) >= 1:
                    watcher.stop()

        # Create a file after a brief delay
        async def create_file() -> None:
            await asyncio.sleep(0.15)
            (tmp_path / "new_file.py").write_text("# new")

        # Run both concurrently with timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    collect_events(),
                    create_file(),
                    return_exceptions=True,
                ),
                timeout=5.0,
            )
        except TimeoutError:
            watcher.stop()
            pytest.fail("File watcher polling did not detect change in time")

        # Should have received at least one batch
        assert len(events_received) >= 1
        all_events = [e for batch in events_received for e in batch]
        created_paths = [e.path for e in all_events if e.kind == FileChangeKind.CREATED]
        assert any("new_file.py" in str(p) for p in created_paths)

    @pytest.mark.asyncio
    async def test_detects_modified_file(self, tmp_path: Path) -> None:
        """Watcher detects modified files."""
        # Create initial file
        test_file = tmp_path / "existing.py"
        test_file.write_text("# original")

        config = WatcherConfig(root=tmp_path, debounce_seconds=0.1)
        watcher = FileWatcher(config)

        events_received: list[list[FileChangeEvent]] = []

        async def collect_events() -> None:
            async for events in watcher.watch():
                events_received.append(events)
                if len(events_received) >= 1:
                    watcher.stop()

        async def modify_file() -> None:
            await asyncio.sleep(0.15)
            test_file.write_text("# modified")

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    collect_events(),
                    modify_file(),
                    return_exceptions=True,
                ),
                timeout=5.0,
            )
        except TimeoutError:
            watcher.stop()
            pytest.fail("File watcher polling did not detect change in time")

        assert len(events_received) >= 1
        all_events = [e for batch in events_received for e in batch]
        modified_paths = [e.path for e in all_events if e.kind == FileChangeKind.MODIFIED]
        assert any("existing.py" in str(p) for p in modified_paths)

    @pytest.mark.asyncio
    async def test_detects_deleted_file(self, tmp_path: Path) -> None:
        """Watcher detects deleted files."""
        # Create initial file
        test_file = tmp_path / "to_delete.py"
        test_file.write_text("# will be deleted")

        config = WatcherConfig(root=tmp_path, debounce_seconds=0.1)
        watcher = FileWatcher(config)

        events_received: list[list[FileChangeEvent]] = []

        async def collect_events() -> None:
            async for events in watcher.watch():
                events_received.append(events)
                if len(events_received) >= 1:
                    watcher.stop()

        async def delete_file() -> None:
            await asyncio.sleep(0.15)
            test_file.unlink()

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    collect_events(),
                    delete_file(),
                    return_exceptions=True,
                ),
                timeout=5.0,
            )
        except TimeoutError:
            watcher.stop()
            pytest.fail("File watcher polling did not detect change in time")

        assert len(events_received) >= 1
        all_events = [e for batch in events_received for e in batch]
        deleted_paths = [e.path for e in all_events if e.kind == FileChangeKind.DELETED]
        assert any("to_delete.py" in str(p) for p in deleted_paths)

    @pytest.mark.asyncio
    async def test_ignores_cplignored_files(self, tmp_path: Path) -> None:
        """Watcher ignores files matching .cplignore patterns."""
        # Create .codeplane/.cplignore
        cpl_dir = tmp_path / ".codeplane"
        cpl_dir.mkdir()
        (cpl_dir / ".cplignore").write_text("*.log\n")

        config = WatcherConfig(root=tmp_path, debounce_seconds=0.1)
        watcher = FileWatcher(config)

        events_received: list[list[FileChangeEvent]] = []

        async def collect_events() -> None:
            async for events in watcher.watch():
                events_received.append(events)
                # Only expect 1 batch since ignored files don't trigger events
                if len(events_received) >= 1:
                    watcher.stop()

        async def create_files() -> None:
            await asyncio.sleep(0.15)
            (tmp_path / "debug.log").write_text("log content")  # Should be ignored
            (tmp_path / "main.py").write_text("# python")  # Should be detected

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    collect_events(),
                    create_files(),
                    return_exceptions=True,
                ),
                timeout=5.0,
            )
        except TimeoutError:
            watcher.stop()
            pytest.fail("File watcher polling did not detect change in time")

        all_events = [e for batch in events_received for e in batch]
        all_paths = [str(e.path) for e in all_events]

        # main.py should be detected, debug.log should not
        assert any("main.py" in p for p in all_paths)
        assert not any("debug.log" in p for p in all_paths)


class TestWatcherQueue:
    """Tests for WatcherQueue."""

    @pytest.mark.asyncio
    async def test_basic_put_get(self) -> None:
        """Queue supports basic put/get."""
        queue = WatcherQueue()

        events = [FileChangeEvent(Path("a.py"), FileChangeKind.MODIFIED, time.time())]
        await queue.put(events)

        assert not queue.empty()
        result = await queue.get()
        assert len(result) == 1
        assert result[0].path == Path("a.py")

    @pytest.mark.asyncio
    async def test_queue_full_drops_events(self) -> None:
        """Queue drops events when full."""
        queue = WatcherQueue(max_size=2)

        events = [FileChangeEvent(Path("a.py"), FileChangeKind.MODIFIED, time.time())]

        # Fill the queue
        assert await queue.put(events)
        assert await queue.put(events)

        # Queue is now full
        assert not await queue.put(events)
        assert queue.dropped_count == 1

    @pytest.mark.asyncio
    async def test_empty_check(self) -> None:
        """Queue correctly reports empty state."""
        queue = WatcherQueue()

        assert queue.empty()

        events = [FileChangeEvent(Path("a.py"), FileChangeKind.MODIFIED, time.time())]
        await queue.put(events)

        assert not queue.empty()

        await queue.get()
        assert queue.empty()


class TestBackgroundIndexer:
    """Tests for BackgroundIndexer."""

    @pytest.mark.asyncio
    async def test_start_stop(self, tmp_path: Path) -> None:
        """Indexer starts and stops cleanly."""
        config = WatcherConfig(root=tmp_path, debounce_seconds=0.1)
        indexed_paths: list[list[Path]] = []

        async def index_callback(paths: list[Path]) -> None:
            indexed_paths.append(paths)

        indexer = BackgroundIndexer(config, index_callback)

        assert not indexer.is_running

        await indexer.start()
        assert indexer.is_running

        await indexer.stop()
        assert not indexer.is_running

    @pytest.mark.asyncio
    async def test_indexes_changed_files(self, tmp_path: Path) -> None:
        """Indexer calls callback for changed files."""
        config = WatcherConfig(root=tmp_path, debounce_seconds=0.1)
        indexed_paths: list[list[Path]] = []
        index_event = asyncio.Event()

        async def index_callback(paths: list[Path]) -> None:
            indexed_paths.append(paths)
            index_event.set()

        indexer = BackgroundIndexer(config, index_callback)

        await indexer.start()

        # Create a file
        await asyncio.sleep(0.15)
        (tmp_path / "new_file.py").write_text("# new")

        # Wait for indexing
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(index_event.wait(), timeout=2.0)

        await indexer.stop()

        # Should have indexed the new file
        all_paths = [p for batch in indexed_paths for p in batch]
        assert any("new_file.py" in str(p) for p in all_paths)

    @pytest.mark.asyncio
    async def test_callback_error_doesnt_crash(self, tmp_path: Path) -> None:
        """Indexer survives callback errors."""
        config = WatcherConfig(root=tmp_path, debounce_seconds=0.1)
        call_count = 0

        async def failing_callback(_paths: list[Path]) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Intentional test error")

        indexer = BackgroundIndexer(config, failing_callback)

        await indexer.start()

        # Create a file to trigger callback
        await asyncio.sleep(0.15)
        (tmp_path / "trigger.py").write_text("# trigger")

        # Wait a bit for processing
        await asyncio.sleep(0.5)

        # Indexer should still be running
        assert indexer.is_running

        await indexer.stop()

    @pytest.mark.asyncio
    async def test_double_start_noop(self, tmp_path: Path) -> None:
        """Starting twice is a no-op."""
        config = WatcherConfig(root=tmp_path)

        async def noop_callback(paths: list[Path]) -> None:
            pass

        indexer = BackgroundIndexer(config, noop_callback)

        await indexer.start()
        await indexer.start()  # Should not raise

        assert indexer.is_running

        await indexer.stop()

    @pytest.mark.asyncio
    async def test_double_stop_noop(self, tmp_path: Path) -> None:
        """Stopping twice is a no-op."""
        config = WatcherConfig(root=tmp_path)

        async def noop_callback(paths: list[Path]) -> None:
            pass

        indexer = BackgroundIndexer(config, noop_callback)

        await indexer.start()
        await indexer.stop()
        await indexer.stop()  # Should not raise

        assert not indexer.is_running
