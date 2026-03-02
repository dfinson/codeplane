"""Unit tests for checkpoint pure-logic helpers.

Covers:
- _extract_traceback_locations: regex extraction of (path, line) from tracebacks
- _build_failure_snippets: focused code snippets around failure locations
"""

from __future__ import annotations

import pytest

from codeplane.mcp.tools.checkpoint import (
    _build_failure_snippets,
    _extract_traceback_locations,
)

# ---------------------------------------------------------------------------
# _extract_traceback_locations
# ---------------------------------------------------------------------------


class TestExtractTracebackLocations:
    """Tests for _extract_traceback_locations."""

    def test_none_returns_empty(self) -> None:
        assert _extract_traceback_locations(None) == []

    def test_empty_string_returns_empty(self) -> None:
        assert _extract_traceback_locations("") == []

    def test_no_matches(self) -> None:
        assert _extract_traceback_locations("some random text\nno paths here") == []

    def test_single_location(self) -> None:
        tb = "src/foo.py:42: in test_something"
        result = _extract_traceback_locations(tb)
        assert result == [("src/foo.py", 42)]

    def test_multiple_locations(self) -> None:
        tb = (
            "src/foo.py:10: in test_a\n    x = bar()\nsrc/bar.py:20: in bar\n    raise ValueError\n"
        )
        result = _extract_traceback_locations(tb)
        assert result == [("src/foo.py", 10), ("src/bar.py", 20)]

    def test_duplicate_lines_preserved(self) -> None:
        """Duplicates are kept — dedup is caller's job."""
        tb = "src/a.py:5: in f\nsrc/a.py:5: in g\n"
        result = _extract_traceback_locations(tb)
        assert len(result) == 2
        assert all(loc == ("src/a.py", 5) for loc in result)

    def test_nested_path(self) -> None:
        tb = "tests/mcp/tools/test_edit.py:123: in test_create"
        result = _extract_traceback_locations(tb)
        assert result == [("tests/mcp/tools/test_edit.py", 123)]

    def test_absolute_path_not_matched(self) -> None:
        """Paths with spaces won't match \\S+ — this is expected/okay."""
        tb = "/home/user/my project/src/foo.py:10: in test"
        result = _extract_traceback_locations(tb)
        # "project/src/foo.py" matches because \S+ stops at the space
        assert len(result) == 1
        assert result[0] == ("project/src/foo.py", 10)


# ---------------------------------------------------------------------------
# _build_failure_snippets
# ---------------------------------------------------------------------------


FILE_A = """\
import os
import sys

def foo():
    x = 1
    y = 2
    return x + y

def bar():
    return foo() * 2

class Widget:
    def __init__(self):
        self.value = 0

    def update(self, n):
        self.value += n
        return self.value

    def reset(self):
        self.value = 0
"""

FILE_B = """\
from a import foo

def test_foo():
    assert foo() == 3

def test_bar():
    assert foo() == 99
"""


class TestBuildFailureSnippets:
    """Tests for _build_failure_snippets."""

    def test_empty_failure_list(self) -> None:
        result = _build_failure_snippets([], {"a.py": FILE_A})
        assert result == {}

    def test_file_not_in_contents(self) -> None:
        """Failures referencing unknown files are silently skipped."""
        failures = [{"name": "test_x", "path": "missing.py", "line": 5, "message": "fail"}]
        result = _build_failure_snippets(failures, {"a.py": FILE_A})
        assert result == {}

    def test_single_failure_includes_marker(self) -> None:
        failures = [{"name": "test_foo", "path": "a.py", "line": 5, "message": "boom"}]
        result = _build_failure_snippets(failures, {"a.py": FILE_A}, context_lines=2)
        assert "a.py" in result
        snippet = result["a.py"]
        # Line 5 should have > marker
        assert "   5 >|" in snippet
        # Surrounding lines should have space marker
        assert "   3  |" in snippet
        assert "   7  |" in snippet

    def test_context_lines_respected(self) -> None:
        failures = [{"name": "test_foo", "path": "a.py", "line": 10, "message": "fail"}]
        result = _build_failure_snippets(failures, {"a.py": FILE_A}, context_lines=3)
        snippet = result["a.py"]
        lines = snippet.splitlines()
        # With context_lines=3, we get lines 7-13 (line 10 ± 3)
        line_numbers = [
            int(ln.split("|")[0].strip().rstrip(">").strip()) for ln in lines if "|" in ln
        ]
        assert min(line_numbers) == 7
        assert max(line_numbers) == 13

    def test_adjacent_ranges_merged(self) -> None:
        """Failures close together should produce one contiguous snippet."""
        failures = [
            {"name": "t1", "path": "a.py", "line": 5, "message": "f"},
            {"name": "t2", "path": "a.py", "line": 7, "message": "f"},
        ]
        result = _build_failure_snippets(failures, {"a.py": FILE_A}, context_lines=2)
        snippet = result["a.py"]
        # Should NOT contain "..." divider since ranges overlap
        assert "..." not in snippet
        # Both lines should have markers
        assert "   5 >|" in snippet
        assert "   7 >|" in snippet

    def test_distant_ranges_separated(self) -> None:
        """Failures far apart should have ... divider between ranges."""
        failures = [
            {"name": "t1", "path": "a.py", "line": 1, "message": "f"},
            {"name": "t2", "path": "a.py", "line": 20, "message": "f"},
        ]
        result = _build_failure_snippets(failures, {"a.py": FILE_A}, context_lines=2)
        snippet = result["a.py"]
        assert "..." in snippet

    def test_multiple_files(self) -> None:
        failures = [
            {"name": "t1", "path": "a.py", "line": 5, "message": "f"},
            {"name": "t2", "path": "b.py", "line": 4, "message": "f"},
        ]
        result = _build_failure_snippets(
            failures, {"a.py": FILE_A, "b.py": FILE_B}, context_lines=1
        )
        assert "a.py" in result
        assert "b.py" in result

    def test_traceback_locations_included(self) -> None:
        """Traceback paths in file_contents also get snippets."""
        failures = [
            {
                "name": "test_bar",
                "path": "b.py",
                "line": 7,
                "message": "assert 3 == 99",
                "traceback": "a.py:10: in bar\n    return foo() * 2\n",
            },
        ]
        result = _build_failure_snippets(
            failures, {"a.py": FILE_A, "b.py": FILE_B}, context_lines=2
        )
        # Both files should have snippets
        assert "b.py" in result
        assert "a.py" in result
        # a.py snippet should mark line 10
        assert "  10 >|" in result["a.py"]

    def test_clamp_to_file_boundaries(self) -> None:
        """Context should not go below line 1 or above file length."""
        failures = [{"name": "t", "path": "b.py", "line": 1, "message": "f"}]
        result = _build_failure_snippets(failures, {"b.py": FILE_B}, context_lines=5)
        snippet = result["b.py"]
        line_numbers = [
            int(ln.split("|")[0].strip().rstrip(">").strip())
            for ln in snippet.splitlines()
            if "|" in ln
        ]
        assert min(line_numbers) == 1
        # File B has 7 lines, so max should be clamped to 6 (line 1 + 5)
        assert max(line_numbers) <= len(FILE_B.splitlines())

    def test_failure_without_line_skipped(self) -> None:
        """Failures with no line number are skipped (only traceback may add them)."""
        failures = [{"name": "t", "path": "a.py", "line": None, "message": "f"}]
        result = _build_failure_snippets(failures, {"a.py": FILE_A}, context_lines=2)
        assert result == {}

    @pytest.mark.parametrize("context", [0, 1])
    def test_minimal_context(self, context: int) -> None:
        """Even with tiny context, the failure line itself appears."""
        failures = [{"name": "t", "path": "a.py", "line": 5, "message": "f"}]
        result = _build_failure_snippets(failures, {"a.py": FILE_A}, context_lines=context)
        assert "a.py" in result
        assert "   5 >|" in result["a.py"]
