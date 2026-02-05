"""Tests for read_files operation.

Covers:
- Single file reading
- Multiple file reading
- Line range extraction
- Language detection
- Metadata inclusion
- Error handling
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.files.ops import FileOps, FileResult, ReadFilesResult


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository structure."""
    (tmp_path / "main.py").write_text("line1\nline2\nline3\nline4\nline5\n")
    (tmp_path / "app.js").write_text("const x = 1;\nconst y = 2;\n")
    (tmp_path / "data.json").write_text('{"key": "value"}')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "utils.py").write_text("def foo():\n    pass\n")
    return tmp_path


class TestReadFilesSingle:
    """Tests for reading a single file."""

    def test_read_single_file(self, temp_repo: Path) -> None:
        """Should read a single file."""
        ops = FileOps(temp_repo)
        result = ops.read_files("main.py")

        assert isinstance(result, ReadFilesResult)
        assert len(result.files) == 1
        assert result.files[0].path == "main.py"
        assert "line1" in result.files[0].content

    def test_read_file_detects_language(self, temp_repo: Path) -> None:
        """Should detect language from extension."""
        ops = FileOps(temp_repo)

        py_result = ops.read_files("main.py")
        assert py_result.files[0].language == "python"

        js_result = ops.read_files("app.js")
        assert js_result.files[0].language == "javascript"

        json_result = ops.read_files("data.json")
        assert json_result.files[0].language == "json"

    def test_read_file_counts_lines(self, temp_repo: Path) -> None:
        """Should count lines correctly."""
        ops = FileOps(temp_repo)
        result = ops.read_files("main.py")
        assert result.files[0].line_count == 5

    def test_read_nested_file(self, temp_repo: Path) -> None:
        """Should read file in subdirectory."""
        ops = FileOps(temp_repo)
        result = ops.read_files("src/utils.py")

        assert len(result.files) == 1
        assert result.files[0].path == "src/utils.py"
        assert "def foo" in result.files[0].content


class TestReadFilesMultiple:
    """Tests for reading multiple files."""

    def test_read_multiple_files(self, temp_repo: Path) -> None:
        """Should read multiple files."""
        ops = FileOps(temp_repo)
        result = ops.read_files(["main.py", "app.js"])

        assert len(result.files) == 2
        paths = {f.path for f in result.files}
        assert "main.py" in paths
        assert "app.js" in paths

    def test_skip_missing_files(self, temp_repo: Path) -> None:
        """Should skip files that don't exist."""
        ops = FileOps(temp_repo)
        result = ops.read_files(["main.py", "missing.txt"])

        assert len(result.files) == 1
        assert result.files[0].path == "main.py"

    def test_empty_result_for_all_missing(self, temp_repo: Path) -> None:
        """Should return empty result if all files missing."""
        ops = FileOps(temp_repo)
        result = ops.read_files(["missing1.txt", "missing2.txt"])
        assert len(result.files) == 0


class TestReadFilesRanges:
    """Tests for line range extraction."""

    def test_read_with_range(self, temp_repo: Path) -> None:
        """Should extract line range."""
        ops = FileOps(temp_repo)
        result = ops.read_files("main.py", ranges=[{"path": "main.py", "start": 2, "end": 4}])

        assert len(result.files) == 1
        content = result.files[0].content
        assert "line2" in content
        assert "line3" in content
        assert "line4" in content
        assert "line1" not in content
        assert "line5" not in content

    def test_range_sets_line_count(self, temp_repo: Path) -> None:
        """Should set line_count to range size."""
        ops = FileOps(temp_repo)
        result = ops.read_files("main.py", ranges=[{"path": "main.py", "start": 2, "end": 4}])
        assert result.files[0].line_count == 3  # lines 2, 3, 4

    def test_range_includes_range_tuple(self, temp_repo: Path) -> None:
        """Should include range tuple in result."""
        ops = FileOps(temp_repo)
        result = ops.read_files("main.py", ranges=[{"path": "main.py", "start": 2, "end": 4}])
        assert result.files[0].range == (2, 4)

    def test_range_clamps_to_file_bounds(self, temp_repo: Path) -> None:
        """Should clamp range to file bounds."""
        ops = FileOps(temp_repo)
        result = ops.read_files(
            "main.py",
            ranges=[{"path": "main.py", "start": 1, "end": 100}],  # Beyond file
        )
        # Should read all 5 lines
        assert result.files[0].line_count == 5

    def test_no_range_full_file(self, temp_repo: Path) -> None:
        """Should read full file without range."""
        ops = FileOps(temp_repo)
        result = ops.read_files("main.py")
        assert result.files[0].range is None


class TestReadFilesMetadata:
    """Tests for metadata inclusion."""

    def test_no_metadata_by_default(self, temp_repo: Path) -> None:
        """Should not include metadata by default."""
        ops = FileOps(temp_repo)
        result = ops.read_files("main.py")
        assert result.files[0].metadata is None

    def test_include_metadata(self, temp_repo: Path) -> None:
        """Should include metadata when requested."""
        ops = FileOps(temp_repo)
        result = ops.read_files("main.py", include_metadata=True)

        metadata = result.files[0].metadata
        assert metadata is not None
        assert "size_bytes" in metadata
        assert "modified_at" in metadata
        assert metadata["size_bytes"] > 0


class TestFileResult:
    """Tests for FileResult dataclass."""

    def test_create_file_result(self) -> None:
        """Should create FileResult."""
        result = FileResult(
            path="test.py",
            content="hello",
            language="python",
            line_count=1,
        )
        assert result.path == "test.py"
        assert result.content == "hello"
        assert result.language == "python"
        assert result.line_count == 1
        assert result.range is None
        assert result.metadata is None

    def test_file_result_with_range(self) -> None:
        """Should store range tuple."""
        result = FileResult(
            path="test.py",
            content="hello",
            language="python",
            line_count=1,
            range=(10, 20),
        )
        assert result.range == (10, 20)

    def test_file_result_with_metadata(self) -> None:
        """Should store metadata dict."""
        result = FileResult(
            path="test.py",
            content="hello",
            language="python",
            line_count=1,
            metadata={"size_bytes": 100, "modified_at": 12345},
        )
        assert result.metadata["size_bytes"] == 100  # type: ignore
