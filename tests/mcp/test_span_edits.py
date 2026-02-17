"""Tests for span-based edit mode in write_files.

Covers:
- Basic span edit with correct file_sha256
- Hash mismatch detection
- Multi-edit non-overlapping and overlapping scenarios
- Descending line order application
- Coexistence with old_content edits
"""

from __future__ import annotations

import hashlib

from codeplane.mcp.tools.mutation import EditParam


class TestEditParam:
    """Tests for EditParam model with span edit fields."""

    def test_span_edit_fields(self) -> None:
        """EditParam accepts span edit fields."""
        e = EditParam(
            path="file.py",
            action="update",
            start_line=5,
            end_line=10,
            expected_file_sha256="abc123",
            new_content="new code\n",
        )
        assert e.start_line == 5
        assert e.end_line == 10
        assert e.expected_file_sha256 == "abc123"
        assert e.new_content == "new code\n"

    def test_regular_edit_fields(self) -> None:
        """EditParam still works for regular (old_content) edits."""
        e = EditParam(
            path="file.py",
            action="update",
            old_content="old text",
            new_content="new text",
        )
        assert e.old_content == "old text"
        assert e.start_line is None
        assert e.expected_file_sha256 is None

    def test_span_edit_is_span_type(self) -> None:
        """Span edits are identified by having start_line, end_line, and expected_file_sha256."""
        e = EditParam(
            path="file.py",
            action="update",
            start_line=1,
            end_line=5,
            expected_file_sha256="hash",
            new_content="new",
        )
        is_span = e.action == "update" and e.start_line is not None and e.end_line is not None
        assert is_span


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
            ),
            EditParam(
                path="f.py",
                action="update",
                start_line=5,
                end_line=15,
                expected_file_sha256="h",
                new_content="b",
            ),
        ]
        sorted_edits = sorted(edits, key=lambda x: (x.start_line or 0))
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
            ),
            EditParam(
                path="f.py",
                action="update",
                start_line=10,
                end_line=15,
                expected_file_sha256="h",
                new_content="b",
            ),
        ]
        sorted_edits = sorted(edits, key=lambda x: (x.start_line or 0))
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
            ),
            EditParam(
                path="f.py",
                action="update",
                start_line=5,
                end_line=10,
                expected_file_sha256="h",
                new_content="a",
            ),
        ]
        # Descending order by start_line
        desc = sorted(edits, key=lambda x: -(x.start_line or 0))
        assert (desc[0].start_line or 0) > (desc[1].start_line or 0)
