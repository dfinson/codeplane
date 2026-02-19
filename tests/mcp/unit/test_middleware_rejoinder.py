"""Tests for periodic tool-preference rejoinders."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastmcp.tools.tool import ToolResult

from codeplane.mcp.gate import PatternMatch, build_pattern_hint
from codeplane.mcp.middleware import (
    _REJOINDER_INTERVAL,
    _REJOINDER_ROTATION,
    _REJOINDERS,
    ToolMiddleware,
)


def _make_mock_context(session_id: str = "test-session") -> MagicMock:
    ctx = MagicMock()
    ctx.fastmcp_context = MagicMock()
    ctx.fastmcp_context.session_id = session_id
    return ctx


def _make_session_manager() -> tuple[MagicMock, MagicMock]:
    session = MagicMock()
    session.counters = {}
    mgr = MagicMock()
    mgr.get_or_create.return_value = session
    return mgr, session


class TestMaybeGetRejoinder:
    """Tests for ToolMiddleware._maybe_get_rejoinder()."""

    def test_returns_none_before_interval(self) -> None:
        """No rejoinder fires for calls 1 through N-1."""
        mgr, _ = _make_session_manager()
        mw = ToolMiddleware(session_manager=mgr)
        ctx = _make_mock_context()
        for _ in range(_REJOINDER_INTERVAL - 1):
            assert mw._maybe_get_rejoinder(ctx) is None

    def test_fires_at_interval(self) -> None:
        """Rejoinder fires on the Nth call."""
        mgr, _ = _make_session_manager()
        mw = ToolMiddleware(session_manager=mgr)
        ctx = _make_mock_context()
        for _ in range(_REJOINDER_INTERVAL - 1):
            mw._maybe_get_rejoinder(ctx)
        result = mw._maybe_get_rejoinder(ctx)
        assert result is not None
        assert result.startswith("REJOINDER:")

    def test_weighted_rotation_order(self) -> None:
        """Rotation follows A, B, A, A, B, A, ... pattern."""
        mgr, _ = _make_session_manager()
        mw = ToolMiddleware(session_manager=mgr)
        ctx = _make_mock_context()

        fired: list[str] = []
        for _ in range(_REJOINDER_INTERVAL * 6):
            result = mw._maybe_get_rejoinder(ctx)
            if result is not None:
                fired.append(result)

        expected = [
            _REJOINDERS[_REJOINDER_ROTATION[i % len(_REJOINDER_ROTATION)]] for i in range(6)
        ]
        assert fired == expected

    def test_counter_is_session_scoped(self) -> None:
        """Different sessions have independent counters."""
        session_a = MagicMock()
        session_a.counters = {}
        session_b = MagicMock()
        session_b.counters = {}

        mgr = MagicMock()
        mgr.get_or_create.side_effect = lambda sid: session_a if sid == "a" else session_b

        mw = ToolMiddleware(session_manager=mgr)
        ctx_a = _make_mock_context("a")
        ctx_b = _make_mock_context("b")

        # Drive session A to the firing threshold
        for _ in range(_REJOINDER_INTERVAL):
            mw._maybe_get_rejoinder(ctx_a)

        # Session B should still be at zero
        assert mw._maybe_get_rejoinder(ctx_b) is None

    def test_returns_none_without_session_manager(self) -> None:
        """No session manager means no rejoinders."""
        mw = ToolMiddleware()
        ctx = _make_mock_context()
        assert mw._maybe_get_rejoinder(ctx) is None

    def test_returns_none_without_fastmcp_context(self) -> None:
        """Missing fastmcp_context is handled gracefully."""
        mgr, _ = _make_session_manager()
        mw = ToolMiddleware(session_manager=mgr)
        ctx = MagicMock()
        ctx.fastmcp_context = None
        assert mw._maybe_get_rejoinder(ctx) is None


class TestRejoindMerging:
    """Tests for rejoinder merging into result dicts."""

    def test_append_to_existing_agentic_hint(self) -> None:
        """Rejoinder appends to (not overwrites) existing agentic_hint."""
        existing_hint = "Full result cached at .codeplane/cache/foo.json"
        result_dict: dict[str, object] = {
            "summary": "ok",
            "agentic_hint": existing_hint,
        }

        rejoinder = _REJOINDERS[0]
        existing = result_dict.get("agentic_hint", "")
        if existing:
            result_dict["agentic_hint"] = str(existing) + "\n\n" + rejoinder
        else:
            result_dict["agentic_hint"] = rejoinder

        hint = str(result_dict["agentic_hint"])
        assert hint.startswith(existing_hint)
        assert "REJOINDER:" in hint
        assert "\n\n" in hint

    def test_set_when_no_existing_hint(self) -> None:
        """Rejoinder is set directly when no prior agentic_hint."""
        result_dict: dict[str, object] = {"summary": "ok"}

        rejoinder = _REJOINDERS[0]
        existing = result_dict.get("agentic_hint", "")
        if existing:
            result_dict["agentic_hint"] = str(existing) + "\n\n" + rejoinder
        else:
            result_dict["agentic_hint"] = rejoinder

        assert result_dict["agentic_hint"] == rejoinder

    def test_coexistence_with_pattern_hint(self) -> None:
        """Both pattern hint and rejoinder appear when both fire."""
        match = PatternMatch(
            pattern_name="phantom_read",
            severity="warn",
            cause="tool_bypass",
            message="You bypassed read_source",
            reason_prompt="How did you get the content?",
            suggested_workflow={"for_reading": "use read_source"},
        )

        result_dict: dict[str, object] = {"results": [], "summary": "done"}

        # Apply pattern hint (same logic as middleware)
        hint_fields = build_pattern_hint(match)
        existing_hint = result_dict.get("agentic_hint")
        if existing_hint:
            hint_fields["agentic_hint"] = str(existing_hint) + "\n\n" + hint_fields["agentic_hint"]
        result_dict.update(hint_fields)

        # Apply rejoinder (same logic as middleware)
        rejoinder = _REJOINDERS[0]
        existing = result_dict.get("agentic_hint", "")
        if existing:
            result_dict["agentic_hint"] = str(existing) + "\n\n" + rejoinder
        else:
            result_dict["agentic_hint"] = rejoinder

        hint = str(result_dict["agentic_hint"])
        assert "PATTERN:" in hint
        assert "REJOINDER:" in hint
        assert result_dict["detected_pattern"] == "phantom_read"

    def test_repack_into_tool_result(self) -> None:
        """Merged dict repacks into ToolResult correctly."""
        result_dict = {
            "summary": "ok",
            "agentic_hint": _REJOINDERS[0],
        }
        tr = ToolResult(structured_content=result_dict)
        assert tr.structured_content is not None
        assert "REJOINDER:" in tr.structured_content["agentic_hint"]
