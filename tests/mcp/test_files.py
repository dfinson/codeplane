"""Tests for MCP files tools (read_files, list_files).

Tests the actual exports:
- FileTarget model
- _summarize_read() helper
- _summarize_list() helper

Handler tests use conftest.py fixtures for integration testing.
"""

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.files import (
    FileTarget,
    _summarize_list,
    _summarize_read,
)


class TestFileTarget:
    """Tests for FileTarget model."""

    def test_valid_target(self) -> None:
        """Valid target with path and range."""
        t = FileTarget(path="file.py", start_line=1, end_line=10)
        assert t.path == "file.py"
        assert t.start_line == 1
        assert t.end_line == 10

    def test_target_path_only(self) -> None:
        """Target with path only (no range)."""
        t = FileTarget(path="file.py")
        assert t.path == "file.py"
        assert t.start_line is None
        assert t.end_line is None

    def test_start_line_must_be_positive(self) -> None:
        """start_line must be > 0."""
        with pytest.raises(ValidationError):
            FileTarget(path="a.py", start_line=0, end_line=10)

    def test_end_line_must_be_positive(self) -> None:
        """end_line must be > 0."""
        with pytest.raises(ValidationError):
            FileTarget(path="a.py", start_line=1, end_line=0)

    def test_end_before_start_fails(self) -> None:
        """end_line must be >= start_line."""
        with pytest.raises(ValidationError):
            FileTarget(path="a.py", start_line=10, end_line=5)

    def test_equal_start_end_allowed(self) -> None:
        """Single line range is allowed."""
        t = FileTarget(path="a.py", start_line=5, end_line=5)
        assert t.start_line == t.end_line == 5

    def test_partial_range_rejected(self) -> None:
        """Must set both start_line and end_line or neither."""
        with pytest.raises(ValidationError):
            FileTarget(path="a.py", start_line=1)
        with pytest.raises(ValidationError):
            FileTarget(path="a.py", end_line=10)


class TestSummarizeRead:
    """Tests for _summarize_read helper."""

    def test_single_file(self) -> None:
        """Single file summary."""
        files = [{"path": "test.py", "line_count": 100}]
        assert _summarize_read(files) == "1 file (test.py), 100 lines"

    def test_single_file_with_range(self) -> None:
        """Single file with range."""
        files = [{"path": "test.py", "line_count": 10, "range": [5, 14]}]
        assert _summarize_read(files) == "1 file (test.py:5-14), 10 lines"

    def test_multiple_files(self) -> None:
        """Multiple files listed."""
        files = [
            {"path": "a.py", "line_count": 10},
            {"path": "b.py", "line_count": 20},
        ]
        assert _summarize_read(files) == "2 files (a.py, b.py), 30 lines"

    def test_many_files_truncated(self) -> None:
        """Many files shows +N more."""
        files = [
            {"path": "a.py", "line_count": 10},
            {"path": "b.py", "line_count": 10},
            {"path": "c.py", "line_count": 10},
            {"path": "d.py", "line_count": 10},
        ]
        result = _summarize_read(files)
        assert "+2 more" in result

    def test_not_found(self) -> None:
        """Reports not found count."""
        assert _summarize_read([], not_found=3) == "3 file(s) not found"

    def test_partial_not_found(self) -> None:
        """Reports partial not found with multiple files."""
        files = [
            {"path": "a.py", "line_count": 10},
            {"path": "b.py", "line_count": 20},
        ]
        result = _summarize_read(files, not_found=2)
        assert "2 not found" in result


class TestSummarizeList:
    """Tests for _summarize_list helper."""

    def test_root_directory(self) -> None:
        """Root directory uses 'repo root'."""
        assert _summarize_list("", 10, False) == "10 entries in repo root"

    def test_subdirectory(self) -> None:
        """Subdirectory uses path."""
        assert _summarize_list("src", 5, False) == "5 entries in src"

    def test_truncated(self) -> None:
        """Shows truncated indicator."""
        result = _summarize_list("src", 100, True)
        assert "(truncated)" in result
