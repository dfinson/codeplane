"""Tests for MCP files tools (read_files, list_files)."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.files import (
    ListFilesParams,
    RangeParam,
    ReadFilesParams,
    list_files,
    read_files,
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


class TestReadFilesParams:
    """Tests for ReadFilesParams model."""

    def test_paths_required(self):
        """paths is required."""
        with pytest.raises(ValidationError):
            ReadFilesParams()

    def test_paths_accepts_list(self):
        """paths accepts list of strings."""
        params = ReadFilesParams(paths=["a.py", "b.py"])
        assert params.paths == ["a.py", "b.py"]

    def test_ranges_optional(self):
        """ranges defaults to None."""
        params = ReadFilesParams(paths=["a.py"])
        assert params.ranges is None

    def test_ranges_accepts_list(self):
        """ranges accepts list of RangeParam."""
        params = ReadFilesParams(
            paths=["a.py"],
            ranges=[RangeParam(start_line=1, end_line=10)],
        )
        assert len(params.ranges) == 1

    def test_include_metadata_default(self):
        """include_metadata defaults to False."""
        params = ReadFilesParams(paths=["a.py"])
        assert params.include_metadata is False

    def test_include_metadata_true(self):
        """include_metadata can be set True."""
        params = ReadFilesParams(paths=["a.py"], include_metadata=True)
        assert params.include_metadata is True


class TestListFilesParams:
    """Tests for ListFilesParams model."""

    def test_all_defaults(self):
        """All fields have sensible defaults."""
        params = ListFilesParams()
        assert params.path is None
        assert params.pattern is None
        assert params.file_type == "all"
        assert params.recursive is False
        assert params.include_hidden is False
        assert params.include_metadata is False
        assert params.limit == 200

    def test_path_optional(self):
        """path is optional."""
        params = ListFilesParams(path="src")
        assert params.path == "src"

    def test_pattern_accepts_glob(self):
        """pattern accepts glob patterns."""
        params = ListFilesParams(pattern="*.py")
        assert params.pattern == "*.py"

    def test_file_type_literal(self):
        """file_type accepts valid literals."""
        for ft in ["all", "file", "directory"]:
            params = ListFilesParams(file_type=ft)
            assert params.file_type == ft

    def test_file_type_invalid(self):
        """file_type rejects invalid values."""
        with pytest.raises(ValidationError):
            ListFilesParams(file_type="symlink")

    def test_limit_bounds(self):
        """limit is bounded."""
        params = ListFilesParams(limit=1)
        assert params.limit == 1
        params = ListFilesParams(limit=1000)
        assert params.limit == 1000

    def test_limit_minimum(self):
        """limit must be >= 1."""
        with pytest.raises(ValidationError):
            ListFilesParams(limit=0)

    def test_limit_maximum(self):
        """limit must be <= 1000."""
        with pytest.raises(ValidationError):
            ListFilesParams(limit=1001)


class TestReadFilesHandler:
    """Tests for read_files handler."""

    @pytest.mark.asyncio
    async def test_read_single_file(self, mock_context: MagicMock):
        """Reads a single file."""
        mock_context.file_ops.read_files.return_value = MagicMock(
            files=[
                MagicMock(
                    path="test.py",
                    content="print('hello')",
                    language="python",
                    line_count=1,
                    range=None,
                    metadata=None,
                )
            ]
        )

        params = ReadFilesParams(paths=["test.py"])
        result = await read_files(mock_context, params)

        mock_context.file_ops.read_files.assert_called_once()
        assert "files" in result
        assert len(result["files"]) == 1

    @pytest.mark.asyncio
    async def test_read_multiple_files(self, mock_context: MagicMock):
        """Reads multiple files."""
        mock_context.file_ops.read_files.return_value = MagicMock(
            files=[
                MagicMock(
                    path="a.py",
                    content="a",
                    language="python",
                    line_count=1,
                    range=None,
                    metadata=None,
                ),
                MagicMock(
                    path="b.py",
                    content="b",
                    language="python",
                    line_count=1,
                    range=None,
                    metadata=None,
                ),
            ]
        )

        params = ReadFilesParams(paths=["a.py", "b.py"])
        result = await read_files(mock_context, params)

        assert len(result["files"]) == 2

    @pytest.mark.asyncio
    async def test_read_with_ranges(self, mock_context: MagicMock):
        """Reads files with line ranges."""
        mock_context.file_ops.read_files.return_value = MagicMock(
            files=[
                MagicMock(
                    path="test.py",
                    content="line 5\nline 6",
                    language="python",
                    line_count=2,
                    range=[5, 6],
                    metadata=None,
                )
            ]
        )

        params = ReadFilesParams(
            paths=["test.py"],
            ranges=[RangeParam(start_line=5, end_line=6)],
        )
        result = await read_files(mock_context, params)

        assert result["files"][0]["range"] == [5, 6]


class TestListFilesHandler:
    """Tests for list_files handler."""

    @pytest.mark.asyncio
    async def test_list_root_directory(self, mock_context: MagicMock):
        """Lists files in root directory."""
        mock_context.file_ops.list_files.return_value = MagicMock(
            path="",
            entries=[
                MagicMock(name="src", type="directory", metadata=None),
                MagicMock(name="README.md", type="file", metadata=None),
            ],
            total=2,
            truncated=False,
        )

        params = ListFilesParams()
        result = await list_files(mock_context, params)

        assert "entries" in result
        assert len(result["entries"]) == 2

    @pytest.mark.asyncio
    async def test_list_with_pattern(self, mock_context: MagicMock):
        """Lists files matching pattern."""
        mock_context.file_ops.list_files.return_value = MagicMock(
            path="src",
            entries=[
                MagicMock(name="main.py", type="file", metadata=None),
                MagicMock(name="utils.py", type="file", metadata=None),
            ],
            total=2,
            truncated=False,
        )

        params = ListFilesParams(path="src", pattern="*.py")
        result = await list_files(mock_context, params)

        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_list_recursive(self, mock_context: MagicMock):
        """Lists files recursively."""
        mock_context.file_ops.list_files.return_value = MagicMock(
            path="",
            entries=[
                MagicMock(name="a.py", type="file", metadata=None),
                MagicMock(name="src/b.py", type="file", metadata=None),
            ],
            total=2,
            truncated=False,
        )

        params = ListFilesParams(recursive=True)
        await list_files(mock_context, params)

        mock_context.file_ops.list_files.assert_called_once()
        # Check recursive was passed through
        call_kwargs = mock_context.file_ops.list_files.call_args[1]
        assert call_kwargs.get("recursive") is True

    @pytest.mark.asyncio
    async def test_list_directories_only(self, mock_context: MagicMock):
        """Lists only directories."""
        mock_context.file_ops.list_files.return_value = MagicMock(
            path="",
            entries=[
                MagicMock(name="src", type="directory", metadata=None),
                MagicMock(name="tests", type="directory", metadata=None),
            ],
            total=2,
            truncated=False,
        )

        params = ListFilesParams(file_type="directory")
        await list_files(mock_context, params)

        call_kwargs = mock_context.file_ops.list_files.call_args[1]
        assert call_kwargs.get("file_type") == "directory"

    @pytest.mark.asyncio
    async def test_list_truncated(self, mock_context: MagicMock):
        """Reports truncation when limit exceeded."""
        mock_context.file_ops.list_files.return_value = MagicMock(
            path="",
            entries=[MagicMock(name=f"file{i}.py", type="file", metadata=None) for i in range(10)],
            total=100,
            truncated=True,
        )

        params = ListFilesParams(limit=10)
        result = await list_files(mock_context, params)

        assert result["truncated"] is True
        assert result["total"] == 100
