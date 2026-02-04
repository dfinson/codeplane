"""Tests for mcp/tools/files.py module.

Covers:
- RangeParam model
- ReadFilesParams model
- ListFilesParams model
- _summarize_read() helper
- _summarize_list() helper
- read_files handler (integration)
- list_files handler (integration)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.files import (
    ListFilesParams,
    RangeParam,
    ReadFilesParams,
    _summarize_list,
    _summarize_read,
)


class TestRangeParam:
    """Tests for RangeParam model."""

    def test_valid_range(self) -> None:
        """Creates valid range."""
        rng = RangeParam(start_line=1, end_line=50)
        assert rng.start_line == 1
        assert rng.end_line == 50

    def test_path_is_optional(self) -> None:
        """Path field is optional."""
        rng = RangeParam(start_line=1, end_line=10)
        assert rng.path is None

    def test_path_can_be_set(self) -> None:
        """Path can be provided."""
        rng = RangeParam(path="file.py", start_line=1, end_line=10)
        assert rng.path == "file.py"

    def test_end_before_start_fails(self) -> None:
        """Raises when end < start."""
        with pytest.raises(ValidationError):
            RangeParam(start_line=50, end_line=10)

    def test_equal_start_end_allowed(self) -> None:
        """Single line range is allowed."""
        rng = RangeParam(start_line=5, end_line=5)
        assert rng.start_line == rng.end_line == 5

    def test_zero_start_fails(self) -> None:
        """Start line must be > 0."""
        with pytest.raises(ValidationError):
            RangeParam(start_line=0, end_line=10)

    def test_negative_line_fails(self) -> None:
        """Negative lines are rejected."""
        with pytest.raises(ValidationError):
            RangeParam(start_line=-1, end_line=10)


class TestReadFilesParams:
    """Tests for ReadFilesParams model."""

    def test_minimal_valid(self) -> None:
        """Minimal valid params."""
        params = ReadFilesParams(paths=["file.py"])
        assert params.paths == ["file.py"]
        assert params.ranges is None
        assert params.include_metadata is False

    def test_multiple_paths(self) -> None:
        """Multiple paths allowed."""
        params = ReadFilesParams(paths=["a.py", "b.py", "c.py"])
        assert len(params.paths) == 3

    def test_with_ranges(self) -> None:
        """Ranges can be provided."""
        params = ReadFilesParams(
            paths=["file.py"],
            ranges=[RangeParam(start_line=1, end_line=10)],
        )
        assert params.ranges is not None
        assert len(params.ranges) == 1

    def test_include_metadata_flag(self) -> None:
        """Include metadata flag."""
        params = ReadFilesParams(paths=["x.py"], include_metadata=True)
        assert params.include_metadata is True


class TestListFilesParams:
    """Tests for ListFilesParams model."""

    def test_defaults(self) -> None:
        """Default values."""
        params = ListFilesParams()
        assert params.path is None
        assert params.pattern is None
        assert params.recursive is False
        assert params.include_hidden is False
        assert params.include_metadata is False
        assert params.file_type == "all"
        assert params.limit == 200

    def test_path_set(self) -> None:
        """Path can be set."""
        params = ListFilesParams(path="src/")
        assert params.path == "src/"

    def test_pattern_set(self) -> None:
        """Pattern can be set."""
        params = ListFilesParams(pattern="*.py")
        assert params.pattern == "*.py"

    def test_recursive_flag(self) -> None:
        """Recursive flag."""
        params = ListFilesParams(recursive=True)
        assert params.recursive is True

    def test_file_type_options(self) -> None:
        """File type options."""
        for ft in ["all", "file", "directory"]:
            params = ListFilesParams(file_type=ft)  # type: ignore[arg-type]
            assert params.file_type == ft

    def test_invalid_file_type(self) -> None:
        """Invalid file type rejected."""
        with pytest.raises(ValidationError):
            ListFilesParams(file_type="invalid")  # type: ignore[arg-type]

    def test_limit_bounds(self) -> None:
        """Limit must be within bounds."""
        # Valid limits
        ListFilesParams(limit=1)
        ListFilesParams(limit=1000)

        # Invalid
        with pytest.raises(ValidationError):
            ListFilesParams(limit=0)

        with pytest.raises(ValidationError):
            ListFilesParams(limit=1001)


class TestSummarizeRead:
    """Tests for _summarize_read helper."""

    def test_single_file(self) -> None:
        """Summary for single file."""
        files = [{"path": "test.py", "line_count": 100}]
        summary = _summarize_read(files)
        assert "1 file" in summary
        assert "test.py" in summary
        assert "100 lines" in summary

    def test_single_file_with_range(self) -> None:
        """Summary for single file with range."""
        files = [{"path": "test.py", "line_count": 50, "range": (10, 60)}]
        summary = _summarize_read(files)
        assert "10-60" in summary

    def test_multiple_files(self) -> None:
        """Summary for multiple files."""
        files = [
            {"path": "a.py", "line_count": 50},
            {"path": "b.py", "line_count": 50},
        ]
        summary = _summarize_read(files)
        assert "2 files" in summary
        assert "a.py" in summary
        assert "b.py" in summary
        assert "100 lines" in summary

    def test_many_files_truncated(self) -> None:
        """Summary truncates many files."""
        files = [
            {"path": "a.py", "line_count": 10},
            {"path": "b.py", "line_count": 10},
            {"path": "c.py", "line_count": 10},
            {"path": "d.py", "line_count": 10},
            {"path": "e.py", "line_count": 10},
        ]
        summary = _summarize_read(files)
        assert "+3 more" in summary

    def test_with_not_found(self) -> None:
        """Summary with not found files."""
        files = [{"path": "a.py", "line_count": 50}, {"path": "b.py", "line_count": 30}]
        summary = _summarize_read(files, not_found=2)
        assert "2 not found" in summary

    def test_all_not_found(self) -> None:
        """Summary when all files not found."""
        summary = _summarize_read([], not_found=3)
        assert "3 file(s) not found" in summary


class TestSummarizeList:
    """Tests for _summarize_list helper."""

    def test_basic_summary(self) -> None:
        """Basic list summary."""
        summary = _summarize_list("src/", 42, False)
        assert "42 entries" in summary
        assert "src/" in summary

    def test_repo_root(self) -> None:
        """Summary for repo root."""
        summary = _summarize_list("", 10, False)
        assert "repo root" in summary

    def test_truncated(self) -> None:
        """Summary when truncated."""
        summary = _summarize_list("src/", 200, True)
        assert "(truncated)" in summary

    def test_not_truncated(self) -> None:
        """Summary when not truncated."""
        summary = _summarize_list("src/", 10, False)
        assert "truncated" not in summary
