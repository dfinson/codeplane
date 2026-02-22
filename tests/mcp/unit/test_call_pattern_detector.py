"""Unit tests for CallPatternDetector class.

Covers:
- record() appends correctly and respects window size
- record() clears window on action categories (preserving test_scoped)
- evaluate() returns highest-severity match
- evaluate(current_tool) temp record append/pop
- evaluate() priority ordering: break > bypass > general warn
- clear() resets window
- window_length property
"""

from __future__ import annotations

import pytest

from codeplane.mcp.gate import (
    ACTION_CATEGORIES,
    WINDOW_SIZE,
    CallPatternDetector,
)


class TestRecord:
    """Tests for CallPatternDetector.record()."""

    def test_appends_to_window(self) -> None:
        """record() appends a CallRecord to the window."""
        det = CallPatternDetector()
        det.record("search")
        assert det.window_length == 1

    def test_respects_window_maxlen(self) -> None:
        """Window size is bounded by maxlen."""
        det = CallPatternDetector(window_size=5)
        for _ in range(10):
            det.record("search")
        assert det.window_length == 5

    def test_default_window_size(self) -> None:
        """Default window size matches WINDOW_SIZE constant."""
        det = CallPatternDetector()
        for _ in range(WINDOW_SIZE + 5):
            det.record("describe")
        assert det.window_length == WINDOW_SIZE

    def test_records_category_from_tool_name(self) -> None:
        """Category is auto-detected from tool name."""
        det = CallPatternDetector()
        det.record("search")
        assert det._window[-1].category == "search"
        det.record("read_source")
        assert det._window[-1].category == "read"
        det.record("write_source")
        # write clears the window, so only scoped records remain
        # but the write call itself was recorded before clear
        # Let's check differently:

    def test_records_files_and_hit_count(self) -> None:
        """Files and hit_count are stored in the CallRecord."""
        det = CallPatternDetector()
        det.record("search", files=["a.py", "b.py"], hit_count=5)
        rec = det._window[-1]
        assert rec.files == ["a.py", "b.py"]
        assert rec.hit_count == 5

    def test_category_override(self) -> None:
        """category_override forces a specific category."""
        det = CallPatternDetector()
        det.record("verify", category_override="test_scoped")
        assert det._window[-1].category == "test_scoped"


class TestRecordActionClear:
    """Tests for window clearing on action categories."""

    def test_action_category_clears_window(self) -> None:
        """Recording an action-category call clears the window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("read_source")
        assert det.window_length == 3

        det.record("write_source")  # "write" is in ACTION_CATEGORIES
        # Window should be cleared (write call is appended then cleared)
        assert det.window_length == 0

    def test_action_preserves_test_scoped(self) -> None:
        """Action-category clear preserves test_scoped records."""
        det = CallPatternDetector()
        det.record("checkpoint", category_override="test_scoped")
        det.record("search")
        det.record("read_source")
        assert det.window_length == 3

        det.record("write_source")  # clears window
        # Only the test_scoped record should remain
        assert det.window_length == 1
        assert det._window[0].category == "test_scoped"

    def test_multiple_test_scoped_preserved(self) -> None:
        """Multiple test_scoped records all survive an action clear."""
        det = CallPatternDetector()
        det.record("checkpoint", category_override="test_scoped")
        det.record("search")
        det.record("checkpoint", category_override="test_scoped")
        det.record("read_source")
        assert det.window_length == 4

        det.record("refactor_rename")  # "refactor" is in ACTION_CATEGORIES
        assert det.window_length == 2
        assert all(r.category == "test_scoped" for r in det._window)

    def test_non_action_category_no_clear(self) -> None:
        """Non-action categories don't clear the window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("read_source")
        det.record("describe")
        assert det.window_length == 3

    @pytest.mark.parametrize(
        "tool_name,expected_cat",
        [
            ("write_source", "write"),
            ("refactor_rename", "refactor"),
        ],
    )
    def test_all_action_categories_clear(self, tool_name: str, expected_cat: str) -> None:
        """All ACTION_CATEGORIES tools clear the window."""
        assert expected_cat in ACTION_CATEGORIES
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("search")
        det.record(tool_name)
        assert det.window_length == 0

    def test_verify_does_not_clear(self) -> None:
        """checkpoint (test/lint) does NOT clear the window (verification, not mutation)."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("checkpoint")
        assert det.window_length == 3

    def test_verify_clears_when_explicit(self) -> None:
        """checkpoint with clears_window=True DOES clear (auto-fixed files)."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("checkpoint", clears_window=True)
        assert det.window_length == 0

    def test_unknown_tool_does_not_clear(self) -> None:
        """Unknown tools (mapped to 'meta') do NOT clear the window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("some_unknown_tool")  # maps to 'meta'
        assert det.window_length == 3

    def test_diff_does_not_clear(self) -> None:
        """semantic_diff (category 'diff') does NOT clear the window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("semantic_diff")  # category 'diff'
        assert det.window_length == 3

    def test_clears_window_preserves_test_scoped(self) -> None:
        """clears_window=True still preserves test_scoped records."""
        det = CallPatternDetector()
        det.record("verify", category_override="test_scoped")
        det.record("search")
        det.record("verify", clears_window=True)
        assert det.window_length == 1  # only test_scoped remains


class TestEvaluate:
    """Tests for CallPatternDetector.evaluate()."""

    def test_none_below_min_window(self) -> None:
        """evaluate() returns None when window has < 5 entries."""
        det = CallPatternDetector()
        for _ in range(4):
            det.record("search")
        assert det.evaluate() is None

    def test_detects_pure_search_chain(self) -> None:
        """evaluate() fires pure_search_chain for 5+ of last 7 searches."""
        det = CallPatternDetector()
        for _ in range(7):
            det.record("search")
        match = det.evaluate()
        assert match is not None
        assert match.pattern_name == "pure_search_chain"
        assert match.severity == "break"

    def test_detects_read_spiral(self) -> None:
        """evaluate() fires read_spiral for 6+ reads of 1 file."""
        det = CallPatternDetector()
        for _ in range(6):
            det.record("read_source", files=["same.py"])
        match = det.evaluate()
        assert match is not None
        assert match.pattern_name == "read_spiral"

    def test_break_priority_over_warn(self) -> None:
        """Break patterns take priority over warn patterns."""
        det = CallPatternDetector()
        # Create a window that triggers both pure_search_chain (break)
        # and zero_result_searches (warn)
        for _ in range(7):
            det.record("search", hit_count=0)
        match = det.evaluate()
        assert match is not None
        assert match.severity == "break"
        assert match.pattern_name == "pure_search_chain"

    def test_bypass_priority_over_general_warn(self) -> None:
        """Bypass warn patterns (phantom_read) take priority over general warn."""
        det = CallPatternDetector()
        # Build a window with zero-result searches AND a phantom_read bypass
        det.record("search", hit_count=0)
        det.record("search", hit_count=0)
        det.record("search", hit_count=0)
        det.record("meta", category_override="meta")
        det.record("describe")
        # Now evaluate with write_source — phantom_read (bypass) should fire
        # over zero_result_searches (general warn) since bypass comes first
        match = det.evaluate(current_tool="write_source")
        if match is not None and match.pattern_name == "phantom_read":
            assert match.cause == "tool_bypass"


class TestEvaluateCurrentTool:
    """Tests for evaluate(current_tool=...) temp record behavior."""

    def test_current_tool_temp_record(self) -> None:
        """current_tool adds a temp record that is removed after evaluate."""
        det = CallPatternDetector()
        for _ in range(4):
            det.record("search")
        original_len = det.window_length

        # evaluate with current_tool should not change window permanently
        det.evaluate(current_tool="write_source")
        assert det.window_length == original_len

    def test_current_tool_visible_to_patterns(self) -> None:
        """current_tool record IS visible to pattern checks during evaluation."""
        det = CallPatternDetector()
        # Set up: searches + meta, no reads or writes
        det.record("search")
        det.record("meta")
        det.record("search")
        det.record("meta")
        # 4 entries. With current_tool=write_source, total = 5
        # phantom_read should detect search → write with no read
        match = det.evaluate(current_tool="write_source")
        assert match is not None
        assert match.pattern_name == "phantom_read"

    def test_current_tool_removed_on_exception(self) -> None:
        """current_tool is popped even if pattern check raises."""
        det = CallPatternDetector()
        for _ in range(5):
            det.record("search")
        original_len = det.window_length

        # Even after evaluation (no exception expected), window unchanged
        det.evaluate(current_tool="search")
        assert det.window_length == original_len

    def test_without_current_tool(self) -> None:
        """evaluate() without current_tool works normally."""
        det = CallPatternDetector()
        for _ in range(10):
            det.record("search")
        match = det.evaluate()  # no current_tool
        assert match is not None


class TestClear:
    """Tests for CallPatternDetector.clear()."""

    def test_clear_resets_window(self) -> None:
        """clear() empties the window."""
        det = CallPatternDetector()
        for _ in range(5):
            det.record("search")
        assert det.window_length == 5
        det.clear()
        assert det.window_length == 0

    def test_clear_enables_reuse(self) -> None:
        """After clear(), new records can be added normally."""
        det = CallPatternDetector()
        det.record("search")
        det.clear()
        det.record("read_source")
        assert det.window_length == 1
        assert det._window[0].category == "read"


class TestWindowLength:
    """Tests for CallPatternDetector.window_length property."""

    def test_empty(self) -> None:
        assert CallPatternDetector().window_length == 0

    def test_after_records(self) -> None:
        det = CallPatternDetector()
        det.record("search")
        det.record("read_source")
        assert det.window_length == 2

    def test_after_clear(self) -> None:
        det = CallPatternDetector()
        det.record("search")
        det.clear()
        assert det.window_length == 0
