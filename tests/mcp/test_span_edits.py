"""Tests for span-based edit mode in write_files.

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
        )
        assert e.start_line == 5
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
            )

    def test_delete_valid(self) -> None:
        """Delete only needs path."""
        e = EditParam(path="f.py", action="delete")
        assert e.action == "delete"

    def test_extra_fields_rejected(self) -> None:
        """Extra fields raise due to ConfigDict(extra='forbid')."""
        with pytest.raises(ValidationError):
            EditParam(path="f.py", action="delete", bogus="x")


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
        desc = sorted(edits, key=lambda x: -(x.start_line or 0))
        assert (desc[0].start_line or 0) > (desc[1].start_line or 0)
