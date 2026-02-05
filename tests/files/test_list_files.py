"""Tests for list_files operation.

Covers:
- Basic directory listing
- Pattern filtering (glob)
- Recursive listing
- Hidden file handling
- Metadata inclusion
- File type filtering
- Limit/truncation
- Edge cases
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.files.ops import FileOps


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository structure."""
    # Create directory structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("# main")
    (tmp_path / "src" / "utils.py").write_text("# utils")
    (tmp_path / "src" / "lib").mkdir()
    (tmp_path / "src" / "lib" / "helper.py").write_text("# helper")

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("# test")
    (tmp_path / "tests" / "conftest.py").write_text("# conftest")

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("# readme")

    # Hidden files
    (tmp_path / ".gitignore").write_text("*.pyc")
    (tmp_path / ".hidden_dir").mkdir()
    (tmp_path / ".hidden_dir" / "secret.txt").write_text("secret")

    # Root files
    (tmp_path / "README.md").write_text("# Project")
    (tmp_path / "pyproject.toml").write_text("[project]")

    return tmp_path


@pytest.fixture
def file_ops(temp_repo: Path) -> FileOps:
    return FileOps(temp_repo)


class TestBasicListing:
    """Test basic directory listing."""

    def test_list_root(self, file_ops: FileOps) -> None:
        """List root directory."""
        result = file_ops.list_files()

        assert result.path == "."
        assert not result.truncated

        # Should have directories and files, no hidden
        names = [e.name for e in result.entries]
        assert "src" in names
        assert "tests" in names
        assert "docs" in names
        assert "README.md" in names
        assert ".gitignore" not in names  # Hidden excluded by default

    def test_list_subdirectory(self, file_ops: FileOps) -> None:
        """List a subdirectory."""
        result = file_ops.list_files("src")

        assert result.path == "src"
        names = [e.name for e in result.entries]
        assert "main.py" in names
        assert "utils.py" in names
        assert "lib" in names

    def test_list_nonexistent_directory(self, file_ops: FileOps) -> None:
        """Nonexistent directory returns empty."""
        result = file_ops.list_files("nonexistent")

        assert result.path == "nonexistent"
        assert result.entries == []
        assert result.total == 0

    def test_directories_sorted_first(self, file_ops: FileOps) -> None:
        """Directories should appear before files."""
        result = file_ops.list_files()

        # Find transition point
        found_file = False
        for entry in result.entries:
            if entry.type == "file":
                found_file = True
            elif entry.type == "directory" and found_file:
                pytest.fail("Directory found after file - sorting broken")


class TestPatternFiltering:
    """Test glob pattern filtering."""

    def test_simple_extension_pattern(self, file_ops: FileOps) -> None:
        """Filter by extension."""
        result = file_ops.list_files("src", pattern="*.py")

        names = [e.name for e in result.entries]
        assert "main.py" in names
        assert "utils.py" in names
        assert "lib" not in names  # Directory excluded

    def test_recursive_pattern(self, file_ops: FileOps) -> None:
        """Recursive glob pattern."""
        result = file_ops.list_files(pattern="**/*.py")

        paths = [e.path for e in result.entries]
        assert any("main.py" in p for p in paths)
        assert any("helper.py" in p for p in paths)
        assert any("test_main.py" in p for p in paths)

    def test_pattern_with_prefix(self, file_ops: FileOps) -> None:
        """Pattern matching file prefix."""
        result = file_ops.list_files("tests", pattern="test_*")

        names = [e.name for e in result.entries]
        assert "test_main.py" in names
        assert "conftest.py" not in names


class TestRecursiveListing:
    """Test recursive directory listing."""

    def test_recursive_all_files(self, file_ops: FileOps) -> None:
        """Recursive listing finds nested files."""
        result = file_ops.list_files(recursive=True, file_type="file")

        paths = [e.path for e in result.entries]
        assert any("src/main.py" in p or "src\\main.py" in p for p in paths)
        assert any("src/lib/helper.py" in p or "src\\lib\\helper.py" in p for p in paths)

    def test_recursive_respects_hidden(self, file_ops: FileOps) -> None:
        """Recursive listing still respects hidden filter."""
        result = file_ops.list_files(recursive=True)

        paths = [e.path for e in result.entries]
        assert not any(".hidden_dir" in p for p in paths)
        assert not any("secret.txt" in p for p in paths)


class TestHiddenFiles:
    """Test hidden file handling."""

    def test_hidden_excluded_by_default(self, file_ops: FileOps) -> None:
        """Hidden files excluded by default."""
        result = file_ops.list_files()

        names = [e.name for e in result.entries]
        assert ".gitignore" not in names
        assert ".hidden_dir" not in names

    def test_include_hidden(self, file_ops: FileOps) -> None:
        """Hidden files included when requested."""
        result = file_ops.list_files(include_hidden=True)

        names = [e.name for e in result.entries]
        assert ".gitignore" in names
        assert ".hidden_dir" in names

    def test_recursive_hidden_directory_contents(self, file_ops: FileOps) -> None:
        """Hidden directory contents included when hidden enabled."""
        result = file_ops.list_files(recursive=True, include_hidden=True)

        paths = [e.path for e in result.entries]
        assert any("secret.txt" in p for p in paths)


class TestMetadata:
    """Test metadata inclusion."""

    def test_no_metadata_by_default(self, file_ops: FileOps) -> None:
        """Metadata not included by default."""
        result = file_ops.list_files("src")

        for entry in result.entries:
            if entry.type == "file":
                assert entry.size is None
                assert entry.modified_at is None

    def test_include_metadata(self, file_ops: FileOps) -> None:
        """Metadata included when requested."""
        result = file_ops.list_files("src", include_metadata=True)

        files = [e for e in result.entries if e.type == "file"]
        assert len(files) > 0

        for entry in files:
            assert entry.size is not None
            assert entry.size >= 0
            assert entry.modified_at is not None

    def test_directories_no_metadata(self, file_ops: FileOps) -> None:
        """Directories don't get metadata (by design)."""
        result = file_ops.list_files(include_metadata=True)

        dirs = [e for e in result.entries if e.type == "directory"]
        assert len(dirs) > 0
        for d in dirs:
            # Directories don't get size or mtime (implementation choice)
            assert d.size is None
            assert d.modified_at is None


class TestFileTypeFilter:
    """Test file type filtering."""

    def test_files_only(self, file_ops: FileOps) -> None:
        """Filter to files only."""
        result = file_ops.list_files(file_type="file")

        for entry in result.entries:
            assert entry.type == "file"

    def test_directories_only(self, file_ops: FileOps) -> None:
        """Filter to directories only."""
        result = file_ops.list_files(file_type="directory")

        for entry in result.entries:
            assert entry.type == "directory"

    def test_all_types(self, file_ops: FileOps) -> None:
        """Default includes both."""
        result = file_ops.list_files(file_type="all")

        types = {e.type for e in result.entries}
        assert "file" in types
        assert "directory" in types


class TestLimitAndTruncation:
    """Test limit and truncation."""

    def test_limit_respected(self, file_ops: FileOps) -> None:
        """Limit caps returned entries."""
        result = file_ops.list_files(recursive=True, limit=3)

        assert len(result.entries) <= 3

    def test_truncated_flag(self, file_ops: FileOps) -> None:
        """Truncated flag set when more entries exist."""
        result = file_ops.list_files(recursive=True, limit=2)

        if result.total > 2:
            assert result.truncated

    def test_total_count_accurate(self, file_ops: FileOps) -> None:
        """Total count reflects all matching, not just returned."""
        result_limited = file_ops.list_files(recursive=True, limit=2)
        result_full = file_ops.list_files(recursive=True, limit=1000)

        assert result_limited.total == result_full.total


class TestEdgeCases:
    """Test edge cases."""

    def test_empty_directory(self, temp_repo: Path) -> None:
        """Empty directory returns empty list."""
        (temp_repo / "empty").mkdir()
        file_ops = FileOps(temp_repo)

        result = file_ops.list_files("empty")

        assert result.entries == []
        assert result.total == 0

    def test_path_with_trailing_slash(self, file_ops: FileOps) -> None:
        """Path with trailing slash handled."""
        result = file_ops.list_files("src/")

        assert result.path == "src"
        assert len(result.entries) > 0

    def test_nested_path(self, file_ops: FileOps) -> None:
        """Nested path works."""
        result = file_ops.list_files("src/lib")

        assert result.path == "src/lib"
        names = [e.name for e in result.entries]
        assert "helper.py" in names

    def test_path_is_file_not_dir(self, file_ops: FileOps) -> None:
        """Path pointing to file returns empty."""
        result = file_ops.list_files("README.md")

        assert result.entries == []
        assert result.total == 0
