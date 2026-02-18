"""Tests for mcp/tools/files.py module.

Covers:
- SpanTarget model
- _summarize_read() helper
- _summarize_list() helper
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.files import (
    SpanTarget,
    _summarize_list,
    _summarize_read,
)


class TestSpanTarget:
    """Tests for SpanTarget model."""

    def test_valid_target_with_range(self) -> None:
        """Creates valid target with range."""
        t = SpanTarget(path="src/main.py", start_line=1, end_line=50)
        assert t.path == "src/main.py"
        assert t.start_line == 1
        assert t.end_line == 50

    def test_path_is_required(self) -> None:
        """Path field is required."""
        with pytest.raises(ValidationError):
            SpanTarget(start_line=1, end_line=10)  # type: ignore[call-arg]

    def test_start_line_must_be_positive(self) -> None:
        """Start line must be > 0."""
        with pytest.raises(ValidationError):
            SpanTarget(path="a.py", start_line=0, end_line=10)

    def test_end_line_must_be_positive(self) -> None:
        """End line must be > 0."""
        with pytest.raises(ValidationError):
            SpanTarget(path="a.py", start_line=1, end_line=0)

    def test_end_must_be_gte_start(self) -> None:
        """End line must be >= start line."""
        with pytest.raises(ValidationError):
            SpanTarget(path="a.py", start_line=50, end_line=10)

    def test_extra_fields_forbidden(self) -> None:
        """Extra fields are rejected."""
        with pytest.raises(ValidationError):
            SpanTarget(path="a.py", start_line=1, end_line=10, extra_field="bad")  # type: ignore[call-arg]


class TestSummarizeRead:
    """Tests for _summarize_read helper."""

    def test_empty_files_not_found(self) -> None:
        """Reports files not found when empty."""
        result = _summarize_read([], not_found=2)
        assert "2 file(s) not found" in result

    def test_single_file_no_range(self) -> None:
        """Single file without range."""
        files = [{"path": "src/main.py", "line_count": 100}]
        result = _summarize_read(files)
        assert "1 file" in result
        assert "100 lines" in result

    def test_single_file_with_range(self) -> None:
        """Single file with line range."""
        files = [{"path": "src/main.py", "line_count": 10, "range": [5, 15]}]
        result = _summarize_read(files)
        assert "1 file" in result
        assert "5-15" in result
        assert "10 lines" in result

    def test_multiple_files(self) -> None:
        """Multiple files."""
        files = [
            {"path": "src/a.py", "line_count": 50},
            {"path": "src/b.py", "line_count": 30},
        ]
        result = _summarize_read(files)
        assert "2 files" in result
        assert "80 lines" in result

    def test_multiple_files_with_not_found(self) -> None:
        """Multiple files with some not found."""
        files = [
            {"path": "src/a.py", "line_count": 50},
            {"path": "src/b.py", "line_count": 30},
        ]
        result = _summarize_read(files, not_found=1)
        assert "2 files" in result
        assert "not found" in result


class TestSummarizeList:
    """Tests for _summarize_list helper."""

    def test_repo_root(self) -> None:
        """Lists repo root."""
        result = _summarize_list("", total=10, truncated=False)
        assert "10 entries" in result
        assert "repo root" in result

    def test_with_path(self) -> None:
        """Lists specific path."""
        result = _summarize_list("src/", total=5, truncated=False)
        assert "5 entries" in result
        assert "src/" in result

    def test_truncated(self) -> None:
        """Shows truncation indicator."""
        result = _summarize_list("lib/", total=100, truncated=True)
        assert "100 entries" in result
        assert "truncated" in result
