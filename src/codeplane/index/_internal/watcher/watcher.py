"""File watcher for continuous background indexing.

This module implements file watching as specified in SPEC.md §7.7:
- Watch all files NOT ignored by .codeplane/.cplignore patterns
- Debounce events (handle storms, mid-write saves)
- Enqueue changed files for background indexing
- Never block UX during ingestion
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from codeplane.index._internal.ignore import IgnoreChecker

if TYPE_CHECKING:
    from collections.abc import Awaitable


class FileChangeKind(Enum):
    """Kind of file change detected."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class FileChangeEvent:
    """A file change event."""

    path: Path
    kind: FileChangeKind
    timestamp: float

    @property
    def relative_path(self) -> str:
        """Get the path relative to the watch root."""
        return str(self.path)


@dataclass
class WatcherConfig:
    """Configuration for the file watcher."""

    # Root directory to watch
    root: Path

    # Debounce interval in seconds
    debounce_seconds: float = 0.5

    # Patterns to ignore (in addition to gitignore)
    ignore_patterns: list[str] = field(
        default_factory=lambda: [
            # IDE/editor artifacts
            ".idea/**",
            ".vscode/**",
            "*.swp",
            "*.swo",
            "*~",
            # Build artifacts
            "__pycache__/**",
            "*.pyc",
            "*.pyo",
            ".mypy_cache/**",
            ".pytest_cache/**",
            ".ruff_cache/**",
            # Node.js
            "node_modules/**",
            # Git
            ".git/**",
            # CodePlane artifacts
            ".codeplane/**",
        ]
    )

    # Max queue size for pending changes
    max_queue_size: int = 10000


class FileWatcher:
    """Watches filesystem for changes and emits debounced events.

    The watcher:
    - Monitors the root directory recursively
    - Filters out files matching .codeplane/.cplignore patterns
    - Debounces rapid changes (handles save storms)
    - Yields batches of changed files for background indexing

    Usage::

        config = WatcherConfig(root=Path("/repo"))
        watcher = FileWatcher(config)

        async for events in watcher.watch():
            for event in events:
                print(f"{event.kind}: {event.path}")
    """

    def __init__(self, config: WatcherConfig) -> None:
        """Initialize file watcher."""
        self._config = config
        self._ignore_checker = IgnoreChecker(
            config.root,
            extra_patterns=config.ignore_patterns,
            respect_gitignore=True,
        )
        self._pending: dict[Path, FileChangeEvent] = {}
        self._running = False
        self._stop_event: asyncio.Event | None = None

    @property
    def root(self) -> Path:
        """Get the root directory being watched."""
        return self._config.root

    @property
    def is_running(self) -> bool:
        """Check if the watcher is running."""
        return self._running

    async def watch(self) -> AsyncIterator[list[FileChangeEvent]]:
        """Watch for file changes and yield batches.

        This is the main entry point. It yields batches of debounced
        file change events for processing.

        Yields:
            Batches of FileChangeEvent objects
        """
        self._running = True
        self._stop_event = asyncio.Event()

        try:
            async for batch in self._watch_with_polling():
                if batch:
                    yield batch
        finally:
            self._running = False
            self._stop_event = None

    def stop(self) -> None:
        """Signal the watcher to stop."""
        if self._stop_event:
            self._stop_event.set()

    async def _watch_with_polling(self) -> AsyncIterator[list[FileChangeEvent]]:
        """Watch using polling-based change detection.

        This is a simple polling implementation. For production use,
        consider using watchfiles or inotify for better performance.
        """
        # Track file mtimes for change detection
        mtimes: dict[Path, float] = {}

        # Initial scan
        mtimes = self._scan_mtimes()

        while not (self._stop_event and self._stop_event.is_set()):
            # Wait for debounce interval
            await asyncio.sleep(self._config.debounce_seconds)

            # Scan for changes
            current_mtimes = self._scan_mtimes()

            # Detect changes
            events: list[FileChangeEvent] = []
            now = asyncio.get_event_loop().time()

            # Find modified and deleted files
            for path, old_mtime in mtimes.items():
                if path not in current_mtimes:
                    events.append(
                        FileChangeEvent(
                            path=path.relative_to(self._config.root),
                            kind=FileChangeKind.DELETED,
                            timestamp=now,
                        )
                    )
                elif current_mtimes[path] > old_mtime:
                    events.append(
                        FileChangeEvent(
                            path=path.relative_to(self._config.root),
                            kind=FileChangeKind.MODIFIED,
                            timestamp=now,
                        )
                    )

            # Find new files
            for path in current_mtimes:
                if path not in mtimes:
                    events.append(
                        FileChangeEvent(
                            path=path.relative_to(self._config.root),
                            kind=FileChangeKind.CREATED,
                            timestamp=now,
                        )
                    )

            # Update tracked mtimes
            mtimes = current_mtimes

            # Yield batch if we have events
            if events:
                yield events

    def _scan_mtimes(self) -> dict[Path, float]:
        """Scan directory and collect file modification times."""
        mtimes: dict[Path, float] = {}

        for dirpath, dirnames, filenames in os.walk(self._config.root):
            dir_path = Path(dirpath)

            # Filter out ignored directories (in-place to prevent descent)
            dirnames[:] = [
                d for d in dirnames if not self._ignore_checker.should_ignore(dir_path / d)
            ]

            # Collect file mtimes
            for filename in filenames:
                file_path = dir_path / filename
                if self._ignore_checker.should_ignore(file_path):
                    continue

                try:
                    stat = file_path.stat()
                    mtimes[file_path] = stat.st_mtime
                except OSError:
                    # File may have been deleted between listing and stat
                    pass

        return mtimes

    def should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored."""
        return self._ignore_checker.should_ignore(path)


class WatcherQueue:
    """Async queue for file change events with backpressure."""

    def __init__(self, max_size: int = 10000) -> None:
        """Initialize the queue."""
        self._queue: asyncio.Queue[list[FileChangeEvent]] = asyncio.Queue(maxsize=max_size)
        self._dropped = 0

    @property
    def dropped_count(self) -> int:
        """Number of events dropped due to queue full."""
        return self._dropped

    async def put(self, events: list[FileChangeEvent]) -> bool:
        """Add events to the queue. Returns False if queue is full."""
        try:
            self._queue.put_nowait(events)
            return True
        except asyncio.QueueFull:
            self._dropped += len(events)
            return False

    async def get(self) -> list[FileChangeEvent]:
        """Get the next batch of events."""
        return await self._queue.get()

    def empty(self) -> bool:
        """Check if queue is empty."""
        return self._queue.empty()


class BackgroundIndexer:
    """Coordinates file watching with background indexing.

    This class ties together:
    - FileWatcher for change detection
    - WatcherQueue for backpressure
    - Index callback for processing changes

    Usage::

        async def index_callback(paths: list[Path]) -> None:
            # Process changed files
            pass

        indexer = BackgroundIndexer(config, index_callback)
        await indexer.start()
        # ... later ...
        await indexer.stop()
    """

    def __init__(
        self,
        config: WatcherConfig,
        index_callback: Callable[[list[Path]], Awaitable[None]],
    ) -> None:
        """Initialize background indexer."""
        self._config = config
        self._index_callback = index_callback
        self._watcher = FileWatcher(config)
        self._queue = WatcherQueue(max_size=config.max_queue_size)
        self._watch_task: asyncio.Task[None] | None = None
        self._process_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Check if the background indexer is running."""
        return self._running

    @property
    def queue_dropped(self) -> int:
        """Number of events dropped due to queue overflow."""
        return self._queue.dropped_count

    async def start(self) -> None:
        """Start background watching and indexing."""
        if self._running:
            return

        self._running = True

        # Start watcher task
        self._watch_task = asyncio.create_task(self._watch_loop())

        # Start processor task
        self._process_task = asyncio.create_task(self._process_loop())

    async def stop(self) -> None:
        """Stop background watching and indexing."""
        if not self._running:
            return

        self._running = False
        self._watcher.stop()

        # Wait for tasks to complete
        if self._watch_task:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task
            self._watch_task = None

        if self._process_task:
            self._process_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._process_task
            self._process_task = None

    async def _watch_loop(self) -> None:
        """Watch for changes and enqueue them."""
        try:
            async for events in self._watcher.watch():
                await self._queue.put(events)
        except asyncio.CancelledError:
            pass

    async def _process_loop(self) -> None:
        """Process queued changes."""
        import structlog

        logger = structlog.get_logger()
        try:
            while self._running:
                events = await self._queue.get()

                # Collect unique paths (dedupe rapid changes to same file)
                paths = list({event.path for event in events})

                # Call the index callback (log errors to keep indexer alive)
                try:
                    await self._index_callback([self._config.root / p for p in paths])
                except OSError as e:
                    # Filesystem errors (permission denied, disk full, etc.)
                    logger.warning("indexing_callback_os_error", error=str(e), paths=paths)
                except ValueError as e:
                    # Invalid data during indexing (unsupported file type, etc.)
                    logger.warning("indexing_callback_value_error", error=str(e), paths=paths)
                except Exception as e:
                    # Unexpected error - log with full traceback for debugging
                    logger.error(
                        "indexing_callback_unexpected_error",
                        error=str(e),
                        error_type=type(e).__name__,
                        paths=paths,
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            pass
