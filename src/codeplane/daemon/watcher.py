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


def _is_cross_filesystem(path: Path) -> bool:
    """Detect if path is on a cross-filesystem mount (WSL /mnt/*, network drives, etc.)."""
    resolved = path.resolve()
    path_str = str(resolved)
    # WSL accessing Windows filesystem
    if path_str.startswith("/mnt/") and len(path_str) > 5 and path_str[5].isalpha():
        return True
    # Common network/remote mounts
    return path_str.startswith(("/run/user/", "/media/", "/net/"))


@dataclass
class FileWatcher:
    """
    Async file watcher that filters changes through .cplignore.

    Design:
    - Uses watchfiles for native filesystem watching
    - Falls back to git-based polling for cross-filesystem (WSL /mnt/*)
    - Filters changes through IgnoreChecker before emitting
    - Detects .cplignore changes and reloads filter
    - Notifies callback with batched path changes
    """

    repo_root: Path
    on_change: Callable[[list[Path]], None]
    poll_interval: float = 5.0  # Seconds between git status polls

    _ignore_checker: IgnoreChecker = field(init=False)
    _watch_task: asyncio.Task[None] | None = field(default=None, init=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _is_cross_fs: bool = field(init=False)

    def __post_init__(self) -> None:
        """Initialize ignore checker and detect cross-filesystem."""
        self._ignore_checker = IgnoreChecker(self.repo_root)
        self._is_cross_fs = _is_cross_filesystem(self.repo_root)

    async def start(self) -> None:
        """Start watching for file changes."""
        if self._watch_task is not None:
            return

        self._stop_event.clear()
        if self._is_cross_fs:
            self._watch_task = asyncio.create_task(self._poll_loop())
            logger.info(
                "file_watcher_started",
                repo_root=str(self.repo_root),
                mode="polling",
                interval=self.poll_interval,
            )
        else:
            self._watch_task = asyncio.create_task(self._watch_loop())
            logger.info("file_watcher_started", repo_root=str(self.repo_root), mode="native")

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
        """Main watch loop using watchfiles (native filesystem events)."""
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

    async def _poll_loop(self) -> None:
        """Poll loop using git status (for cross-filesystem where inotify fails)."""
        from codeplane.git import GitOps

        git_ops = GitOps(self.repo_root)
        last_status: set[str] = set()

        while not self._stop_event.is_set():
            try:
                # pygit2 status is fast even on cross-fs
                current_status = set(git_ops.status().keys())

                # Detect new/changed files since last poll
                changed = current_status - last_status
                if changed:
                    relevant_paths: list[Path] = []
                    for path_str in changed:
                        rel_path = Path(path_str)
                        if ".git" in rel_path.parts:
                            continue
                        if rel_path.name == ".cplignore":
                            relevant_paths.append(rel_path)
                            continue
                        if self._ignore_checker.should_ignore(self.repo_root / rel_path):
                            continue
                        relevant_paths.append(rel_path)

                    if relevant_paths:
                        logger.info("changes_detected", count=len(relevant_paths))
                        self.on_change(relevant_paths)

                last_status = current_status

            except Exception as e:
                logger.error("poll_error", error=str(e))

            await asyncio.sleep(self.poll_interval)

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
