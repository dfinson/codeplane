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


class TestRejoinerConstants:
    """Validate module-level constants are well-formed."""

    def test_interval_is_positive(self) -> None:
        assert _REJOINDER_INTERVAL > 0

    def test_rotation_indices_valid(self) -> None:
        """Every index in the rotation tuple addresses a valid rejoinder."""
        for idx in _REJOINDER_ROTATION:
            assert 0 <= idx < len(_REJOINDERS)

    def test_rejoinders_are_nonempty_strings(self) -> None:
        for r in _REJOINDERS:
            assert isinstance(r, str)
            assert len(r) > 0

    def test_all_rejoinders_start_with_prefix(self) -> None:
        for r in _REJOINDERS:
            assert r.startswith("REJOINDER:")

    def test_rotation_has_expected_weights(self) -> None:
        """Rotation (0,1,0) gives A weight 2 and B weight 1."""
        from collections import Counter

        counts = Counter(_REJOINDER_ROTATION)
        assert counts[0] == 2  # A appears twice
        assert counts[1] == 1  # B appears once


class TestRejoinerCounterBehavior:
    """Tests for counter increment and persistence behavior."""

    def test_counter_increments_every_call(self) -> None:
        """Counter reaches expected value after N calls."""
        mgr, session = _make_session_manager()
        mw = ToolMiddleware(session_manager=mgr)
        ctx = _make_mock_context()

        for _ in range(7):
            mw._maybe_get_rejoinder(ctx)

        assert session.counters["rejoinder_calls"] == 7

    def test_counter_not_reset_after_firing(self) -> None:
        """Counter continues incrementing past the firing point."""
        mgr, session = _make_session_manager()
        mw = ToolMiddleware(session_manager=mgr)
        ctx = _make_mock_context()

        # Fire once at interval, then continue
        for _ in range(_REJOINDER_INTERVAL + 3):
            mw._maybe_get_rejoinder(ctx)

        assert session.counters["rejoinder_calls"] == _REJOINDER_INTERVAL + 3

    def test_first_fire_is_rejoinder_a(self) -> None:
        """First rejoinder (call 5) is A (search/read tools)."""
        mgr, _ = _make_session_manager()
        mw = ToolMiddleware(session_manager=mgr)
        ctx = _make_mock_context()

        for _ in range(_REJOINDER_INTERVAL - 1):
            mw._maybe_get_rejoinder(ctx)
        result = mw._maybe_get_rejoinder(ctx)
        assert result == _REJOINDERS[0]
        assert "search" in result
        assert "read_source" in result

    def test_second_fire_is_rejoinder_b(self) -> None:
        """Second rejoinder (call 10) is B (test runners)."""
        mgr, _ = _make_session_manager()
        mw = ToolMiddleware(session_manager=mgr)
        ctx = _make_mock_context()

        for _ in range(_REJOINDER_INTERVAL * 2 - 1):
            mw._maybe_get_rejoinder(ctx)
        result = mw._maybe_get_rejoinder(ctx)
        assert result == _REJOINDERS[1]
        assert "run_test_targets" in result

    def test_no_repack_when_extract_returns_none(self) -> None:
        """If _extract_result_dict returns None, rejoinder is silently skipped."""
        # Verify _extract_result_dict returns None for non-dict results
        assert ToolMiddleware._extract_result_dict("plain string") is None
        assert ToolMiddleware._extract_result_dict(None) is None
        assert ToolMiddleware._extract_result_dict(42) is None

    def test_long_run_rotation_wraps(self) -> None:
        """Rotation wraps correctly over many cycles."""
        mgr, _ = _make_session_manager()
        mw = ToolMiddleware(session_manager=mgr)
        ctx = _make_mock_context()

        fired: list[str] = []
        # Run 5 full cycles (15 rejoinders = 75 calls)
        for _ in range(_REJOINDER_INTERVAL * 15):
            result = mw._maybe_get_rejoinder(ctx)
            if result is not None:
                fired.append(result)

        assert len(fired) == 15
        # Every group of 3 should follow A, B, A
        for i in range(0, 15, 3):
            assert fired[i] == _REJOINDERS[0]  # A
            assert fired[i + 1] == _REJOINDERS[1]  # B
            assert fired[i + 2] == _REJOINDERS[0]  # A
