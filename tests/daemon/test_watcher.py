"""Comprehensive tests for daemon watcher module.

Tests cover:
- HARDCODED_DIRS constant
- _get_watchable_paths() function
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
    _create_watch_filter,
    _get_watchable_paths,
    _is_cross_filesystem,
    _summarize_changes_by_type,
)

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


class TestGetWatchablePaths:
    """Tests for _get_watchable_paths function."""

    def test_excludes_hardcoded_dirs(self, tmp_path: Path) -> None:
        """Paths in HARDCODED_DIRS are excluded from watch list."""
        # Create directories including hardcoded ones
        (tmp_path / ".git").mkdir()
        (tmp_path / ".codeplane").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()

        paths = _get_watchable_paths(tmp_path, HARDCODED_DIRS)
        path_names = {p.name for p in paths}

        assert ".git" not in path_names
        assert ".codeplane" not in path_names
        assert "src" in path_names
        assert "tests" in path_names

    def test_includes_files(self, tmp_path: Path) -> None:
        """Files at root level are included."""
        (tmp_path / "README.md").touch()
        (tmp_path / "pyproject.toml").touch()
        (tmp_path / "src").mkdir()

        paths = _get_watchable_paths(tmp_path, HARDCODED_DIRS)
        path_names = {p.name for p in paths}

        assert "README.md" in path_names
        assert "pyproject.toml" in path_names
        assert "src" in path_names

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory returns empty list."""
        paths = _get_watchable_paths(tmp_path, HARDCODED_DIRS)
        assert paths == []

    def test_only_hardcoded_dirs(self, tmp_path: Path) -> None:
        """Directory with only hardcoded dirs returns empty list."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".codeplane").mkdir()

        paths = _get_watchable_paths(tmp_path, HARDCODED_DIRS)
        assert paths == []

    def test_nonexistent_directory_fallback(self) -> None:
        """Nonexistent directory returns fallback (the path itself)."""
        fake_path = Path("/nonexistent/path/that/does/not/exist")
        paths = _get_watchable_paths(fake_path, HARDCODED_DIRS)
        assert paths == [fake_path]

    def test_custom_hardcoded_dirs(self, tmp_path: Path) -> None:
        """Custom hardcoded dirs are excluded."""
        (tmp_path / "keep").mkdir()
        (tmp_path / "exclude").mkdir()

        custom_hardcoded = frozenset({"exclude"})
        paths = _get_watchable_paths(tmp_path, custom_hardcoded)
        path_names = {p.name for p in paths}

        assert "keep" in path_names
        assert "exclude" not in path_names


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


class TestWatchFilterExcludes:
    """Tests for watch filter alignment with PRUNABLE_DIRS."""

    def test_filter_excludes_prunable_dirs(self) -> None:
        """Watch filter excludes all PRUNABLE_DIRS."""
        watch_filter = _create_watch_filter()
        # The filter's _ignore_dirs should contain PRUNABLE_DIRS
        assert PRUNABLE_DIRS.issubset(watch_filter._ignore_dirs)

    def test_filter_excludes_common_dirs(self) -> None:
        """Watch filter excludes common directories to skip."""
        watch_filter = _create_watch_filter()
        # Check specific common directories
        common_excludes = {"node_modules", "__pycache__", ".git", "venv", ".venv"}
        assert common_excludes.issubset(watch_filter._ignore_dirs)


class TestFileWatcherDebouncing:
    """Tests for FileWatcher debouncing behavior."""

    @pytest.fixture
    def watcher(self, tmp_path: Path) -> Generator[FileWatcher, None, None]:
        """Create a FileWatcher for testing."""
        (tmp_path / ".codeplane").mkdir(exist_ok=True)
        changes: list[list[Path]] = []
        watcher = FileWatcher(
            repo_root=tmp_path,
            on_change=lambda p: changes.append(p),
            debounce_window=0.1,
            max_debounce_wait=0.5,
        )
        watcher._changes = changes  # type: ignore[attr-defined]
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

    def test_flush_pending_calls_callback(self, watcher: FileWatcher) -> None:
        """_flush_pending calls on_change with accumulated paths."""
        p1, p2 = Path("a.py"), Path("b.py")
        watcher._queue_change(p1)
        watcher._queue_change(p2)
        watcher._flush_pending()

        changes = getattr(watcher, "_changes", [])
        assert len(changes) == 1
        assert set(changes[0]) == {p1, p2}

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
        changes: list[list[Path]] = []
        watcher = FileWatcher(
            repo_root=tmp_path,
            on_change=lambda p: changes.append(p),
            poll_interval=0.05,
            debounce_window=0.05,
            max_debounce_wait=0.2,
        )
        # Force polling mode
        watcher._is_cross_fs = True
        watcher._changes = changes  # type: ignore[attr-defined]
        yield watcher

    @pytest.mark.asyncio
    async def test_polling_detects_new_file(
        self, polling_watcher: FileWatcher, tmp_path: Path
    ) -> None:
        """Polling mode detects new file creation via mtime comparison.

        Tests the core polling logic by manually calling _scan_mtimes
        and verifying it detects new files.
        """
        # Initial scan sees empty directory (except .codeplane which is pruned)
        initial_mtimes = polling_watcher._scan_mtimes(PRUNABLE_DIRS)
        initial_files = {p.name for p in initial_mtimes}

        # Create a new file
        test_file = tmp_path / "new_file.py"
        test_file.write_text("# new")

        # Rescan should find the new file
        new_mtimes = polling_watcher._scan_mtimes(PRUNABLE_DIRS)
        new_files = {p.name for p in new_mtimes}

        # Verify the new file was detected
        assert "new_file.py" in new_files
        assert "new_file.py" not in initial_files

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

        # Should have detected the change
        changes = getattr(polling_watcher, "_changes", [])
        all_paths = [p for batch in changes for p in batch]
        assert any("existing.py" in str(p) for p in all_paths)


class TestFileWatcherNativeMode:
    """Tests for FileWatcher native (inotify) mode."""

    @pytest.fixture
    def native_watcher(self, tmp_path: Path) -> Generator[FileWatcher, None, None]:
        """Create a FileWatcher in native mode with fast settings."""
        (tmp_path / ".codeplane").mkdir(exist_ok=True)
        changes: list[list[Path]] = []
        watcher = FileWatcher(
            repo_root=tmp_path,
            on_change=lambda p: changes.append(p),
            debounce_window=0.05,
            max_debounce_wait=0.2,
        )
        watcher._is_cross_fs = False
        watcher._changes = changes  # type: ignore[attr-defined]
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


class TestFileWatcherCplignore:
    """Tests for cplignore change handling."""

    @pytest.fixture
    def watcher_with_cplignore(self, tmp_path: Path) -> Generator[FileWatcher, None, None]:
        """Create a watcher with .cplignore file."""
        cplignore_dir = tmp_path / ".codeplane"
        cplignore_dir.mkdir()
        (cplignore_dir / ".cplignore").write_text("*.log\n")

        changes: list[list[Path]] = []
        watcher = FileWatcher(
            repo_root=tmp_path,
            on_change=lambda p: changes.append(p),
        )
        watcher._changes = changes  # type: ignore[attr-defined]
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
