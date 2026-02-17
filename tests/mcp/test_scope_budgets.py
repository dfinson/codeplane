"""Tests for scope budget tracking and enforcement."""

from __future__ import annotations

import time

import pytest

from codeplane.mcp.delivery import ScopeBudget, ScopeManager

# =============================================================================
# ScopeBudget
# =============================================================================


class TestScopeBudget:
    """ScopeBudget creation and tracking."""

    def test_initial_counters_zero(self) -> None:
        """New budget has all counters at zero."""
        b = ScopeBudget("test")
        assert b.read_bytes_total == 0
        assert b.full_file_reads == 0
        assert b.search_calls == 0

    def test_increment_read(self) -> None:
        """increment_read adds bytes to counter."""
        b = ScopeBudget("test")
        b.increment_read(1000)
        assert b.read_bytes_total == 1000
        b.increment_read(500)
        assert b.read_bytes_total == 1500

    def test_increment_full_read(self) -> None:
        """increment_full_read tracks path and bytes."""
        b = ScopeBudget("test")
        b.increment_full_read("a.py", 2000)
        assert b.full_file_reads == 1
        assert b.read_bytes_total == 2000

    def test_increment_search(self) -> None:
        """increment_search increments call counter."""
        b = ScopeBudget("test")
        b.increment_search(10)
        assert b.search_calls == 1
        b.increment_search(5)
        assert b.search_calls == 2

    def test_to_usage_dict(self) -> None:
        """Usage dict contains all counter keys."""
        b = ScopeBudget("test")
        b.increment_read(100)
        d = b.to_usage_dict()
        assert "read_bytes" in d
        assert "full_reads" in d
        assert "search_calls" in d
        assert d["read_bytes"] == 100

    def test_check_budget_none_when_under(self) -> None:
        """check_budget returns None when within limits."""
        b = ScopeBudget("test")
        b.increment_read(100)
        assert b.check_budget("read_bytes") is None

    def test_duplicate_read_warning(self) -> None:
        """Duplicate full read of same file emits warning."""
        b = ScopeBudget("test")
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
        b = ScopeBudget("test")
        b.increment_full_read("a.py", 500)
        b.increment_full_read("b.py", 500)
        w = b.check_duplicate_read("b.py")
        assert w is None  # b.py only read once

    def test_reset_mutations(self) -> None:
        """Mutation resets clear duplicate tracking."""
        b = ScopeBudget("test")
        b.increment_full_read("a.py", 500)
        b.increment_full_read("a.py", 500)
        b.record_mutation()
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
        assert b2.read_bytes_total == 0

    def test_scope_ttl_eviction(self) -> None:
        """Scopes are evicted after TTL."""
        mgr = ScopeManager(ttl_seconds=0.1)  # 100ms TTL
        b1 = mgr.get_or_create("scope-1")
        b1.increment_read(1000)
        time.sleep(0.15)
        # After TTL, get_or_create should return a fresh budget
        b2 = mgr.get_or_create("scope-1")
        assert b2.read_bytes_total == 0

    def test_lru_eviction(self) -> None:
        """LRU eviction when max entries exceeded."""
        mgr = ScopeManager(max_scopes=2, ttl_seconds=300)
        mgr.get_or_create("a")
        mgr.get_or_create("b")
        mgr.get_or_create("c")  # should evict 'a'
        # 'a' should be fresh
        b = mgr.get_or_create("a")
        assert b.read_bytes_total == 0


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
        assert b.read_bytes_total == 600
        d = b.to_usage_dict()
        assert d["read_bytes"] == 600

    def test_full_read_counter(self) -> None:
        """Full reads increment counter."""
        b = ScopeBudget("test")
        b.increment_full_read("a.py", 1000)
        b.increment_full_read("b.py", 2000)
        assert b.full_file_reads == 2
        assert b.read_bytes_total == 3000

    def test_search_call_counter(self) -> None:
        """Search calls increment counter."""
        b = ScopeBudget("test")
        b.increment_search(10)
        b.increment_search(20)
        assert b.search_calls == 2


# =============================================================================
# Budget Reset
# =============================================================================
class TestBudgetReset:
    """Budget reset eligibility and request flow."""

    def test_read_reset_after_mutation(self) -> None:
        """Read budget resets after mutation + explicit request."""
        b = ScopeBudget("test")
        b.increment_read(5_000_000)
        b.increment_read(5_000_001)  # exceed 10MB
        assert b.check_budget("read_bytes") is not None
        b.record_mutation()
        result = b.request_reset(
            "read",
            "Re-reading all modules after significant refactoring changes",
        )
        assert result["reset"] is True
        assert result["before"]["read_bytes_total"] == 10_000_001
        assert result["after"]["read_bytes_total"] == 0
        assert b.read_bytes_total == 0
        assert b.check_budget("read_bytes") is None

    def test_read_reset_not_eligible_without_mutation(self) -> None:
        """Read reset fails without mutation and below ceiling."""
        b = ScopeBudget("test")
        b.increment_read(100)
        with pytest.raises(ValueError, match="requires at least one mutation"):
            b.request_reset(
                "read",
                "I need additional budget to continue reading more files",
            )

    def test_search_reset_requires_n_mutations(self) -> None:
        """Search reset requires N mutations (default 3)."""
        b = ScopeBudget("test")
        for _ in range(201):
            b.increment_search(1)
        assert b.check_budget("search_calls") is not None
        # 1 mutation - not enough
        b.record_mutation()
        with pytest.raises(ValueError, match="requires 3 mutations"):
            b.request_reset(
                "search",
                "Need to search the codebase after first major refactor work",
            )
        # 2 mutations - still not enough
        b.record_mutation()
        with pytest.raises(ValueError, match="requires 3 mutations"):
            b.request_reset(
                "search",
                "Need to search the codebase after completing second edits",
            )
        # 3 mutations - eligible
        b.record_mutation()
        result = b.request_reset(
            "search",
            "Refactored 3 times now, need fresh search budget to continue",
        )
        assert result["reset"] is True
        assert b.search_calls == 0

    def test_justification_too_short(self) -> None:
        """Justification under 50 chars is rejected."""
        b = ScopeBudget("test")
        b.record_mutation()
        with pytest.raises(ValueError, match="at least 50 characters"):
            b.request_reset("read", "short")

    def test_reset_consumes_eligibility(self) -> None:
        """Resetting consumes eligibility - can't reset twice on same mutation."""
        b = ScopeBudget("test")
        b.record_mutation()
        b.request_reset(
            "read",
            "First reset after edit, need to verify all updated files",
        )
        with pytest.raises(ValueError, match="requires at least one mutation"):
            b.request_reset(
                "read",
                "Second reset same epoch, attempting without eligibility",
            )

    def test_total_resets_tracks_cumulative(self) -> None:
        """total_resets increments and never resets."""
        b = ScopeBudget("test")
        b.record_mutation()
        b.request_reset(
            "read",
            "First reset justification for post-mutation re-reading work",
        )
        assert b._total_resets == 1
        b.record_mutation()
        b.request_reset(
            "read",
            "Second reset justification after additional mutation changes",
        )
        assert b._total_resets == 2

    def test_reset_log_records_entries(self) -> None:
        """Reset log captures category, justification, and epoch."""
        b = ScopeBudget("test")
        b.record_mutation()
        justification = "Reset after refactoring, I need a fresh read for analysis"
        b.request_reset("read", justification)
        assert len(b._reset_log) == 1
        entry = b._reset_log[0]
        assert entry["category"] == "read"
        assert entry["justification"] == justification
        assert entry["epoch"] == 1
        assert entry["has_mutations"] is True

    def test_usage_dict_shows_availability(self) -> None:
        """to_usage_dict shows reset availability flags and epoch."""
        b = ScopeBudget("test")
        d = b.to_usage_dict()
        assert "read_reset_available" not in d
        assert d["mutation_epoch"] == 0
        assert d["total_resets"] == 0
        b.record_mutation()
        d = b.to_usage_dict()
        assert d["read_reset_available"] is True
        assert d["mutation_epoch"] == 1

    def test_no_mutation_ceiling_reset_read(self) -> None:
        """Pure-read workflow: reset at ceiling with long justification."""
        b = ScopeBudget("test", max_read_calls=5)
        for _ in range(6):
            b.increment_read(100)
        assert b.check_budget("read_calls") is not None
        # Justification >= 50 but < 250 fails for pure-read path
        with pytest.raises(ValueError, match="at least 250 characters"):
            b.request_reset(
                "read",
                "I need to continue my reading workflow for this review task",
            )
        # Long justification (>= 250 chars) succeeds
        long_justification = (
            "Performing a comprehensive code review of the entire "
            "repository to identify security vulnerabilities and "
            "architectural issues. Need additional read budget to "
            "complete analysis of remaining modules and their "
            "test coverage. This is a read-only audit workflow."
        )
        result = b.request_reset("read", long_justification)
        assert result["reset"] is True
        assert b.read_calls == 0

    def test_no_mutation_ceiling_reset_search(self) -> None:
        """Pure-read workflow: search reset at ceiling with 250+ chars."""
        b = ScopeBudget("test", max_search_calls=5)
        for _ in range(6):
            b.increment_search(1)
        assert b.check_budget("search_calls") is not None
        long_justification = (
            "Performing a comprehensive audit of the entire codebase "
            "to catalog all API endpoints and their dependencies. "
            "Need additional search budget to finish scanning the "
            "remaining service layer modules and integration test "
            "files. This is a read-only architecture review task."
        )
        result = b.request_reset("search", long_justification)
        assert result["reset"] is True
        assert b.search_calls == 0

    def test_pure_read_usage_dict_shows_availability_at_ceiling(self) -> None:
        """Usage dict shows reset availability for pure-read at ceiling."""
        b = ScopeBudget("test", max_read_calls=5)
        d = b.to_usage_dict()
        assert "read_reset_available" not in d
        # Exceed ceiling
        for _ in range(6):
            b.increment_read(100)
        d = b.to_usage_dict()
        assert d["read_reset_available"] is True

    def test_invalid_category_rejected(self) -> None:
        """Invalid category raises ValueError."""
        b = ScopeBudget("test")
        b.record_mutation()
        with pytest.raises(ValueError, match="Invalid reset category"):
            b.request_reset("write", "not a valid category")

    def test_scope_manager_request_reset(self) -> None:
        """ScopeManager.request_reset delegates to budget."""
        mgr = ScopeManager()
        b = mgr.get_or_create("s1")
        b.increment_read(100)
        b.record_mutation()
        result = mgr.request_reset(
            "s1",
            "read",
            "Reset via scope manager after recording mutation event now",
        )
        assert result["reset"] is True

    def test_scope_manager_request_reset_unknown_scope(self) -> None:
        """ScopeManager.request_reset raises for unknown scope."""
        mgr = ScopeManager()
        with pytest.raises(ValueError, match="No budget found"):
            mgr.request_reset(
                "unknown",
                "read",
                "Should fail because scope does not exist in the manager",
            )
        with pytest.raises(ValueError, match="No budget found"):
            mgr.request_reset("unknown", "read", "should fail no scope")
