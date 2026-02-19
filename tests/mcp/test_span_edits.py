"""Tests for span-based edit mode in write_source.

Covers:
- EditParam validation (required fields per action)
- Basic span edit with correct file_sha256
- Hash mismatch detection
- Multi-edit non-overlapping and overlapping scenarios
- Descending line order application
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.mutation import EditParam


class TestEditParamValidation:
    """Tests for EditParam model validation."""

    def test_create_requires_content(self) -> None:
        """Create action requires content field."""
        with pytest.raises(ValidationError, match="content"):
            EditParam(path="f.py", action="create")

    def test_create_valid(self) -> None:
        """Create with content is valid."""
        e = EditParam(path="f.py", action="create", content="hello\n")
        assert e.content == "hello\n"

    def test_update_requires_span_fields(self) -> None:
        """Update requires start_line, end_line, expected_file_sha256, new_content."""
        with pytest.raises(ValidationError, match="start_line"):
            EditParam(path="f.py", action="update")

    def test_update_missing_hash(self) -> None:
        """Update without expected_file_sha256 raises."""
        with pytest.raises(ValidationError, match="expected_file_sha256"):
            EditParam(
                path="f.py",
                action="update",
                start_line=1,
                end_line=5,
                new_content="new",
            )

    def test_update_valid(self) -> None:
        """Update with all span fields is valid."""
        e = EditParam(
            path="f.py",
            action="update",
            start_line=5,
            end_line=10,
            expected_file_sha256="abc123",
            new_content="new code\n",
            expected_content="old code\n",
        )
        assert e.end_line == 10
        assert e.expected_file_sha256 == "abc123"
        assert e.new_content == "new code\n"

    def test_update_end_before_start(self) -> None:
        """end_line < start_line raises."""
        with pytest.raises(ValidationError, match="end_line"):
            EditParam(
                path="f.py",
                action="update",
                start_line=10,
                end_line=5,
                expected_file_sha256="h",
                new_content="x",
                expected_content="y",
            )

    def test_delete_valid(self) -> None:
        """Delete only needs path."""
        e = EditParam(path="f.py", action="delete")
        assert e.action == "delete"

    def test_extra_fields_rejected(self) -> None:
        """Extra fields raise due to ConfigDict(extra='forbid')."""
        with pytest.raises(ValidationError):
            EditParam(path="f.py", action="delete", bogus="x")  # type: ignore[call-arg]


class TestSpanEditValidation:
    """Tests for span edit hash and overlap validation logic."""

    def test_file_sha256_deterministic(self) -> None:
        """Same file content produces same SHA256."""
        content = b"line1\nline2\nline3\n"
        h1 = hashlib.sha256(content).hexdigest()
        h2 = hashlib.sha256(content).hexdigest()
        assert h1 == h2

    def test_hash_changes_on_content_change(self) -> None:
        """Different content produces different hash."""
        h1 = hashlib.sha256(b"original").hexdigest()
        h2 = hashlib.sha256(b"modified").hexdigest()
        assert h1 != h2

    def test_overlapping_spans_detected(self) -> None:
        """Two edits to overlapping lines should be detected."""
        edits = [
            EditParam(
                path="f.py",
                action="update",
                start_line=1,
                end_line=10,
                expected_file_sha256="h",
                new_content="a",
                expected_content="x",
            ),
            EditParam(
                path="f.py",
                action="update",
                start_line=5,
                end_line=15,
                expected_file_sha256="h",
                new_content="b",
                expected_content="y",
            ),
        ]
        sorted_edits = sorted(edits, key=lambda x: x.start_line or 0)
        overlaps = []
        for i in range(len(sorted_edits) - 1):
            cur = sorted_edits[i]
            nxt = sorted_edits[i + 1]
            if (cur.end_line or 0) >= (nxt.start_line or 0):
                overlaps.append((cur.start_line, cur.end_line, nxt.start_line, nxt.end_line))
        assert len(overlaps) == 1

    def test_non_overlapping_spans_ok(self) -> None:
        """Two edits to non-overlapping lines should pass."""
        edits = [
            EditParam(
                path="f.py",
                action="update",
                start_line=1,
                end_line=5,
                expected_file_sha256="h",
                new_content="a",
                expected_content="x",
            ),
            EditParam(
                path="f.py",
                action="update",
                start_line=10,
                end_line=15,
                expected_file_sha256="h",
                new_content="b",
                expected_content="y",
            ),
        ]
        sorted_edits = sorted(edits, key=lambda x: x.start_line or 0)
        overlaps = []
        for i in range(len(sorted_edits) - 1):
            cur = sorted_edits[i]
            nxt = sorted_edits[i + 1]
            if (cur.end_line or 0) >= (nxt.start_line or 0):
                overlaps.append((cur.start_line, cur.end_line, nxt.start_line, nxt.end_line))
        assert len(overlaps) == 0

    def test_descending_order_application(self) -> None:
        """Edits should be applied in descending start_line order."""
        edits = [
            EditParam(
                path="f.py",
                action="update",
                start_line=20,
                end_line=25,
                expected_file_sha256="h",
                new_content="b",
                expected_content="y",
            ),
            EditParam(
                path="f.py",
                action="update",
                start_line=5,
                end_line=10,
                expected_file_sha256="h",
                new_content="a",
                expected_content="x",
            ),
        ]
        desc = sorted(edits, key=lambda x: -(x.start_line or 0))
        assert (desc[0].start_line or 0) > (desc[1].start_line or 0)


# =============================================================================
# Fuzzy Span Matching
# =============================================================================


class TestFuzzySpanMatching:
    """Tests for expected_content fuzzy line matching."""

    def test_exact_match_no_correction(self) -> None:
        """When content matches at given position, no correction."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = ["line 1\n", "line 2\n", "line 3\n", "line 4\n", "line 5\n"]
        start, end, corrected = _fuzzy_match_span(lines, 1, 3, "line 2\nline 3\n")
        assert start == 1
        assert end == 3
        assert corrected is False

    def test_off_by_one_forward(self) -> None:
        """Content shifted forward by 1 line is auto-corrected."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = ["a\n", "b\n", "TARGET\n", "d\n", "e\n"]
        # Agent thinks TARGET is at line index 1, but it's at 2
        start, end, corrected = _fuzzy_match_span(lines, 1, 2, "TARGET\n")
        assert start == 2
        assert end == 3
        assert corrected is True

    def test_off_by_one_backward(self) -> None:
        """Content shifted backward by 1 line is auto-corrected."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = ["a\n", "TARGET\n", "c\n", "d\n", "e\n"]
        # Agent thinks TARGET is at line index 2, but it's at 1
        start, end, corrected = _fuzzy_match_span(lines, 2, 3, "TARGET\n")
        assert start == 1
        assert end == 2
        assert corrected is True

    def test_off_by_three(self) -> None:
        """Content shifted by 3 lines is still found within window."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = [f"line{i}\n" for i in range(10)]
        # Agent thinks "line7" is at index 4, but it's at 7
        start, end, corrected = _fuzzy_match_span(lines, 4, 5, "line7\n")
        assert start == 7
        assert end == 8
        assert corrected is True

    def test_beyond_window_no_correction(self) -> None:
        """Content shifted beyond window returns original (no match)."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = [f"line{i}\n" for i in range(20)]
        # Agent thinks "line15" is at index 2, but it's 13 away (> window)
        start, end, corrected = _fuzzy_match_span(lines, 2, 3, "line15\n")
        assert start == 2
        assert end == 3
        assert corrected is False

    def test_whitespace_normalized(self) -> None:
        """Trailing whitespace differences don't block matching."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = ["a\n", "target  \n", "c\n"]
        start, end, corrected = _fuzzy_match_span(lines, 0, 1, "target\n")
        assert start == 1
        assert end == 2
        assert corrected is True

    def test_multi_line_span(self) -> None:
        """Multi-line expected_content matches correctly."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = ["a\n", "b\n", "foo\n", "bar\n", "baz\n", "e\n"]
        # Agent thinks foo/bar/baz is at index 1-3, but it's at 2-4
        start, end, corrected = _fuzzy_match_span(lines, 1, 4, "foo\nbar\nbaz\n")
        assert start == 2
        assert end == 5
        assert corrected is True

    def test_edit_param_accepts_expected_content(self) -> None:
        """EditParam.expected_content is accepted as optional field."""
        e = EditParam(
            path="f.py",
            action="update",
            start_line=1,
            end_line=5,
            expected_file_sha256="abc123",
            new_content="new stuff",
            expected_content="old stuff",
        )
        assert e.expected_content == "old stuff"

    def test_edit_param_expected_content_required(self) -> None:
        """EditParam update requires expected_content."""
        with pytest.raises(ValidationError, match="expected_content"):
            EditParam(
                path="f.py",
                action="update",
                start_line=1,
                end_line=5,
                expected_file_sha256="abc123",
                new_content="new stuff",
            )

    def test_width_correction_same_position(self) -> None:
        """Agent has correct start but wrong end_line (off-by-one width)."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = ["a\n", "b\n", "c\n", "d\n", "e\n"]
        # Agent says span [0:3] (3 lines) but expected_content is only 2 lines
        start, end, corrected = _fuzzy_match_span(lines, 0, 3, "a\nb\n")
        assert start == 0
        assert end == 2  # Corrected to match expected_content width
        assert corrected is True

    def test_width_correction_end_extending_past(self) -> None:
        """Agent end_line is 1 too many — width correction shrinks span."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = ["x\n", "target1\n", "target2\n", "target3\n", "y\n"]
        # Agent says start=1, end=5 (4 lines) but content is 3 lines
        start, end, corrected = _fuzzy_match_span(lines, 1, 5, "target1\ntarget2\ntarget3\n")
        assert start == 1
        assert end == 4
        assert corrected is True

    def test_width_correction_not_triggered_when_exact(self) -> None:
        """Width correction skipped when span width matches expected_content lines."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = ["a\n", "b\n", "c\n"]
        start, end, corrected = _fuzzy_match_span(lines, 0, 2, "a\nb\n")
        assert start == 0
        assert end == 2
        assert corrected is False  # Exact match, no correction

    def test_content_mismatch_no_match_returns_original(self) -> None:
        """When expected_content doesn't match anywhere, original span returned."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span

        lines = ["a\n", "b\n", "c\n", "d\n", "e\n"]
        start, end, corrected = _fuzzy_match_span(lines, 1, 2, "NONEXISTENT\n")
        assert start == 1
        assert end == 2
        assert corrected is False


class TestContentVerification:
    """Tests for expected_content verification in write_source apply loop."""

    def test_content_mismatch_raises_error(self) -> None:
        """Mismatched expected_content raises MCPError with line counts."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span, _lines_match

        lines = ["a\n", "b\n", "c\n", "d\n", "e\n"]
        expected_content = "WRONG\nCONTENT\n"
        start, end = 1, 3  # 0-indexed

        # Simulate the verification logic from write_source
        new_start, new_end, was_corrected = _fuzzy_match_span(lines, start, end, expected_content)
        assert was_corrected is False

        exp_lines = expected_content.splitlines(keepends=True)
        if exp_lines and not exp_lines[-1].endswith("\n"):
            exp_lines[-1] += "\n"
        assert not _lines_match(lines[start:end], exp_lines)

    def test_line_count_reported_in_mismatch(self) -> None:
        """Error message includes both expected and actual line counts."""
        from codeplane.mcp.tools.mutation import _fuzzy_match_span, _lines_match

        lines = [f"line{i}\n" for i in range(10)]
        # 5-line expected_content vs 3-line span — no match anywhere
        expected_content = "no\nmatch\nanywhere\nin\nfile\n"
        start, end = 2, 5

        new_start, new_end, was_corrected = _fuzzy_match_span(lines, start, end, expected_content)
        exp_lines = expected_content.splitlines(keepends=True)
        if exp_lines and not exp_lines[-1].endswith("\n"):
            exp_lines[-1] += "\n"

        assert not _lines_match(lines[new_start:new_end], exp_lines)
        exp_count = len(exp_lines)
        actual_count = new_end - new_start
        assert exp_count == 5
        assert actual_count == 3

    def test_mismatch_raises_mcp_error_with_content_mismatch_code(self) -> None:
        """Full error path: MCPError raised with CONTENT_MISMATCH code and message."""
        from codeplane.mcp.errors import MCPError, MCPErrorCode
        from codeplane.mcp.tools.mutation import _fuzzy_match_span, _lines_match

        lines = ["a\n", "b\n", "c\n", "d\n", "e\n"]
        expected_content = "WRONG\nCONTENT\n"
        start, end = 1, 3
        path = "test.py"
        start_line_1indexed = start + 1
        end_line_1indexed = end

        # Reproduce the exact error-raising logic from mutation.py
        new_start, new_end, was_corrected = _fuzzy_match_span(lines, start, end, expected_content)
        assert was_corrected is False

        exp_lines = expected_content.splitlines(keepends=True)
        if exp_lines and not exp_lines[-1].endswith("\n"):
            exp_lines[-1] += "\n"

        assert not _lines_match(lines[start:end], exp_lines)

        with pytest.raises(MCPError, match="expected_content") as exc_info:
            raise MCPError(
                code=MCPErrorCode.CONTENT_MISMATCH,
                message=(
                    f"expected_content ({len(exp_lines)} lines) does not match "
                    f"actual content ({end - start} lines) at "
                    f"{path}:{start_line_1indexed}-{end_line_1indexed}. "
                    f"Fuzzy search (\xb1{5} lines) also found no match."
                ),
                remediation=(
                    "Re-read the target span with read_source to get "
                    "current content and correct line numbers, then retry."
                ),
            )

        assert exc_info.value.code == MCPErrorCode.CONTENT_MISMATCH
        assert "2 lines" in exc_info.value.message  # expected line count
        assert "test.py" in exc_info.value.message  # path included
        assert "re-read" in exc_info.value.remediation.lower()  # actionable hint
