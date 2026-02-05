"""Tests for MCP files tools (read_files, list_files).

Tests the actual exports:
- RangeParam model
- _summarize_read() helper
- _summarize_list() helper

Handler tests use conftest.py fixtures for integration testing.
"""

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.files import (
    RangeParam,
    _summarize_list,
    _summarize_read,
)


class TestRangeParam:
    """Tests for RangeParam model."""

    def test_valid_range(self):
        """Valid range with start < end."""
        r = RangeParam(start_line=1, end_line=10)
        assert r.start_line == 1
        assert r.end_line == 10
        assert r.path is None

    def test_range_with_path(self):
        """Range can specify associated path."""
        r = RangeParam(path="file.py", start_line=5, end_line=15)
        assert r.path == "file.py"

    def test_start_line_must_be_positive(self):
        """start_line must be > 0."""
        with pytest.raises(ValidationError):
            RangeParam(start_line=0, end_line=10)

    def test_end_line_must_be_positive(self):
        """end_line must be > 0."""
        with pytest.raises(ValidationError):
            RangeParam(start_line=1, end_line=0)

    def test_end_before_start_fails(self):
        """end_line must be >= start_line."""
        with pytest.raises(ValidationError):
            RangeParam(start_line=10, end_line=5)

    def test_equal_start_end_allowed(self):
        """Single line range is allowed."""
        r = RangeParam(start_line=5, end_line=5)
        assert r.start_line == r.end_line == 5


class TestSummarizeRead:
    """Tests for _summarize_read helper."""

    def test_single_file(self):
        """Single file summary."""
        files = [{"path": "test.py", "line_count": 100}]
        assert _summarize_read(files) == "1 file (test.py), 100 lines"

    def test_single_file_with_range(self):
        """Single file with range."""
        files = [{"path": "test.py", "line_count": 10, "range": [5, 14]}]
        assert _summarize_read(files) == "1 file (test.py:5-14), 10 lines"

    def test_multiple_files(self):
        """Multiple files listed."""
        files = [
            {"path": "a.py", "line_count": 10},
            {"path": "b.py", "line_count": 20},
        ]
        assert _summarize_read(files) == "2 files (a.py, b.py), 30 lines"

    def test_many_files_truncated(self):
        """Many files shows +N more."""
        files = [
            {"path": "a.py", "line_count": 10},
            {"path": "b.py", "line_count": 10},
            {"path": "c.py", "line_count": 10},
            {"path": "d.py", "line_count": 10},
        ]
        result = _summarize_read(files)
        assert "+2 more" in result

    def test_not_found(self):
        """Reports not found count."""
        assert _summarize_read([], not_found=3) == "3 file(s) not found"

    def test_partial_not_found(self):
        """Reports partial not found with multiple files."""
        files = [
            {"path": "a.py", "line_count": 10},
            {"path": "b.py", "line_count": 20},
        ]
        result = _summarize_read(files, not_found=2)
        assert "2 not found" in result


class TestSummarizeList:
    """Tests for _summarize_list helper."""

    def test_root_directory(self):
        """Root directory uses 'repo root'."""
        assert _summarize_list("", 10, False) == "10 entries in repo root"

    def test_subdirectory(self):
        """Subdirectory uses path."""
        assert _summarize_list("src", 5, False) == "5 entries in src"

    def test_truncated(self):
        """Shows truncated indicator."""
        result = _summarize_list("src", 100, True)
        assert "(truncated)" in result
