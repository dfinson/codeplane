"""Tests for scope budget tracking and enforcement."""

from __future__ import annotations

import time

from codeplane.mcp.delivery import ScopeBudget, ScopeManager

# =============================================================================
# ScopeBudget
# =============================================================================


class TestScopeBudget:
    """ScopeBudget creation and tracking."""

    def test_initial_counters_zero(self) -> None:
        """New budget has all counters at zero."""
        b = ScopeBudget()
        assert b.read_bytes == 0
        assert b.full_reads == 0
        assert b.search_calls == 0

    def test_increment_read(self) -> None:
        """increment_read adds bytes to counter."""
        b = ScopeBudget()
        b.increment_read(1000)
        assert b.read_bytes == 1000
        b.increment_read(500)
        assert b.read_bytes == 1500

    def test_increment_full_read(self) -> None:
        """increment_full_read tracks path and bytes."""
        b = ScopeBudget()
        b.increment_full_read("a.py", 2000)
        assert b.full_reads == 1
        assert b.read_bytes == 2000

    def test_increment_search(self) -> None:
        """increment_search increments call counter."""
        b = ScopeBudget()
        b.increment_search(10)
        assert b.search_calls == 1
        b.increment_search(5)
        assert b.search_calls == 2

    def test_to_usage_dict(self) -> None:
        """Usage dict contains all counter keys."""
        b = ScopeBudget()
        b.increment_read(100)
        d = b.to_usage_dict()
        assert "read_bytes" in d
        assert "full_reads" in d
        assert "search_calls" in d
        assert d["read_bytes"] == 100

    def test_check_budget_none_when_under(self) -> None:
        """check_budget returns None when within limits."""
        b = ScopeBudget()
        b.increment_read(100)
        assert b.check_budget("read_bytes") is None

    def test_duplicate_read_warning(self) -> None:
        """Duplicate full read of same file emits warning."""
        b = ScopeBudget()
        b.increment_full_read("a.py", 500)
        w1 = b.check_duplicate_read("a.py")
        assert w1 is None  # First duplicate check — only on second call

        b.increment_full_read("a.py", 500)
        w2 = b.check_duplicate_read("a.py")
        if w2:
            assert w2["code"] == "DUPLICATE_FULL_READ"
            assert w2["path"] == "a.py"

    def test_duplicate_read_different_paths(self) -> None:
        """Different paths don't trigger duplicate warning."""
        b = ScopeBudget()
        b.increment_full_read("a.py", 500)
        b.increment_full_read("b.py", 500)
        w = b.check_duplicate_read("b.py")
        assert w is None  # b.py only read once

    def test_reset_mutations(self) -> None:
        """Mutation resets clear duplicate tracking."""
        b = ScopeBudget()
        b.increment_full_read("a.py", 500)
        b.increment_full_read("a.py", 500)
        b.reset_on_mutation()
        # After reset, next read shouldn't trigger duplicate
        w = b.check_duplicate_read("a.py")
        assert w is None


# =============================================================================
# ScopeManager
# =============================================================================


class TestScopeManager:
    """ScopeManager creation and lookup."""

    def test_get_or_create(self) -> None:
        """First call creates, second returns same instance."""
        mgr = ScopeManager()
        b1 = mgr.get_or_create("scope-1")
        b2 = mgr.get_or_create("scope-1")
        assert b1 is b2

    def test_independent_scopes(self) -> None:
        """Different scope_ids have independent budgets."""
        mgr = ScopeManager()
        b1 = mgr.get_or_create("scope-a")
        b2 = mgr.get_or_create("scope-b")
        b1.increment_read(1000)
        assert b2.read_bytes == 0

    def test_scope_ttl_eviction(self) -> None:
        """Scopes are evicted after TTL."""
        mgr = ScopeManager(ttl=0.1)  # 100ms TTL
        b1 = mgr.get_or_create("scope-1")
        b1.increment_read(1000)
        time.sleep(0.15)
        # After TTL, get_or_create should return a fresh budget
        b2 = mgr.get_or_create("scope-1")
        assert b2.read_bytes == 0

    def test_lru_eviction(self) -> None:
        """LRU eviction when max entries exceeded."""
        mgr = ScopeManager(max_entries=2, ttl=300)
        mgr.get_or_create("a")
        mgr.get_or_create("b")
        mgr.get_or_create("c")  # should evict 'a'
        # 'a' should be fresh
        b = mgr.get_or_create("a")
        assert b.read_bytes == 0


# =============================================================================
# Scope usage in envelope
# =============================================================================


class TestScopeUsageInEnvelope:
    """Scope usage tracking appears in delivery envelopes."""

    def test_scope_usage_echoed_in_build_envelope(self) -> None:
        """scope_usage dict appears in build_envelope output."""
        from codeplane.mcp.delivery import build_envelope

        usage = {"read_bytes": 500, "full_reads": 1, "search_calls": 0}
        result = build_envelope(
            {"data": "test"},
            resource_kind="source",
            scope_id="test-scope",
            scope_usage=usage,
            inline_summary="test",
        )
        assert result["scope_usage"] == usage
        assert result["scope_id"] == "test-scope"

    def test_no_scope_id_no_scope_fields(self) -> None:
        """Without scope_id, scope fields are absent."""
        from codeplane.mcp.delivery import build_envelope

        result = build_envelope(
            {"data": "test"},
            resource_kind="source",
            inline_summary="test",
        )
        assert "scope_id" not in result or result.get("scope_id") is None
        assert "scope_usage" not in result or result.get("scope_usage") is None

    def test_budget_counter_increments(self) -> None:
        """Multiple reads accumulate in scope budget."""
        mgr = ScopeManager()
        b = mgr.get_or_create("test")
        b.increment_read(100)
        b.increment_read(200)
        b.increment_read(300)
        assert b.read_bytes == 600
        d = b.to_usage_dict()
        assert d["read_bytes"] == 600

    def test_full_read_counter(self) -> None:
        """Full reads increment counter."""
        b = ScopeBudget()
        b.increment_full_read("a.py", 1000)
        b.increment_full_read("b.py", 2000)
        assert b.full_reads == 2
        assert b.read_bytes == 3000

    def test_search_call_counter(self) -> None:
        """Search calls increment counter."""
        b = ScopeBudget()
        b.increment_search(10)
        b.increment_search(20)
        assert b.search_calls == 2
