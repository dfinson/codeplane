"""Tests for read_source and read_file_full tools."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from codeplane.mcp.tools.files import (
    SpanTarget,
    StructuralTarget,
    _compute_file_sha256,
    _summarize_read,
)

# =============================================================================
# SpanTarget / StructuralTarget Validation
# =============================================================================


class TestSpanTargetValidation:
    """SpanTarget model validation."""

    def test_valid_range(self) -> None:
        """Valid span range accepted."""
        t = SpanTarget(path="a.py", start_line=1, end_line=10)
        assert t.start_line == 1
        assert t.end_line == 10

    def test_end_before_start(self) -> None:
        """end_line < start_line raises."""
        with pytest.raises(ValueError, match="end_line"):
            SpanTarget(path="a.py", start_line=10, end_line=5)

    def test_zero_start_line(self) -> None:
        """start_line must be > 0."""
        with pytest.raises(ValueError):
            SpanTarget(path="a.py", start_line=0, end_line=5)

    def test_single_line(self) -> None:
        """Single-line span (start==end) is valid."""
        t = SpanTarget(path="a.py", start_line=5, end_line=5)
        assert t.start_line == t.end_line


class TestStructuralTarget:
    """StructuralTarget model validation."""

    def test_default_unit(self) -> None:
        """Default unit is function."""
        t = StructuralTarget(path="a.py", symbol_id="my_func")
        assert t.unit == "function"

    def test_all_units(self) -> None:
        """All valid units accepted."""
        for unit in ("function", "class", "signature", "docstring"):
            t = StructuralTarget(path="a.py", symbol_id="sym", unit=unit)
            assert t.unit == unit


# =============================================================================
# File SHA256 computation
# =============================================================================


class TestFileSha256:
    """File SHA256 helper."""

    def test_deterministic(self, tmp_path: Path) -> None:
        """Same file gives same hash every time."""
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        h1 = _compute_file_sha256(f)
        h2 = _compute_file_sha256(f)
        assert h1 == h2

    def test_matches_hashlib(self, tmp_path: Path) -> None:
        """Hash matches direct hashlib computation."""
        f = tmp_path / "test.txt"
        content = "hello world\n"
        f.write_text(content)
        expected = hashlib.sha256(content.encode()).hexdigest()
        assert _compute_file_sha256(f) == expected

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        """Different content yields different hashes."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("aaa")
        f2.write_text("bbb")
        assert _compute_file_sha256(f1) != _compute_file_sha256(f2)


# =============================================================================
# Summary helper
# =============================================================================


class TestSummarizeRead:
    """Read summary formatting."""

    def test_single_file(self) -> None:
        """Single file summary includes path and line count."""
        files = [{"path": "src/main.py", "line_count": 50}]
        s = _summarize_read(files)
        assert "1 file" in s
        assert "50 lines" in s

    def test_multiple_files(self) -> None:
        """Multiple files shows count."""
        files = [
            {"path": "a.py", "line_count": 10},
            {"path": "b.py", "line_count": 20},
        ]
        s = _summarize_read(files)
        assert "2 files" in s

    def test_not_found_only(self) -> None:
        """All files missing."""
        s = _summarize_read([], not_found=3)
        assert "not found" in s

    def test_single_file_with_range(self) -> None:
        """Single file with range shows line range."""
        files = [{"path": "a.py", "line_count": 10, "range": [5, 15]}]
        s = _summarize_read(files)
        assert "5-15" in s
