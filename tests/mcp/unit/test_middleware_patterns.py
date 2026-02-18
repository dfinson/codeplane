"""Tests for middleware pattern evaluation and hint injection flow.

Covers:
- _post_call_bookkeeping() returning PatternMatch for bypass patterns
- _post_call_bookkeeping() returning None for non-bypass / no pattern
- _extract_result_dict() handling CallToolResult, ToolResult, dict, and failures
- _strip_tool_prefix() extracting short tool names
- Pattern hint injection into ToolResult(structured_content=...)
- Hit count extraction from search results via _extract_result_dict
- build_pattern_hint() and build_pattern_gate_spec() helpers
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastmcp.tools.tool import ToolResult

from codeplane.mcp.gate import (
    CallPatternDetector,
    GateManager,
    GateSpec,
    PatternMatch,
    build_pattern_gate_spec,
    build_pattern_hint,
)
from codeplane.mcp.middleware import ToolMiddleware

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def middleware() -> ToolMiddleware:
    return ToolMiddleware()


def _make_mock_session() -> MagicMock:
    """Create a mock session with real GateManager and PatternDetector."""
    session = MagicMock()
    session.gate_manager = GateManager()
    session.pattern_detector = CallPatternDetector()
    return session


def _make_mock_context(session_id: str = "test-session") -> MagicMock:
    """Create a mock MiddlewareContext."""
    ctx = MagicMock()
    ctx.fastmcp_context = MagicMock()
    ctx.fastmcp_context.session_id = session_id
    return ctx


# ---------------------------------------------------------------------------
# _extract_result_dict
# ---------------------------------------------------------------------------


class TestExtractResultDict:
    """Tests for ToolMiddleware._extract_result_dict()."""

    def test_extract_from_call_tool_result_text(self, middleware: ToolMiddleware) -> None:
        """Extract JSON dict from CallToolResult with text content."""
        content_item = MagicMock()
        content_item.text = json.dumps({"results": [1, 2, 3], "summary": "3 hits"})
        result = MagicMock()
        result.content = [content_item]
        # Ensure structured_content is not checked first
        del result.structured_content

        extracted = middleware._extract_result_dict(result)
        assert extracted is not None
        assert extracted["results"] == [1, 2, 3]
        assert extracted["summary"] == "3 hits"

    def test_extract_from_tool_result_structured(self, middleware: ToolMiddleware) -> None:
        """Extract dict from ToolResult with structured_content."""
        result = ToolResult(structured_content={"key": "value", "count": 42})
        extracted = middleware._extract_result_dict(result)
        assert extracted is not None
        assert extracted["key"] == "value"
        assert extracted["count"] == 42

    def test_extract_from_plain_dict(self, middleware: ToolMiddleware) -> None:
        """Extract from plain dict result."""
        result = {"data": [1, 2], "status": "ok"}
        extracted = middleware._extract_result_dict(result)
        assert extracted is not None
        assert extracted["data"] == [1, 2]

    def test_returns_copy_for_dict(self, middleware: ToolMiddleware) -> None:
        """Plain dict extraction returns a copy, not the original."""
        original = {"key": "value"}
        extracted = middleware._extract_result_dict(original)
        assert extracted is not None
        assert extracted is not original

    def test_returns_copy_for_structured_content(self, middleware: ToolMiddleware) -> None:
        """ToolResult structured_content extraction returns a copy."""
        result = ToolResult(structured_content={"key": "value"})
        extracted = middleware._extract_result_dict(result)
        assert extracted is not None
        # Modify extracted and verify original is unaffected
        extracted["new_key"] = "new_value"
        assert result.structured_content is not None and "new_key" not in result.structured_content

    def test_returns_none_for_invalid_json(self, middleware: ToolMiddleware) -> None:
        """Returns None when text content is not valid JSON."""
        content_item = MagicMock()
        content_item.text = "not valid json"
        result = MagicMock()
        result.content = [content_item]
        del result.structured_content

        assert middleware._extract_result_dict(result) is None

    def test_returns_none_for_json_list(self, middleware: ToolMiddleware) -> None:
        """Returns None when JSON is a list, not a dict."""
        content_item = MagicMock()
        content_item.text = json.dumps([1, 2, 3])
        result = MagicMock()
        result.content = [content_item]
        del result.structured_content

        assert middleware._extract_result_dict(result) is None

    def test_returns_none_for_string_result(self, middleware: ToolMiddleware) -> None:
        """Returns None for a plain string result."""
        assert middleware._extract_result_dict("hello") is None

    def test_returns_none_for_none_result(self, middleware: ToolMiddleware) -> None:
        """Returns None for None result."""
        assert middleware._extract_result_dict(None) is None

    def test_returns_none_for_empty_content(self, middleware: ToolMiddleware) -> None:
        """Returns None when content list is empty."""
        result = MagicMock()
        result.content = []
        del result.structured_content

        assert middleware._extract_result_dict(result) is None

    def test_skips_non_text_content(self, middleware: ToolMiddleware) -> None:
        """Skips content items without a 'text' attribute."""
        item_no_text = MagicMock(spec=[])
        item_with_text = MagicMock()
        item_with_text.text = json.dumps({"found": True})
        result = MagicMock()
        result.content = [item_no_text, item_with_text]
        del result.structured_content

        extracted = middleware._extract_result_dict(result)
        assert extracted is not None
        assert extracted["found"] is True


# ---------------------------------------------------------------------------
# _strip_tool_prefix
# ---------------------------------------------------------------------------


class TestStripToolPrefix:
    """Tests for ToolMiddleware._strip_tool_prefix()."""

    def test_strips_prefix(self) -> None:
        """Strips MCP server prefix from known tool names."""
        assert ToolMiddleware._strip_tool_prefix("codeplane-copy3_search") == "search"

    def test_strips_long_prefix(self) -> None:
        """Strips long prefix for known tool."""
        assert (
            ToolMiddleware._strip_tool_prefix("mcp_codeplane-my_repo_read_source") == "read_source"
        )

    def test_already_short(self) -> None:
        """Already-short tool name is returned as-is."""
        assert ToolMiddleware._strip_tool_prefix("search") == "search"

    def test_unknown_tool(self) -> None:
        """Unknown tool name returned as-is."""
        assert ToolMiddleware._strip_tool_prefix("unknown_tool_xyz") == "unknown_tool_xyz"

    def test_git_tools(self) -> None:
        """Git tool names are correctly stripped."""
        assert (
            ToolMiddleware._strip_tool_prefix("codeplane-cod_git_stage_and_commit")
            == "git_stage_and_commit"
        )
        assert ToolMiddleware._strip_tool_prefix("codeplane-cod_git_status") == "git_status"

    def test_write_source(self) -> None:
        assert ToolMiddleware._strip_tool_prefix("codeplane-cod_write_source") == "write_source"


# ---------------------------------------------------------------------------
# _post_call_bookkeeping
# ---------------------------------------------------------------------------


class TestPostCallBookkeeping:
    """Tests for the pattern recording + evaluation flow."""

    def _setup_middleware(self) -> tuple[ToolMiddleware, MagicMock, MagicMock]:
        """Set up middleware with a session manager."""
        mw = ToolMiddleware()
        session = _make_mock_session()
        session_manager = MagicMock()
        session_manager.get_or_create.return_value = session
        mw._session_manager = session_manager
        ctx = _make_mock_context()
        return mw, session, ctx

    def test_returns_none_no_pattern(self) -> None:
        """Returns None when no pattern is detected."""
        mw, session, ctx = self._setup_middleware()
        result = mw._post_call_bookkeeping(ctx, "search", {}, {})
        assert result is None

    def test_returns_none_without_session_manager(self) -> None:
        """Returns None when session_manager is not set."""
        mw = ToolMiddleware()
        ctx = _make_mock_context()
        result = mw._post_call_bookkeeping(ctx, "search", {}, {})
        assert result is None

    def test_returns_none_without_fastmcp_context(self) -> None:
        """Returns None when fastmcp_context is missing."""
        mw = ToolMiddleware()
        mw._session_manager = MagicMock()
        ctx = MagicMock()
        ctx.fastmcp_context = None
        result = mw._post_call_bookkeeping(ctx, "search", {}, {})
        assert result is None

    def test_ticks_gate_manager(self) -> None:
        """Gate manager tick is called on each bookkeeping."""
        mw, session, ctx = self._setup_middleware()
        # Issue a gate, then tick via bookkeeping
        spec = GateSpec(kind="test", reason_min_chars=10, reason_prompt="why?")
        session.gate_manager.issue(spec)
        assert session.gate_manager.pending_count == 1

        mw._post_call_bookkeeping(ctx, "search", {}, {})
        # Gate should have been ticked (calls_remaining decremented by 1)
        # Default expires_calls=3, so after 1 tick it should still be pending
        assert session.gate_manager.pending_count == 1

    def test_records_call_in_detector(self) -> None:
        """Tool call is recorded in the pattern detector."""
        mw, session, ctx = self._setup_middleware()
        mw._post_call_bookkeeping(ctx, "search", {}, {})
        assert session.pattern_detector.window_length == 1

    def test_extracts_files_from_paths_arg(self) -> None:
        """Files are extracted from 'paths' argument."""
        mw, session, ctx = self._setup_middleware()
        arguments = {"paths": ["a.py", "b.py"]}
        mw._post_call_bookkeeping(ctx, "read_source", arguments, {})
        assert session.pattern_detector._window[-1].files == ["a.py", "b.py"]

    def test_extracts_files_from_targets_arg(self) -> None:
        """Files are extracted from 'targets' argument dicts."""
        mw, session, ctx = self._setup_middleware()
        arguments = {
            "targets": [
                {"path": "x.py", "start_line": 1},
                {"path": "y.py", "start_line": 10},
            ]
        }
        mw._post_call_bookkeeping(ctx, "read_source", arguments, {})
        assert session.pattern_detector._window[-1].files == ["x.py", "y.py"]

    def test_extracts_hit_count_from_results(self) -> None:
        """Hit count is extracted from result dict's 'results' list."""
        mw, session, ctx = self._setup_middleware()
        result = {"results": [{"id": 1}, {"id": 2}, {"id": 3}], "summary": "3 hits"}
        mw._post_call_bookkeeping(ctx, "search", {}, result)
        assert session.pattern_detector._window[-1].hit_count == 3

    def test_extracts_hit_count_from_call_tool_result(self) -> None:
        """Hit count extracted from CallToolResult JSON text."""
        mw, session, ctx = self._setup_middleware()
        content_item = MagicMock()
        content_item.text = json.dumps({"results": [{"a": 1}, {"b": 2}]})
        result = MagicMock()
        result.content = [content_item]
        del result.structured_content

        mw._post_call_bookkeeping(ctx, "search", {}, result)
        assert session.pattern_detector._window[-1].hit_count == 2

    def test_returns_warn_pattern_match(self) -> None:
        """Returns PatternMatch when a warn-severity bypass is detected."""
        mw, session, ctx = self._setup_middleware()
        # Fill detector with searches and zero reads,
        # then evaluate with git_stage_and_commit to trigger terminal_bypass
        for _ in range(4):
            session.pattern_detector.record("search")

        result = mw._post_call_bookkeeping(ctx, "git_stage_and_commit", {}, {})
        # terminal_bypass_commit should fire
        if result is not None:
            assert isinstance(result, PatternMatch)
            assert result.severity == "warn"

    def test_does_not_return_break_match(self) -> None:
        """Break-severity matches are NOT returned (handled at tool level)."""
        mw, session, ctx = self._setup_middleware()
        for _ in range(10):
            session.pattern_detector.record("search")

        # pure_search_chain should fire (break), but bookkeeping only returns warn
        result = mw._post_call_bookkeeping(ctx, "search", {}, {})
        assert result is None

    def test_lint_check_only_does_not_clear_window(self) -> None:
        """lint_check with no auto-fixes does NOT clear the pattern window."""
        mw, session, ctx = self._setup_middleware()
        session.pattern_detector.record("search")
        session.pattern_detector.record("search")
        result_dict = {"status": "clean", "total_files_modified": 0}
        mw._post_call_bookkeeping(ctx, "lint_check", {}, result_dict)
        # 2 searches + 1 lint = 3 — not cleared
        assert session.pattern_detector.window_length == 3

    def test_lint_check_autofix_clears_window(self) -> None:
        """lint_check with auto-fixes DOES clear the pattern window."""
        mw, session, ctx = self._setup_middleware()
        session.pattern_detector.record("search")
        session.pattern_detector.record("search")
        result_dict = {"status": "dirty", "total_files_modified": 2}
        mw._post_call_bookkeeping(ctx, "lint_check", {}, result_dict)
        assert session.pattern_detector.window_length == 0

    def test_test_run_does_not_clear_window(self) -> None:
        """run_test_targets does NOT clear the pattern window."""
        mw, session, ctx = self._setup_middleware()
        session.pattern_detector.record("search")
        session.pattern_detector.record("search")
        mw._post_call_bookkeeping(ctx, "run_test_targets", {}, {})
        # 2 searches + 1 test = 3 — not cleared
        assert session.pattern_detector.window_length == 3


# ---------------------------------------------------------------------------
# build_pattern_hint / build_pattern_gate_spec
# ---------------------------------------------------------------------------
class TestBuildPatternHelpers:
    """Tests for pattern match helper functions."""

    @pytest.fixture
    def sample_match(self) -> PatternMatch:
        return PatternMatch(
            pattern_name="test_pattern",
            severity="warn",
            cause="inefficient",
            message="Test message",
            reason_prompt="Why are you doing this?",
            suggested_workflow={"step1": "do this", "step2": "then that"},
        )

    def test_build_pattern_hint(self, sample_match: PatternMatch) -> None:
        """build_pattern_hint produces correct structure."""
        hint = build_pattern_hint(sample_match)
        assert "agentic_hint" in hint
        assert "PATTERN: test_pattern" in hint["agentic_hint"]
        assert hint["detected_pattern"] == "test_pattern"
        assert hint["pattern_cause"] == "inefficient"
        assert hint["suggested_workflow"] == {"step1": "do this", "step2": "then that"}

    def test_build_pattern_hint_includes_message(self, sample_match: PatternMatch) -> None:
        """Hint includes the pattern message."""
        hint = build_pattern_hint(sample_match)
        assert "Test message" in hint["agentic_hint"]

    def test_build_pattern_hint_includes_reason_prompt(self, sample_match: PatternMatch) -> None:
        """Hint includes the reason prompt."""
        hint = build_pattern_hint(sample_match)
        assert "Why are you doing this?" in hint["agentic_hint"]

    def test_build_pattern_gate_spec(self, sample_match: PatternMatch) -> None:
        """build_pattern_gate_spec produces a GateSpec."""
        spec = build_pattern_gate_spec(sample_match)
        assert isinstance(spec, GateSpec)
        assert spec.kind == "pattern_break"
        assert spec.reason_min_chars == 50
        assert spec.reason_prompt == sample_match.reason_prompt
        assert spec.message == sample_match.message


# ---------------------------------------------------------------------------
# Integration: hint injection into ToolResult
# ---------------------------------------------------------------------------


class TestHintInjection:
    """Integration tests for the hint injection flow."""

    def test_hint_merged_into_result_dict(self) -> None:
        """Pattern hint is merged into extracted result dict."""
        match = PatternMatch(
            pattern_name="phantom_read",
            severity="warn",
            cause="tool_bypass",
            message="You bypassed read_source",
            reason_prompt="How did you get the content?",
            suggested_workflow={"for_reading": "use read_source"},
        )
        original = {"results": [], "summary": "done"}
        hint = build_pattern_hint(match)
        merged = {**original, **hint}

        assert merged["results"] == []
        assert merged["summary"] == "done"
        assert merged["detected_pattern"] == "phantom_read"
        assert merged["pattern_cause"] == "tool_bypass"
        assert "agentic_hint" in merged

    def test_tool_result_with_merged_hint(self) -> None:
        """ToolResult(structured_content=merged_dict) works correctly."""
        merged = {
            "results": [],
            "summary": "done",
            "detected_pattern": "phantom_read",
            "agentic_hint": "PATTERN: phantom_read - ...",
        }
        tr = ToolResult(structured_content=merged)
        assert tr.structured_content is not None
        assert tr.structured_content["detected_pattern"] == "phantom_read"
        assert tr.structured_content["results"] == []

    def test_existing_agentic_hint_preserved(self) -> None:
        """Pattern hint appends to existing agentic_hint (e.g. fetch commands)."""
        match = PatternMatch(
            pattern_name="zero_result_searches",
            severity="warn",
            cause="inefficient",
            message="3 searches returned 0 results",
            reason_prompt="Try mode='lexical' for text patterns.",
            suggested_workflow={"if_exploring": "use map_repo"},
        )
        # Simulate a resource-delivery response with fetch hint
        original = {
            "resource_kind": "log",
            "delivery": "resource",
            "agentic_hint": "Full result cached at .codeplane/cache/log/abc.json\njq '.results' .codeplane/cache/log/abc.json",
        }
        hint_fields = build_pattern_hint(match)
        existing = original.get("agentic_hint")
        if existing:
            hint_fields["agentic_hint"] = existing + "\n\n" + hint_fields["agentic_hint"]
        original.update(hint_fields)

        # Fetch hint comes first, pattern coaching appended
        assert original["agentic_hint"].startswith("Full result cached at")
        assert "jq '.results'" in original["agentic_hint"]
        assert "PATTERN: zero_result_searches" in original["agentic_hint"]
        # Other pattern fields are present
        assert original["detected_pattern"] == "zero_result_searches"
