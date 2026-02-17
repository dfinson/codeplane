"""Tests for search enrichment and delivery envelope integration.

Covers:
- wrap_existing_response: delivery envelope fields on search results
- Scope budget tracking: search call counting and hit tracking
- Enrichment metadata: result structure at each enrichment level
- No source text invariant: search results never expose source content
"""

from __future__ import annotations

from typing import Any

import pytest

from codeplane.mcp.delivery import (
    ScopeBudget,
    ScopeManager,
    wrap_existing_response,
)

# =============================================================================
# Delivery Envelope Tests
# =============================================================================


class TestSearchDeliveryEnvelope:
    """Verify wrap_existing_response adds correct envelope fields."""

    def _make_search_result(self, count: int = 3) -> dict[str, Any]:
        """Build a minimal search result dict."""
        return {
            "results": [
                {
                    "hit_id": f"lexical:src/foo.py:{i}:0",
                    "path": "src/foo.py",
                    "span": {"start_line": i, "start_col": 0, "end_line": i, "end_col": 0},
                    "kind": "lexical",
                    "symbol_id": None,
                    "preview_line": f"line {i} preview",
                }
                for i in range(1, count + 1)
            ],
            "pagination": {},
            "query_time_ms": 0,
            "summary": f"{count} lexical results",
        }

    def test_envelope_fields_present(self) -> None:
        """Wrapped search response has required delivery fields."""
        result = self._make_search_result()
        wrapped = wrap_existing_response(result, resource_kind="search_hits")
        assert wrapped["resource_kind"] == "search_hits"
        assert wrapped["delivery"] == "inline"
        assert "inline_budget_bytes_used" in wrapped
        assert "inline_budget_bytes_limit" in wrapped

    def test_scope_id_added_when_provided(self) -> None:
        """scope_id is injected into the envelope."""
        result = self._make_search_result()
        wrapped = wrap_existing_response(
            result,
            resource_kind="search_hits",
            scope_id="test-scope",
        )
        assert wrapped["scope_id"] == "test-scope"

    def test_scope_id_absent_when_none(self) -> None:
        """No scope_id key when not provided."""
        result = self._make_search_result()
        wrapped = wrap_existing_response(result, resource_kind="search_hits")
        assert "scope_id" not in wrapped

    def test_scope_usage_added_when_provided(self) -> None:
        """scope_usage dict appears in envelope."""
        result = self._make_search_result()
        usage = {"search_calls": 1, "search_hits_returned_total": 3}
        wrapped = wrap_existing_response(
            result,
            resource_kind="search_hits",
            scope_id="s1",
            scope_usage=usage,
        )
        assert wrapped["scope_usage"] == usage

    def test_scope_usage_absent_when_none(self) -> None:
        """No scope_usage key when not provided."""
        result = self._make_search_result()
        wrapped = wrap_existing_response(result, resource_kind="search_hits")
        assert "scope_usage" not in wrapped

    def test_paged_delivery_when_truncated(self) -> None:
        """delivery='paged' when pagination indicates truncation."""
        result = self._make_search_result()
        result["pagination"] = {"truncated": True, "next_cursor": "10"}
        wrapped = wrap_existing_response(result, resource_kind="search_hits")
        assert wrapped["delivery"] == "paged"


# =============================================================================
# Search Scope Budget Integration
# =============================================================================


class TestSearchScopeBudget:
    """Verify search calls correctly interact with scope budgets."""

    def test_increment_search_tracks_calls_and_hits(self) -> None:
        """increment_search bumps both search_calls and hit total."""
        budget = ScopeBudget("test")
        budget.increment_search(hits=5)
        assert budget.search_calls == 1
        assert budget.search_hits_returned_total == 5

    def test_multiple_searches_accumulate(self) -> None:
        """Multiple search calls accumulate counters correctly."""
        budget = ScopeBudget("test")
        budget.increment_search(hits=3)
        budget.increment_search(hits=7)
        budget.increment_search(hits=0)
        assert budget.search_calls == 3
        assert budget.search_hits_returned_total == 10

    def test_search_budget_exceeded(self) -> None:
        """check_budget returns hint when search_calls exceeds limit."""
        budget = ScopeBudget("test")
        budget.max_search_calls = 2
        budget.increment_search(hits=1)
        budget.increment_search(hits=1)
        budget.increment_search(hits=1)  # exceeds
        hint = budget.check_budget("search_calls")
        assert hint is not None
        assert "Refine" in hint

    def test_search_hits_budget_exceeded(self) -> None:
        """check_budget returns hint when search_hits exceeds limit."""
        budget = ScopeBudget("test")
        budget.max_search_hits_returned_total = 10
        budget.increment_search(hits=11)  # exceeds
        hint = budget.check_budget("search_hits")
        assert hint is not None
        assert "filter" in hint.lower()

    def test_scope_manager_search_tracking(self) -> None:
        """ScopeManager returns same budget for same scope_id."""
        mgr = ScopeManager()
        b1 = mgr.get_or_create("agent-1")
        b1.increment_search(hits=5)
        b2 = mgr.get_or_create("agent-1")
        assert b2.search_calls == 1
        assert b2.search_hits_returned_total == 5

    def test_to_usage_dict_includes_search_fields(self) -> None:
        """to_usage_dict includes search_calls and search_hits_returned_total."""
        budget = ScopeBudget("test")
        budget.increment_search(hits=3)
        usage = budget.to_usage_dict()
        assert usage["search_calls"] == 1
        assert usage["search_hits_returned_total"] == 3


# =============================================================================
# Search Result Structure (No Source Text Invariant)
# =============================================================================


class TestSearchResultStructure:
    """Verify search results never contain source text."""

    def _make_result_none_enrichment(self) -> dict[str, Any]:
        """Result item at enrichment='none': spans only."""
        return {
            "hit_id": "lexical:src/app.py:10:0",
            "path": "src/app.py",
            "span": {"start_line": 10, "start_col": 0, "end_line": 10, "end_col": 0},
            "kind": "lexical",
            "symbol_id": None,
            "preview_line": "def hello():",
        }

    def _make_result_minimal_enrichment(self) -> dict[str, Any]:
        """Result item at enrichment='minimal': adds symbol metadata."""
        item = self._make_result_none_enrichment()
        item["symbol"] = {
            "name": "hello",
            "kind": "function",
            "qualified_name": "app.hello",
        }
        return item

    def _make_result_standard_enrichment(self) -> dict[str, Any]:
        """Result item at enrichment='standard': adds enclosing_span."""
        item = self._make_result_minimal_enrichment()
        item["enclosing_span"] = {
            "start_line": 10,
            "end_line": 15,
            "kind": "function",
        }
        item["has_docstring"] = True
        item["signature_hash"] = "abc123"
        return item

    _SOURCE_TEXT_KEYS = {"content", "source", "source_text", "body", "code"}

    @pytest.mark.parametrize(
        "enrichment_level",
        ["none", "minimal", "standard"],
    )
    def test_no_source_text_at_any_enrichment(self, enrichment_level: str) -> None:
        """Search results never contain source text keys."""
        makers = {
            "none": self._make_result_none_enrichment,
            "minimal": self._make_result_minimal_enrichment,
            "standard": self._make_result_standard_enrichment,
        }
        item = makers[enrichment_level]()
        assert not self._SOURCE_TEXT_KEYS & item.keys(), (
            f"Source text key found at enrichment='{enrichment_level}': "
            f"{self._SOURCE_TEXT_KEYS & item.keys()}"
        )

    def test_none_has_no_symbol_metadata(self) -> None:
        """enrichment='none' has no symbol key."""
        item = self._make_result_none_enrichment()
        assert "symbol" not in item

    def test_minimal_has_symbol_metadata(self) -> None:
        """enrichment='minimal' includes symbol name/kind."""
        item = self._make_result_minimal_enrichment()
        assert "symbol" in item
        assert item["symbol"]["name"] == "hello"
        assert item["symbol"]["kind"] == "function"

    def test_standard_has_enclosing_span(self) -> None:
        """enrichment='standard' includes enclosing_span."""
        item = self._make_result_standard_enrichment()
        assert "enclosing_span" in item
        span = item["enclosing_span"]
        assert span["start_line"] <= span["end_line"]
        assert "kind" in span

    def test_preview_line_truncated(self) -> None:
        """preview_line is capped at 120 chars."""
        long_preview = "x" * 200
        # Handler truncates: (r.snippet or "")[:120]
        truncated = long_preview[:120]
        assert len(truncated) == 120

    def test_span_required_fields(self) -> None:
        """Every search result must have span with start_line/start_col."""
        item = self._make_result_none_enrichment()
        span = item["span"]
        assert "start_line" in span
        assert "start_col" in span
        assert "end_line" in span


# =============================================================================
# Paged Continuations Budget
# =============================================================================


class TestPagedContinuationsBudget:
    """Verify paged operations track continuations correctly."""

    def test_increment_paged(self) -> None:
        """increment_paged bumps paged_continuations."""
        budget = ScopeBudget("test")
        budget.increment_paged()
        assert budget.paged_continuations == 1

    def test_paged_budget_exceeded(self) -> None:
        """check_budget returns hint when paged_continuations exceeds limit."""
        budget = ScopeBudget("test")
        budget.max_paged_continuations = 3
        for _ in range(4):
            budget.increment_paged()
        hint = budget.check_budget("paged_continuations")
        assert hint is not None

    def test_mutation_does_not_reset_paged_counter(self) -> None:
        """record_mutation clears read history but not paged counter."""
        budget = ScopeBudget("test")
        budget.increment_paged()
        budget.increment_paged()
        budget.record_mutation()
        assert budget.paged_continuations == 2
