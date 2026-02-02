"""Background indexer using thread pool for CPU-bound work."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from codeplane.index.ops import IndexCoordinator, IndexStats

logger = structlog.get_logger()


class IndexerState(Enum):
    """Background indexer state."""

    IDLE = "idle"
    INDEXING = "indexing"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class IndexerStatus:
    """Current indexer status."""

    state: IndexerState
    queue_size: int
    last_stats: IndexStats | None = None
    last_error: str | None = None


@dataclass
class BackgroundIndexer:
    """
    Non-blocking indexer using thread pool for CPU-bound work.

    Design:
    - HTTP server runs in main asyncio loop
    - Indexing work is submitted to ThreadPoolExecutor
    - Queue batches rapid changes with debouncing
    - IndexCoordinator locks ensure thread safety
    """

    coordinator: IndexCoordinator
    debounce_seconds: float = 0.5
    max_workers: int = 1  # Single worker for serialization

    _state: IndexerState = field(default=IndexerState.IDLE, init=False)
    _executor: ThreadPoolExecutor | None = field(default=None, init=False)
    _pending_paths: set[Path] = field(default_factory=set, init=False)
    _pending_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _debounce_task: asyncio.Task[None] | None = field(default=None, init=False)
    _last_stats: IndexStats | None = field(default=None, init=False)
    _last_error: str | None = field(default=None, init=False)
    _on_complete: Callable[[IndexStats], Awaitable[None]] | None = field(default=None, init=False)

    def start(self) -> None:
        """Start the background indexer."""
        if self._executor is not None:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="codeplane-indexer",
        )
        self._state = IndexerState.IDLE
        logger.info("background_indexer_started", max_workers=self.max_workers)

    async def stop(self) -> None:
        """Stop the background indexer gracefully."""
        self._state = IndexerState.STOPPING

        # Cancel pending debounce
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task

        # Shutdown executor
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

        self._state = IndexerState.STOPPED
        logger.info("background_indexer_stopped")

    def queue_paths(self, paths: list[Path]) -> None:
        """Queue paths for indexing with debouncing."""
        with self._pending_lock:
            self._pending_paths.update(paths)
            count = len(self._pending_paths)

        logger.debug("paths_queued", new_paths=len(paths), total_pending=count)

        # Schedule debounced flush
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        """Schedule a debounced flush of pending paths."""
        loop = asyncio.get_event_loop()

        # Cancel existing debounce task
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()

        self._debounce_task = loop.create_task(self._debounced_flush())

    async def _debounced_flush(self) -> None:
        """Wait for debounce period then flush."""
        try:
            await asyncio.sleep(self.debounce_seconds)
            await self._flush()
        except asyncio.CancelledError:
            pass

    async def _flush(self) -> None:
        """Flush pending paths to the indexer."""
        if self._executor is None or self._state == IndexerState.STOPPING:
            return

        # Atomically grab and clear pending paths
        with self._pending_lock:
            if not self._pending_paths:
                return
            paths = list(self._pending_paths)
            self._pending_paths.clear()

        self._state = IndexerState.INDEXING

        try:
            from rich.console import Console

            console = Console(stderr=True)
            with console.status(f"[cyan]Reindexing {len(paths)} file(s)...[/cyan]", spinner="dots"):
                # Run indexing in thread pool
                loop = asyncio.get_event_loop()
                stats = await loop.run_in_executor(
                    self._executor,
                    self._index_sync,
                    paths,
                )
            self._last_stats = stats
            self._last_error = None

            # Build descriptive message showing what changed
            parts: list[str] = []
            if stats.files_added:
                parts.append(f"{stats.files_added} added")
            if stats.files_updated:
                parts.append(f"{stats.files_updated} updated")
            if stats.files_removed:
                parts.append(f"{stats.files_removed} removed")
            summary = ", ".join(parts) if parts else "no changes"
            console.print(f"  [green]✓[/green] {summary} in {stats.duration_seconds:.2f}s")

            # Notify completion callback
            if self._on_complete is not None:
                await self._on_complete(stats)

        except Exception as e:
            self._last_error = str(e)
            logger.error("indexing_failed", error=str(e))

        finally:
            self._state = IndexerState.IDLE

    def _index_sync(self, paths: list[Path]) -> IndexStats:
        """Synchronous indexing - runs in thread pool."""
        # Run async method in new event loop for thread
        return asyncio.run(self.coordinator.reindex_incremental(paths))

    def set_on_complete(self, callback: Callable[[IndexStats], Awaitable[None]]) -> None:
        """Set callback to invoke after successful indexing."""
        self._on_complete = callback

    @property
    def status(self) -> IndexerStatus:
        """Get current indexer status."""
        with self._pending_lock:
            queue_size = len(self._pending_paths)
        return IndexerStatus(
            state=self._state,
            queue_size=queue_size,
            last_stats=self._last_stats,
            last_error=self._last_error,
        )
