"""File watcher using watchfiles for async filesystem monitoring."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from watchfiles import Change, awatch

from codeplane.index._internal.ignore import IgnoreChecker

logger = structlog.get_logger()


@dataclass
class FileWatcher:
    """
    Async file watcher that filters changes through .cplignore.

    Design:
    - Uses watchfiles for cross-platform async watching
    - Filters changes through IgnoreChecker before emitting
    - Detects .cplignore changes and reloads filter
    - Notifies callback with batched path changes
    """

    repo_root: Path
    on_change: Callable[[list[Path]], None]

    _ignore_checker: IgnoreChecker = field(init=False)
    _watch_task: asyncio.Task[None] | None = field(default=None, init=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def __post_init__(self) -> None:
        """Initialize ignore checker."""
        self._ignore_checker = IgnoreChecker(self.repo_root)

    async def start(self) -> None:
        """Start watching for file changes."""
        if self._watch_task is not None:
            return

        self._stop_event.clear()
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info("file_watcher_started", repo_root=str(self.repo_root))

    async def stop(self) -> None:
        """Stop watching for file changes."""
        self._stop_event.set()

        if self._watch_task is not None:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task
            self._watch_task = None

        logger.info("file_watcher_stopped")

    async def _watch_loop(self) -> None:
        """Main watch loop."""
        try:
            async for changes in awatch(
                self.repo_root,
                stop_event=self._stop_event,
                ignore_permission_denied=True,
            ):
                await self._handle_changes(changes)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("watcher_error", error=str(e))

    async def _handle_changes(self, changes: set[tuple[Change, str]]) -> None:
        """Process a batch of file changes."""
        relevant_paths: list[Path] = []

        for change_type, path_str in changes:
            path = Path(path_str)

            # Skip .git directory
            try:
                rel_path = path.relative_to(self.repo_root)
            except ValueError:
                continue

            if ".git" in rel_path.parts:
                continue

            # Check for .cplignore change
            if rel_path.name == ".cplignore":
                logger.info("cplignore_changed", path=str(rel_path))
                # Always include .cplignore changes - reconciler handles reload
                relevant_paths.append(rel_path)
                continue

            # Filter through .cplignore
            if self._ignore_checker.should_ignore(self.repo_root / rel_path):
                logger.debug("path_ignored", path=str(rel_path))
                continue

            relevant_paths.append(rel_path)
            logger.debug(
                "path_changed",
                path=str(rel_path),
                change_type=change_type.name,
            )

        if relevant_paths:
            logger.info("changes_detected", count=len(relevant_paths))
            self.on_change(relevant_paths)
