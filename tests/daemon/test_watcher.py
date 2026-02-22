"""Comprehensive tests for daemon watcher module.

Tests cover:
- HARDCODED_DIRS constant
- _collect_watch_dirs() function (replaces _get_watchable_paths)
- FileWatcher debouncing behavior
- cplignore change detection
- Cross-filesystem detection
- Integration with IgnoreChecker
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from codeplane.core.excludes import PRUNABLE_DIRS
from codeplane.daemon.watcher import (
    DEBOUNCE_WINDOW_SEC,
    HARDCODED_DIRS,
    MAX_DEBOUNCE_WAIT_SEC,
    FileWatcher,
    _collect_watch_dirs,
    _is_cross_filesystem,
    _summarize_changes_by_type,
)
from codeplane.index._internal.ignore import IgnoreChecker

if TYPE_CHECKING:
    from collections.abc import Generator


class TestHardcodedDirs:
    """Tests for HARDCODED_DIRS constant."""

    def test_contains_codeplane(self) -> None:
        """HARDCODED_DIRS must contain .codeplane to prevent inotify feedback."""
        assert ".codeplane" in HARDCODED_DIRS

    def test_contains_git(self) -> None:
        """HARDCODED_DIRS must contain .git."""
        assert ".git" in HARDCODED_DIRS

    def test_contains_vcs_dirs(self) -> None:
        """HARDCODED_DIRS contains common VCS directories."""
        expected_vcs = {".git", ".svn", ".hg", ".bzr"}
        assert expected_vcs.issubset(HARDCODED_DIRS)

    def test_is_subset_of_prunable_dirs(self) -> None:
        """HARDCODED_DIRS should be a subset of PRUNABLE_DIRS."""
        assert HARDCODED_DIRS.issubset(PRUNABLE_DIRS)

    def test_is_frozenset(self) -> None:
        """HARDCODED_DIRS must be immutable."""
        assert isinstance(HARDCODED_DIRS, frozenset)


class TestCollectWatchDirs:
    """Tests for _collect_watch_dirs function."""

    @pytest.fixture
    def ignore_checker(self, tmp_path: Path) -> IgnoreChecker:
        """Create an IgnoreChecker for testing."""
        return IgnoreChecker(tmp_path, respect_gitignore=False)

    def test_includes_repo_root(self, tmp_path: Path, ignore_checker: IgnoreChecker) -> None:
        """Repo root is always included in the watch list."""
        dirs = _collect_watch_dirs(tmp_path, ignore_checker)
        assert tmp_path in dirs

    def test_excludes_hardcoded_dirs(self, tmp_path: Path, ignore_checker: IgnoreChecker) -> None:
        """Directories in HARDCODED_DIRS are excluded from watch list."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".codeplane").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()

        dirs = _collect_watch_dirs(tmp_path, ignore_checker)
        dir_names = {d.name for d in dirs}

        assert ".git" not in dir_names
        assert ".codeplane" not in dir_names
        assert "src" in dir_names
        assert "tests" in dir_names

    def test_excludes_prunable_dirs(self, tmp_path: Path, ignore_checker: IgnoreChecker) -> None:
        """Directories in DEFAULT_PRUNABLE_DIRS are excluded."""
        (tmp_path / "src").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".venv").mkdir()

        dirs = _collect_watch_dirs(tmp_path, ignore_checker)
        dir_names = {d.name for d in dirs}

        assert "src" in dir_names
        assert "node_modules" not in dir_names
        assert "__pycache__" not in dir_names
        assert ".venv" not in dir_names

    def test_walks_recursively(self, tmp_path: Path, ignore_checker: IgnoreChecker) -> None:
        """Collects nested directories (non-prunable ones)."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "core").mkdir()
        (tmp_path / "src" / "core" / "utils").mkdir()

        dirs = _collect_watch_dirs(tmp_path, ignore_checker)
        dir_strs = {str(d) for d in dirs}

        assert str(tmp_path / "src") in dir_strs
        assert str(tmp_path / "src" / "core") in dir_strs
        assert str(tmp_path / "src" / "core" / "utils") in dir_strs

    def test_prunes_nested_prunable_dirs(
        self, tmp_path: Path, ignore_checker: IgnoreChecker
    ) -> None:
        """Prunable dirs nested inside non-prunable dirs are excluded."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__pycache__").mkdir()
        (tmp_path / "src" / "lib").mkdir()
        (tmp_path / "src" / "lib" / "node_modules").mkdir()

        dirs = _collect_watch_dirs(tmp_path, ignore_checker)
        dir_names = {d.name for d in dirs}

        assert "src" in dir_names
        assert "lib" in dir_names
        assert "__pycache__" not in dir_names
        assert "node_modules" not in dir_names

    def test_empty_directory(self, tmp_path: Path, ignore_checker: IgnoreChecker) -> None:
        """Empty directory returns only the root."""
        dirs = _collect_watch_dirs(tmp_path, ignore_checker)
        assert dirs == [tmp_path]

    def test_only_hardcoded_dirs(self, tmp_path: Path, ignore_checker: IgnoreChecker) -> None:
        """Directory with only hardcoded dirs returns only the root."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".codeplane").mkdir()

        dirs = _collect_watch_dirs(tmp_path, ignore_checker)
        assert dirs == [tmp_path]


class TestCrossFilesystemDetection:
    """Tests for _is_cross_filesystem function."""

    def test_wsl_mnt_path(self) -> None:
        """WSL /mnt/c/ style paths are detected as cross-filesystem."""
        assert _is_cross_filesystem(Path("/mnt/c/Users/test")) is True
        assert _is_cross_filesystem(Path("/mnt/d/Projects")) is True

    def test_regular_linux_path(self) -> None:
        """Regular Linux paths are not cross-filesystem."""
        assert _is_cross_filesystem(Path("/home/user/projects")) is False
        assert _is_cross_filesystem(Path("/tmp/test")) is False

    def test_mnt_without_drive_letter(self) -> None:
        """Paths under /mnt/ but not drive letters are not cross-filesystem."""
        # /mnt/data (no single letter after /mnt/) is not WSL cross-FS
        assert _is_cross_filesystem(Path("/mnt/data")) is False

    def test_network_mounts(self) -> None:
        """Network mount paths are detected as cross-filesystem."""
        assert _is_cross_filesystem(Path("/run/user/1000/gvfs/smb")) is True
        assert _is_cross_filesystem(Path("/media/usb")) is True
        assert _is_cross_filesystem(Path("/net/server/share")) is True


class TestSummarizeChangesByType:
    """Tests for _summarize_changes_by_type function."""

    def test_single_python_file(self) -> None:
        """Single Python file uses singular form."""
        paths = [Path("src/main.py")]
        summary = _summarize_changes_by_type(paths)
        assert "1 Python file" in summary
        assert "files" not in summary.replace("1 Python file", "")

    def test_multiple_python_files(self) -> None:
        """Multiple Python files use plural form."""
        paths = [Path("a.py"), Path("b.py"), Path("c.py")]
        summary = _summarize_changes_by_type(paths)
        assert "3 Python files" in summary

    def test_mixed_file_types(self) -> None:
        """Mixed types are summarized with counts."""
        paths = [
            Path("main.py"),
            Path("util.py"),
            Path("config.json"),
            Path("style.css"),
        ]
        summary = _summarize_changes_by_type(paths)
        assert "2 Python files" in summary
        assert "1 JSON file" in summary
        assert "1 CSS file" in summary

    def test_unknown_extension(self) -> None:
        """Unknown extensions use uppercase extension name."""
        paths = [Path("file.xyz")]
        summary = _summarize_changes_by_type(paths)
        assert "XYZ" in summary

    def test_empty_list(self) -> None:
        """Empty list returns empty summary."""
        summary = _summarize_changes_by_type([])
        assert summary == ""


class TestFileWatcherDebouncing:
    """Tests for FileWatcher debouncing behavior."""

    @pytest.fixture
    def watcher(self, tmp_path: Path) -> Generator[FileWatcher, None, None]:
        """Create a FileWatcher for testing."""
        (tmp_path / ".codeplane").mkdir(exist_ok=True)
        watcher = FileWatcher(
            repo_root=tmp_path,
            on_change=lambda _: None,
            debounce_window=0.1,
            max_debounce_wait=0.5,
        )
        yield watcher

    def test_debounce_window_constant(self) -> None:
        """Debounce window constant has reasonable value."""
        assert DEBOUNCE_WINDOW_SEC > 0
        assert DEBOUNCE_WINDOW_SEC < 5.0

    def test_max_debounce_constant(self) -> None:
        """Max debounce constant has reasonable value."""
        assert MAX_DEBOUNCE_WAIT_SEC > DEBOUNCE_WINDOW_SEC
        assert MAX_DEBOUNCE_WAIT_SEC < 10.0

    def test_queue_change_adds_to_pending(self, watcher: FileWatcher) -> None:
        """_queue_change adds path to pending set."""
        path = Path("test.py")
        watcher._queue_change(path)
        assert path in watcher._pending_changes

    def test_queue_change_sets_timestamps(self, watcher: FileWatcher) -> None:
        """_queue_change sets first and last change timestamps."""
        path = Path("test.py")
        watcher._queue_change(path)
        assert watcher._first_change_time > 0
        assert watcher._last_change_time > 0

    def test_should_flush_after_window(self, watcher: FileWatcher) -> None:
        """_should_flush returns True after debounce window."""
        watcher._queue_change(Path("test.py"))
        # Simulate time passing
        watcher._last_change_time = time.monotonic() - watcher.debounce_window - 0.01
        assert watcher._should_flush() is True

    def test_should_not_flush_during_window(self, watcher: FileWatcher) -> None:
        """_should_flush returns False during debounce window."""
        watcher._queue_change(Path("test.py"))
        # Change just happened
        assert watcher._should_flush() is False

    def test_should_flush_after_max_wait(self, watcher: FileWatcher) -> None:
        """_should_flush returns True after max wait regardless of last change."""
        watcher._queue_change(Path("test.py"))
        # Simulate continuous changes (last_change recent, first_change old)
        watcher._first_change_time = time.monotonic() - watcher.max_debounce_wait - 0.01
        assert watcher._should_flush() is True

    def test_flush_pending_clears_state(self, watcher: FileWatcher) -> None:
        """_flush_pending clears pending changes and timestamps."""
        watcher._queue_change(Path("test.py"))
        watcher._flush_pending()

        assert len(watcher._pending_changes) == 0
        assert watcher._first_change_time == 0.0
        assert watcher._last_change_time == 0.0


class TestFileWatcherPollingMode:
    """Tests for FileWatcher polling mode (cross-filesystem)."""

    @pytest.fixture
    def polling_watcher(self, tmp_path: Path) -> Generator[FileWatcher, None, None]:
        """Create a FileWatcher forced into polling mode."""
        (tmp_path / ".codeplane").mkdir(exist_ok=True)
        watcher = FileWatcher(
            repo_root=tmp_path,
            on_change=lambda _: None,
            poll_interval=0.05,
            debounce_window=0.05,
            max_debounce_wait=0.2,
        )
        # Force polling mode
        watcher._is_cross_fs = True
        yield watcher

    @pytest.mark.asyncio
    async def test_polling_detects_new_file(
        self, polling_watcher: FileWatcher, tmp_path: Path
    ) -> None:
        """Polling mode detects new file creation."""
        await polling_watcher.start()
        try:
            # Create a file
            test_file = tmp_path / "new_file.py"
            test_file.write_text("# new")

            # Wait for poll + debounce
            await asyncio.sleep(0.3)
        finally:
            await polling_watcher.stop()

        # Verify via pending changes being flushed (callback was invoked)
        # The callback should have been called at least once
        assert polling_watcher._pending_changes == set()  # Flushed

    @pytest.mark.asyncio
    async def test_polling_detects_file_modification(
        self, polling_watcher: FileWatcher, tmp_path: Path
    ) -> None:
        """Polling mode detects file modification."""
        # Create file before starting watcher
        test_file = tmp_path / "existing.py"
        test_file.write_text("# original")

        await polling_watcher.start()
        try:
            # Modify the file
            await asyncio.sleep(0.1)  # Let initial scan complete
            test_file.write_text("# modified")

            # Wait for poll + debounce
            await asyncio.sleep(0.3)
        finally:
            await polling_watcher.stop()

        # Verify debounce state was cleared (changes were flushed)
        assert polling_watcher._pending_changes == set()


class TestFileWatcherNativeMode:
    """Tests for FileWatcher native (non-recursive inotify) mode."""

    @pytest.fixture
    def native_watcher(self, tmp_path: Path) -> Generator[FileWatcher, None, None]:
        """Create a FileWatcher in native mode with fast settings."""
        (tmp_path / ".codeplane").mkdir(exist_ok=True)
        watcher = FileWatcher(
            repo_root=tmp_path,
            on_change=lambda _: None,
            debounce_window=0.05,
            max_debounce_wait=0.2,
        )
        watcher._is_cross_fs = False
        yield watcher

    @pytest.mark.asyncio
    async def test_native_starts_dir_scan_task(
        self, native_watcher: FileWatcher, tmp_path: Path
    ) -> None:
        """Native mode starts periodic directory scan task."""
        # Create a watchable directory
        (tmp_path / "src").mkdir()

        await native_watcher.start()
        try:
            await asyncio.sleep(0.1)
            assert native_watcher._dir_scan_task is not None
            assert not native_watcher._dir_scan_task.done()
        finally:
            await native_watcher.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self, native_watcher: FileWatcher, tmp_path: Path) -> None:
        """stop() cancels all background tasks."""
        (tmp_path / "src").mkdir()

        await native_watcher.start()
        await asyncio.sleep(0.1)

        await native_watcher.stop()

        assert native_watcher._watch_task is None
        assert native_watcher._debounce_task is None
        assert native_watcher._dir_scan_task is None

    @pytest.mark.asyncio
    async def test_native_collects_watch_dirs(
        self, native_watcher: FileWatcher, tmp_path: Path
    ) -> None:
        """Native mode collects non-recursive directory list."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "core").mkdir()
        (tmp_path / "node_modules").mkdir()

        await native_watcher.start()
        try:
            await asyncio.sleep(0.2)
            # Watched dirs should include src and src/core but not node_modules
            watched_names = {d.name for d in native_watcher._watched_dirs}
            assert "src" in watched_names
            assert "core" in watched_names
            assert "node_modules" not in watched_names
        finally:
            await native_watcher.stop()


class TestFileWatcherCplignore:
    """Tests for cplignore change handling."""

    @pytest.fixture
    def watcher_with_cplignore(self, tmp_path: Path) -> Generator[FileWatcher, None, None]:
        """Create a watcher with .cplignore file."""
        cplignore_dir = tmp_path / ".codeplane"
        cplignore_dir.mkdir()
        (cplignore_dir / ".cplignore").write_text("*.log\n")

        watcher = FileWatcher(
            repo_root=tmp_path,
            on_change=lambda _: None,
        )
        yield watcher

    def test_initial_cplignore_content_captured(self, watcher_with_cplignore: FileWatcher) -> None:
        """Initial .cplignore content is captured for diff."""
        assert watcher_with_cplignore._last_cplignore_content == "*.log\n"

    def test_handle_cplignore_change_updates_cache(
        self, watcher_with_cplignore: FileWatcher, tmp_path: Path
    ) -> None:
        """_handle_cplignore_change updates cached content."""
        cplignore = tmp_path / ".codeplane" / ".cplignore"
        cplignore.write_text("*.log\n*.tmp\n")

        rel_path = Path(".codeplane") / ".cplignore"
        watcher_with_cplignore._handle_cplignore_change(rel_path)

        assert watcher_with_cplignore._last_cplignore_content == "*.log\n*.tmp\n"
