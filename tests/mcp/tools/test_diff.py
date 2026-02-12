"""Integration tests for semantic diff MCP tool.

Tests cover:
- Agentic hint generation
- Result serialization
"""

from __future__ import annotations

from typing import Any

from codeplane.index._internal.diff.models import (
    ImpactInfo,
    SemanticDiffResult,
    StructuralChange,
)
from codeplane.mcp.budget import BudgetAccumulator
from codeplane.mcp.tools.diff import (
    _build_agentic_hint,
    _DiffCache,
    _parse_cursor,
    _result_to_dict,
)

# ============================================================================
# Helpers
# ============================================================================


def _change(
    change: str = "added",
    structural_severity: str = "non_breaking",
    name: str = "foo",
    kind: str = "function",
    qualified_name: str | None = None,
    impact: ImpactInfo | None = None,
) -> StructuralChange:
    return StructuralChange(
        path="src/a.py",
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        change=change,
        structural_severity=structural_severity,
        behavior_change_risk="unknown",
        old_sig="def old()",
        new_sig="def new()",
        impact=impact,
        nested_changes=None,
    )


def _result(
    changes: list[StructuralChange] | None = None,
    summary: str = "test",
    breaking: str | None = None,
) -> SemanticDiffResult:
    return SemanticDiffResult(
        structural_changes=changes or [],
        non_structural_changes=[],
        summary=summary,
        breaking_summary=breaking,
        files_analyzed=1 if changes else 0,
        base_description="HEAD",
        target_description="working tree",
    )


# ============================================================================
# Tests: Agentic Hint Generation
# ============================================================================


class TestAgenticHint:
    """Tests for _build_agentic_hint."""

    def test_no_changes(self) -> None:
        hint = _build_agentic_hint(_result())
        assert "No actionable changes" in hint

    def test_signature_changed_with_refs(self) -> None:
        impact = ImpactInfo(
            reference_count=5,
            referencing_files=["src/a.py", "src/b.py"],
        )
        hint = _build_agentic_hint(
            _result(
                [
                    _change(
                        "signature_changed",
                        "breaking",
                        "connect",
                        "method",
                        "Client.connect",
                        impact,
                    ),
                ]
            )
        )
        assert "Signature of Client.connect" in hint
        assert "5 references" in hint

    def test_removed_hint(self) -> None:
        hint = _build_agentic_hint(_result([_change("removed", "breaking", "OldClass", "class")]))
        assert "OldClass was removed" in hint

    def test_body_changed_hint(self) -> None:
        hint = _build_agentic_hint(
            _result(
                [
                    _change("body_changed", "non_breaking", "foo"),
                    _change("body_changed", "non_breaking", "bar"),
                ]
            )
        )
        assert "2 function bodies changed" in hint

    def test_affected_tests_hint(self) -> None:
        impact = ImpactInfo(affected_test_files=["tests/test_a.py"])
        hint = _build_agentic_hint(_result([_change("removed", "breaking", "foo", impact=impact)]))
        assert "Affected test files:" in hint
        assert "tests/test_a.py" in hint


# ============================================================================
# Tests: Result Serialization
# ============================================================================


class TestResultSerialization:
    """Tests for _result_to_dict."""

    def test_empty_result(self) -> None:
        d = _result_to_dict(_result())
        assert d["summary"] == "test"
        assert d["structural_changes"] == []

    def test_with_impact(self) -> None:
        impact = ImpactInfo(reference_count=3, referencing_files=["a.py"])
        d = _result_to_dict(_result([_change("removed", "breaking", "foo", impact=impact)]))
        assert d["structural_changes"][0]["impact"]["reference_count"] == 3


# ============================================================================
# Tests: Pagination
# ============================================================================


class TestPagination:
    """Tests for _result_to_dict pagination."""

    def test_empty_pagination_when_no_changes(self) -> None:
        d = _result_to_dict(_result())
        assert "next_cursor" not in d["pagination"]
        assert d["pagination"].get("total_estimate") == 0

    def test_no_pagination_under_budget(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(5)]
        d = _result_to_dict(_result(changes))
        assert len(d["structural_changes"]) == 5
        assert "next_cursor" not in d["pagination"]
        assert "truncated" not in d["pagination"]

    def test_pagination_triggers_over_budget(self, monkeypatch: Any) -> None:
        # Swap in a tiny-budget accumulator so even small items overflow
        class _TinyBudget(BudgetAccumulator):
            def __init__(self, budget: int = 500) -> None:
                super().__init__(budget=budget)

        monkeypatch.setattr("codeplane.mcp.tools.diff.BudgetAccumulator", _TinyBudget)
        changes = [_change(name=f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes), cache_id=1)
        assert len(d["structural_changes"]) < 10
        assert d["pagination"]["truncated"] is True
        assert "next_cursor" in d["pagination"]
        # Cursor format: "<cache_id>:<offset>"
        assert d["pagination"]["next_cursor"].startswith("1:")
        assert d["pagination"]["total_estimate"] == 10

    def test_cursor_continues_from_offset(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(10)]
        # cursor_offset=3 should skip first 3
        d = _result_to_dict(_result(changes), cursor_offset=3)
        assert d["structural_changes"][0]["name"] == "fn_3"

    def test_cursor_last_page(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes), cursor_offset=9)
        assert len(d["structural_changes"]) == 1
        assert d["structural_changes"][0]["name"] == "fn_9"
        assert "next_cursor" not in d["pagination"]
        assert "truncated" not in d["pagination"]

    def test_agentic_hint_computed_from_all_changes(self, monkeypatch: Any) -> None:
        """agentic_hint reflects ALL changes, not just the paginated page."""

        class _TinyBudget(BudgetAccumulator):
            def __init__(self, budget: int = 500) -> None:
                super().__init__(budget=budget)

        monkeypatch.setattr("codeplane.mcp.tools.diff.BudgetAccumulator", _TinyBudget)
        changes = [_change("body_changed", "non_breaking", f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes))
        # Hint should mention all 10, not just the items on this page
        assert "10 function bodies changed" in d["agentic_hint"]


# ============================================================================
# Tests: Cursor Parsing
# ============================================================================


class TestParseCursor:
    """Tests for _parse_cursor."""

    def test_none_returns_no_cache_zero_offset(self) -> None:
        cache_id, offset = _parse_cursor(None)
        assert cache_id is None
        assert offset == 0

    def test_valid_cache_cursor(self) -> None:
        cache_id, offset = _parse_cursor("42:17")
        assert cache_id == 42
        assert offset == 17

    def test_legacy_plain_offset(self) -> None:
        """Legacy cursors (plain int) are treated as offset-only."""
        cache_id, offset = _parse_cursor("5")
        assert cache_id is None
        assert offset == 5

    def test_malformed_returns_defaults(self) -> None:
        cache_id, offset = _parse_cursor("garbage")
        assert cache_id is None
        assert offset == 0


# ============================================================================
# Tests: DiffCache
# ============================================================================


class TestDiffCache:
    """Tests for _DiffCache."""

    def test_store_and_retrieve(self) -> None:
        cache = _DiffCache(max_entries=3)
        r = _result([_change(name="a")])
        cid = cache.store(r)
        assert cache.get(cid) is r

    def test_cache_miss(self) -> None:
        cache = _DiffCache(max_entries=3)
        assert cache.get(999) is None

    def test_eviction_on_overflow(self) -> None:
        cache = _DiffCache(max_entries=2)
        r1 = _result([_change(name="a")])
        r2 = _result([_change(name="b")])
        r3 = _result([_change(name="c")])
        id1 = cache.store(r1)
        cache.store(r2)
        cache.store(r3)
        # r1 should be evicted
        assert cache.get(id1) is None

    def test_ttl_expiration(self) -> None:
        import codeplane.mcp.tools.diff as diff_mod

        cache = _DiffCache(max_entries=5)
        r = _result([_change(name="a")])
        cid = cache.store(r)

        # Expire the entry by rewinding its creation timestamp
        entry = cache._entries[cid]
        original_ttl = diff_mod._CACHE_TTL_SECONDS
        entry.created_at -= original_ttl + 1

        assert cache.get(cid) is None

    def test_clear(self) -> None:
        cache = _DiffCache(max_entries=3)
        cid = cache.store(_result())
        cache.clear()
        assert cache.get(cid) is None
