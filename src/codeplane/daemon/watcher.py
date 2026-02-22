"""File watcher using watchfiles for async filesystem monitoring.

Design:
- Python walks the repo tree respecting IgnoreChecker pruning tiers
- Builds an explicit list of directories to watch
- Passes them to awatch with recursive=False (one inotify watch per dir)
- Reacts immediately to new directory creation by restarting awatch
- Falls back to mtime polling for cross-filesystem (WSL /mnt/*)

Improved logging (Issues #4, #6):
- Change detection logs summarize by file type with grammatical correctness
- cplignore changes show pattern diff and explain consequence
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from watchfiles import Change, awatch

from codeplane.index._internal.ignore import IgnoreChecker

logger = structlog.get_logger()

# Directories that are NEVER watched, regardless of other settings.
# These are hardcoded because:
# - .codeplane/logs causes inotify feedback loop (writes trigger more watches)
# - VCS dirs (.git, .svn, etc.) have their own change detection
HARDCODED_DIRS: frozenset[str] = frozenset({".git", ".svn", ".hg", ".bzr", ".codeplane"})


def _collect_watch_dirs(
    repo_root: Path,
    ignore_checker: IgnoreChecker,
) -> list[Path]:
    """Walk the repo tree and collect all directories to watch.

    Respects the tiered pruning model:
    - Tier 0 (HARDCODED_DIRS): Always pruned, never watched
    - Tier 1 (DEFAULT_PRUNABLE_DIRS): Pruned unless negated in .cplignore
    - .gitignore patterns: Pruned via IgnoreChecker

    Returns a flat list of directories. The repo_root itself is always included.
    Each directory gets a single non-recursive inotify watch.
    """
    dirs: list[Path] = [repo_root]
    try:
        for dirpath, dirnames, _filenames in os.walk(repo_root):
            # Prune in-place: remove dirs we should skip
            dirnames[:] = [d for d in dirnames if not ignore_checker.should_prune_dir(d)]
            for d in dirnames:
                dirs.append(Path(dirpath) / d)
    except OSError:
        pass
    return dirs


def _is_cross_filesystem(path: Path) -> bool:
    """Detect if path is on a cross-filesystem mount (WSL /mnt/*, network drives, etc.)."""
    resolved = path.resolve()
    path_str = str(resolved)
    # WSL accessing Windows filesystem: /mnt/c/, /mnt/d/, etc.
    # Must be single letter followed by / (not /mnt/data/ which is a regular mount)
    if (
        path_str.startswith("/mnt/")
        and len(path_str) > 6
        and path_str[5].isalpha()
        and path_str[6] == "/"
    ):
        return True
    # Common network/remote mounts
    return path_str.startswith(("/run/user/", "/media/", "/net/"))


def _summarize_changes_by_type(paths: list[Path]) -> str:
    """Summarize file changes by extension/type with grammatical correctness.

    Returns a human-readable summary like:
    - "1 Python file" (singular)
    - "3 Python files" (plural)
    - "2 Python files, 1 config file" (multiple types)
    """
    # Map extensions to human-readable names
    ext_names: dict[str, str] = {
        ".py": "Python",
        ".pyi": "Python stub",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".jsx": "JSX",
        ".tsx": "TSX",
        ".json": "JSON",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".toml": "TOML",
        ".md": "Markdown",
        ".rs": "Rust",
        ".go": "Go",
        ".java": "Java",
        ".kt": "Kotlin",
        ".rb": "Ruby",
        ".css": "CSS",
        ".html": "HTML",
        ".sql": "SQL",
        ".sh": "shell",
    }

    # Count by extension
    ext_counts: Counter[str] = Counter()
    for p in paths:
        ext = p.suffix.lower()
        ext_counts[ext] += 1

    # Build summary parts
    parts: list[str] = []
    for ext, count in ext_counts.most_common(3):  # Top 3 types
        name = ext_names.get(ext, ext.lstrip(".").upper() if ext else "other")
        word = "file" if count == 1 else "files"
        parts.append(f"{count} {name} {word}")

    # Handle remaining types if more than 3
    shown_count = sum(ext_counts[ext] for ext, _ in ext_counts.most_common(3))
    remaining = len(paths) - shown_count
    if remaining > 0:
        word = "other" if remaining == 1 else "others"
        parts.append(f"{remaining} {word}")

    return ", ".join(parts)


# Debouncing configuration
DEBOUNCE_WINDOW_SEC = 0.5  # Sliding window for batching rapid changes
MAX_DEBOUNCE_WAIT_SEC = 2.0  # Maximum wait before forcing flush


@dataclass
class FileWatcher:
    """
    Async file watcher with sliding-window debouncing.

    Design:
    - Python walks repo tree to build explicit directory list
    - Uses watchfiles with recursive=False (one inotify watch per dir)
    - Reacts immediately to new directory creation by restarting awatch
    - Falls back to mtime polling for cross-filesystem (WSL /mnt/*)
    - Implements sliding-window debounce to batch rapid changes
    - Filters changes through IgnoreChecker before emitting
    - Detects .cplignore changes and reloads filter
    - Notifies callback with batched path changes

    Debouncing (Solution A + B combined):
    - Solution A: Sliding window debounce in watcher itself
    - Solution B: BackgroundIndexer also coalesces (defense in depth)
    - Changes are buffered until DEBOUNCE_WINDOW_SEC of quiet time
    - MAX_DEBOUNCE_WAIT_SEC caps maximum delay for rapid fire changes
    """

    repo_root: Path
    on_change: Callable[[list[Path]], None]
    poll_interval: float = 1.0  # Seconds between mtime polls (cross-filesystem)
    debounce_window: float = DEBOUNCE_WINDOW_SEC
    max_debounce_wait: float = MAX_DEBOUNCE_WAIT_SEC

    _ignore_checker: IgnoreChecker = field(init=False)
    _watch_task: asyncio.Task[None] | None = field(default=None, init=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _is_cross_fs: bool = field(init=False)
    # Debouncing state
    _pending_changes: set[Path] = field(default_factory=set, init=False)
    _last_change_time: float = field(default=0.0, init=False)
    _first_change_time: float = field(default=0.0, init=False)
    _debounce_task: asyncio.Task[None] | None = field(default=None, init=False)
    # Track previous cplignore content for diff (Issue #6)
    _last_cplignore_content: str | None = field(default=None, init=False)
    _dir_scan_task: asyncio.Task[None] | None = field(default=None, init=False)
    # Watched directory set for non-recursive mode
    _watched_dirs: set[Path] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        """Initialize ignore checker and detect cross-filesystem."""
        self._ignore_checker = IgnoreChecker(self.repo_root, respect_gitignore=True)
        self._is_cross_fs = _is_cross_filesystem(self.repo_root)
        # Capture initial cplignore content for diff
        cplignore_path = self.repo_root / ".codeplane" / ".cplignore"
        if cplignore_path.exists():
            with contextlib.suppress(OSError):
                self._last_cplignore_content = cplignore_path.read_text()

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
                debounce_window=self.debounce_window,
            )
        else:
            self._watch_task = asyncio.create_task(self._watch_loop())
            # Periodic safety-net scan for directory changes we might have missed
            self._dir_scan_task = asyncio.create_task(self._periodic_dir_scan())
            logger.info(
                "file_watcher_started",
                repo_root=str(self.repo_root),
                mode="native_nonrecursive",
                debounce_window=self.debounce_window,
            )

    async def stop(self) -> None:
        """Stop watching for file changes."""
        self._stop_event.set()

        # Cancel debounce task if pending
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task
            self._debounce_task = None

        # Cancel dir scan task
        if self._dir_scan_task is not None and not self._dir_scan_task.done():
            self._dir_scan_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dir_scan_task
            self._dir_scan_task = None

        # Flush any pending changes before stopping
        if self._pending_changes:
            self._flush_pending()

        if self._watch_task is not None:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self._watch_task, timeout=2.0)
            self._watch_task = None

        logger.info("file_watcher_stopped")

    def _queue_change(self, path: Path) -> None:
        """Queue a change for debounced delivery."""
        now = time.monotonic()

        if not self._pending_changes:
            self._first_change_time = now

        self._pending_changes.add(path)
        self._last_change_time = now

    def _should_flush(self) -> bool:
        """Check if we should flush pending changes."""
        if not self._pending_changes:
            return False

        now = time.monotonic()
        time_since_last = now - self._last_change_time
        time_since_first = now - self._first_change_time

        # Flush if quiet window elapsed OR max wait exceeded
        return time_since_last >= self.debounce_window or time_since_first >= self.max_debounce_wait

    def _flush_pending(self) -> None:
        """Flush pending changes to callback."""
        if not self._pending_changes:
            return

        paths = list(self._pending_changes)
        self._pending_changes.clear()
        self._first_change_time = 0.0
        self._last_change_time = 0.0

        # Log with human-readable summary (Issue #4)
        summary = _summarize_changes_by_type(paths)
        logger.info("changes_detected", count=len(paths), summary=summary)

        self.on_change(paths)

    async def _debounce_flush_loop(self) -> None:
        """Background task that flushes when debounce window elapses."""
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(0.1)  # Check every 100ms

                if self._should_flush():
                    self._flush_pending()
        except asyncio.CancelledError:
            pass

    async def _watch_loop(self) -> None:
        """Main watch loop using watchfiles with non-recursive inotify.

        Python builds the complete directory list, awatch gets recursive=False.
        This prevents notify-rs from recursively traversing into prunable dirs
        (node_modules, .venv, etc.) which causes inotify setup failures and
        silent fallback to CPU-intensive PollWatcher.

        When new directories are created, we detect them via inotify events
        and restart awatch to include them.
        """
        # Start debounce flush task
        self._debounce_task = asyncio.create_task(self._debounce_flush_loop())

        try:
            while not self._stop_event.is_set():
                # Build directory list using Python walk with pruning
                watch_dirs = _collect_watch_dirs(self.repo_root, self._ignore_checker)
                self._watched_dirs = set(watch_dirs)

                if not watch_dirs:
                    logger.warning("no_watchable_dirs", repo_root=str(self.repo_root))
                    return

                logger.info(
                    "watch_dirs_collected",
                    count=len(watch_dirs),
                    repo_root=str(self.repo_root),
                )

                try:
                    async for changes in awatch(
                        *watch_dirs,
                        recursive=False,
                        step=500,  # Check Rust side every 500ms
                        rust_timeout=10_000,
                        stop_event=self._stop_event,
                        ignore_permission_denied=True,
                    ):
                        needs_restart = await self._handle_changes(changes)
                        if needs_restart:
                            logger.info("watcher_restart_requested", reason="new_directories")
                            break  # Break inner loop to re-collect dirs
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self._stop_event.is_set():
                        return
                    logger.error("watcher_error", error=str(e))
                    # Brief backoff before retry
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            if self._debounce_task:
                self._debounce_task.cancel()

    async def _periodic_dir_scan(self) -> None:
        """Periodic safety-net scan for directory changes.

        Primary detection is via inotify events in _handle_changes.
        This scan catches edge cases: directories created during watcher restart,
        symlink changes, or any events that slipped through.
        """
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(30.0)
                current_dirs = set(_collect_watch_dirs(self.repo_root, self._ignore_checker))
                if current_dirs != self._watched_dirs:
                    new_dirs = current_dirs - self._watched_dirs
                    removed_dirs = self._watched_dirs - current_dirs
                    logger.info(
                        "dir_scan_drift_detected",
                        new_count=len(new_dirs),
                        removed_count=len(removed_dirs),
                        new_sample=[
                            str(d.relative_to(self.repo_root)) for d in sorted(new_dirs)[:5]
                        ],
                    )
                    # Signal the watch loop to restart by cancelling it.
                    # The outer while loop in _watch_loop will re-collect dirs.
                    if self._watch_task and not self._watch_task.done():
                        self._watch_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await self._watch_task
                        self._watch_task = asyncio.create_task(self._watch_loop())
        except asyncio.CancelledError:
            pass

    async def _poll_loop(self) -> None:
        """Poll loop using mtime checks (for cross-filesystem where inotify fails).

        Uses os.walk with pruning rather than git status,
        since gitignored files may still be indexed if not in .cplignore.

        Implements sliding-window debounce for burst handling.
        """
        # Track mtimes for all non-cplignored files
        mtimes: dict[Path, float] = {}

        # Initial scan
        mtimes = self._scan_mtimes()

        # Start debounce flush task
        self._debounce_task = asyncio.create_task(self._debounce_flush_loop())

        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(self.poll_interval)

                try:
                    current_mtimes = self._scan_mtimes()

                    # Find changed files
                    for path, mtime in current_mtimes.items():
                        old_mtime = mtimes.get(path)
                        if old_mtime is None or mtime > old_mtime:
                            rel_path = path.relative_to(self.repo_root)
                            # Filter: exclude .git, check cplignore
                            if ".git" not in rel_path.parts and (
                                rel_path.name == ".cplignore"
                                or not self._ignore_checker.should_ignore(self.repo_root / rel_path)
                            ):
                                self._queue_change(rel_path)

                    # Find deleted files
                    for path in mtimes:
                        if path not in current_mtimes:
                            rel_path = path.relative_to(self.repo_root)
                            # Filter: exclude .git, check cplignore
                            if ".git" not in rel_path.parts and (
                                rel_path.name == ".cplignore"
                                or not self._ignore_checker.should_ignore(self.repo_root / rel_path)
                            ):
                                self._queue_change(rel_path)

                    mtimes = current_mtimes

                except Exception as e:
                    logger.error("poll_error", error=str(e))
        finally:
            if self._debounce_task:
                self._debounce_task.cancel()

    def _scan_mtimes(self) -> dict[Path, float]:
        """Scan filesystem for file mtimes, respecting prunable dirs."""
        mtimes: dict[Path, float] = {}
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            # Prune using IgnoreChecker (respects tiers + .cplignore negation)
            dirnames[:] = [d for d in dirnames if not self._ignore_checker.should_prune_dir(d)]

            for filename in filenames:
                file_path = Path(dirpath) / filename
                with contextlib.suppress(OSError):
                    mtimes[file_path] = file_path.stat().st_mtime

        return mtimes

    def _handle_cplignore_change(self, rel_path: Path) -> None:
        """Handle .cplignore change with detailed logging (Issue #6)."""
        cplignore_path = self.repo_root / rel_path

        # Read new content
        new_content: str | None = None
        if cplignore_path.exists():
            with contextlib.suppress(OSError):
                new_content = cplignore_path.read_text()

        # Compute diff stats
        old_patterns = {
            line.strip()
            for line in (self._last_cplignore_content or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        new_patterns = {
            line.strip()
            for line in (new_content or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }

        added = new_patterns - old_patterns
        removed = old_patterns - new_patterns

        # Log with diff summary (Issue #6 Option B)
        diff_parts: list[str] = []
        if added:
            suffix = "s" if len(added) != 1 else ""
            diff_parts.append(f"+{len(added)} pattern{suffix}")
        if removed:
            suffix = "s" if len(removed) != 1 else ""
            diff_parts.append(f"-{len(removed)} pattern{suffix}")
        diff_summary = ", ".join(diff_parts) if diff_parts else "no changes"

        logger.info(
            "cplignore_changed",
            path=str(rel_path),
            diff=diff_summary,
            added_patterns=list(added)[:5] if added else None,  # Sample up to 5
            removed_patterns=list(removed)[:5] if removed else None,
        )

        # Log consequence (Issue #6 Option A)
        logger.info(
            "full_reindex_triggered",
            reason="ignore_patterns_changed",
            patterns_added=len(added),
            patterns_removed=len(removed),
        )

        # Update cached content
        self._last_cplignore_content = new_content

    async def _handle_changes(self, changes: set[tuple[Change, str]]) -> bool:
        """Process a batch of file changes (queue for debouncing).

        Returns True if a watcher restart is needed (new directories detected).
        """
        needs_restart = False

        for change_type, path_str in changes:
            path = Path(path_str)

            # Skip .git directory
            try:
                rel_path = path.relative_to(self.repo_root)
            except ValueError:
                continue

            if ".git" in rel_path.parts:
                continue

            # Detect new directory creation — request watcher restart to add watch
            if change_type == Change.added and path.is_dir():
                if (
                    not self._ignore_checker.should_prune_dir(path.name)
                    and path not in self._watched_dirs
                ):
                    logger.info(
                        "new_directory_detected",
                        path=str(rel_path),
                    )
                    needs_restart = True
                continue  # Directories themselves don't get queued as file changes

            # Check for .cplignore change
            if rel_path.name == ".cplignore":
                self._handle_cplignore_change(rel_path)
                self._queue_change(rel_path)
                continue

            # Filter through .cplignore
            if self._ignore_checker.should_ignore(self.repo_root / rel_path):
                logger.debug("path_ignored", path=str(rel_path))
                continue

            self._queue_change(rel_path)
            logger.debug(
                "path_queued",
                path=str(rel_path),
                change_type=change_type.name,
            )

        return needs_restart
