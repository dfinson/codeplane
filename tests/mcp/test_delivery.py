"""Tests for MCP delivery envelope, client profiles, and cplcache hints.

Covers:
- ClientProfile + resolve_profile: profile selection logic
- wrap_response: inline vs sidecar-cache delivery
- cplcache hint builder: per-kind terminal commands
- ScopeBudget / ScopeManager (preserved from prior tests)
"""

from __future__ import annotations

from typing import Any

from codeplane.mcp.delivery import (
    _SLICE_STRATEGIES,
    ScopeBudget,
    ScopeManager,
    SliceStrategy,
    _build_cplcache_hint,
    _build_inline_summary,
    _order_sections,
    resolve_profile,
    wrap_response,
)
from codeplane.mcp.sidecar_cache import CacheSection

# =============================================================================
# Client Profile Tests
# =============================================================================


class TestClientProfile:
    """Tests for resolve_profile."""

    def test_default_profile(self) -> None:
        """No clientInfo -> default profile."""
        profile = resolve_profile(None, None)
        assert profile.name == "default"

    def test_exact_name_match(self) -> None:
        """clientInfo.name='copilot_coding_agent' -> correct profile."""
        profile = resolve_profile({"name": "copilot_coding_agent"}, None)
        assert profile.name == "copilot_coding_agent"

    def test_unknown_name_falls_to_default(self) -> None:
        """Unknown name -> default profile."""
        profile = resolve_profile({"name": "unknown_client"}, None)
        assert profile.name == "default"

    def test_config_override(self) -> None:
        """Explicit config override takes priority."""
        profile = resolve_profile(
            {"name": "Visual Studio Code"},
            None,
            config_override="copilot_coding_agent",
        )
        assert profile.name == "copilot_coding_agent"


# =============================================================================
# wrap_response Tests
# =============================================================================


class TestWrapResponse:
    """Tests for wrap_response -- inline vs sidecar delivery."""

    def test_small_payload_inline(self) -> None:
        """Payloads under inline cap are returned inline."""
        result = wrap_response(
            {"data": "small"},
            resource_kind="test_kind",
        )
        assert result["delivery"] == "inline"
        assert result["resource_kind"] == "test_kind"
        assert result["data"] == "small"

    def test_large_payload_sidecar(self) -> None:
        """Payloads over inline cap go to sidecar cache."""
        big_data = {"content": "x" * 50_000}
        result = wrap_response(
            big_data,
            resource_kind="source",
            session_id="test-sess",
        )
        assert result["delivery"] == "sidecar_cache"
        assert result["resource_kind"] == "source"
        assert "cache_id" in result
        assert "agentic_hint" in result
        assert "cplcache" in result["agentic_hint"]
        # Original payload should NOT be in the envelope
        assert "content" not in result

    def test_scope_id_echoed(self) -> None:
        """scope_id appears in inline response."""
        result = wrap_response(
            {"data": "ok"},
            resource_kind="test",
            scope_id="scope-123",
        )
        assert result["scope_id"] == "scope-123"

    def test_scope_usage_echoed(self) -> None:
        """scope_usage dict appears in response."""
        result = wrap_response(
            {"data": "ok"},
            resource_kind="test",
            scope_usage={"read_bytes": 100},
        )
        assert result["scope_usage"] == {"read_bytes": 100}

    def test_inline_budget_fields(self) -> None:
        """Inline delivery includes budget tracking fields."""
        result = wrap_response(
            {"data": "ok"},
            resource_kind="test",
        )
        assert "inline_budget_bytes_used" in result
        assert "inline_budget_bytes_limit" in result


# =============================================================================
# cplcache Hint Tests
# =============================================================================


class TestCplcacheHints:
    """Tests for strategy-driven cplcache hint builder."""

    def _section(
        self,
        key: str,
        byte_size: int,
        type_desc: str = "dict(1 keys)",
        item_count: int | None = 1,
        ready: bool = True,
    ) -> CacheSection:
        return CacheSection(
            key=key,
            byte_size=byte_size,
            type_desc=type_desc,
            item_count=item_count,
            ready=ready,
        )

    # --- Basic envelope content ---

    def test_hint_has_cache_id(self) -> None:
        hint = _build_cplcache_hint("abc123", 50000, "recon_result")
        assert "abc123" in hint

    def test_hint_has_byte_size(self) -> None:
        hint = _build_cplcache_hint("abc123", 50000, "recon_result")
        assert "50,000" in hint

    def test_hint_has_command_template(self) -> None:
        hint = _build_cplcache_hint("abc123", 50000, "recon_result")
        assert "python3 .codeplane/scripts/cplcache.py" in hint
        assert '--cache-id "abc123"' in hint
        assert "<SLICE>" in hint

    # --- Strategy flow ---

    def test_known_kind_shows_strategy_flow(self) -> None:
        """Known resource_kind includes plan text."""
        hint = _build_cplcache_hint("abc123", 50000, "checkpoint")
        assert "Plan:" in hint
        assert "passed" in hint.lower()

    def test_unknown_kind_no_strategy(self) -> None:
        """Unknown resource_kind has no Plan line."""
        hint = _build_cplcache_hint("abc123", 50000, "unknown_kind")
        assert "Plan:" not in hint

    def test_recon_strategy_flow(self) -> None:
        hint = _build_cplcache_hint("abc123", 50000, "recon_result")
        assert "scaffold_files" in hint
        assert "lite_files" in hint
        assert "repo_map" in hint

    # --- Section descriptions ---

    def test_ready_sections_with_descriptions(self) -> None:
        """Ready sections show key labels with descriptions."""
        sections = {
            "lint": self._section("lint", 1234),
            "tests": self._section("tests", 45000, type_desc="dict(5 keys)", item_count=5),
            "commit": self._section("commit", 890),
        }
        hint = _build_cplcache_hint("abc123", 50000, "checkpoint", sections)
        assert ">>> RESPONSE CACHED" in hint
        assert "Sections" in hint
        assert "lint" in hint
        assert "commit" in hint
        assert "1,234" in hint
        assert "45,000" in hint
        # Descriptions from strategy
        assert "linter diagnostics" in hint
        assert "commit SHA" in hint

    def test_oversized_sections_with_descriptions(self) -> None:
        """Oversized non-chunkable sections show key labels."""
        sections = {
            "scaffold_files": self._section("scaffold_files", 120_000, ready=False),
            "summary": self._section("summary", 200),
        }
        hint = _build_cplcache_hint("abc123", 121_000, "recon_result", sections)
        assert ">>> RESPONSE CACHED" in hint
        assert "scaffold_files" in hint
        assert "120,000" in hint
        assert "imports + signatures" in hint  # description from recon_result strategy

    def test_section_byte_sizes_in_hint(self) -> None:
        """Each section shows its byte size."""
        sections = {
            "lint": self._section("lint", 1234),
            "agentic_hint": self._section(
                "agentic_hint", 234, type_desc="str(200 chars)", item_count=200
            ),
        }
        hint = _build_cplcache_hint("abc123", 2000, "checkpoint", sections)
        assert "1,234" in hint
        assert "234" in hint

    def test_no_sections_fallback(self) -> None:
        """Without sections, command template shown."""
        hint = _build_cplcache_hint("abc123", 5000, "unknown")
        assert 'python3 .codeplane/scripts/cplcache.py --cache-id "abc123"' in hint
        assert "<SLICE>" in hint

    def test_mixed_ready_and_oversized(self) -> None:
        """Hint lists all sections in one Sections block."""
        sections = {
            "passed": self._section("passed", 6, type_desc="bool", item_count=None),
            "lint": self._section("lint", 500),
            "coverage": self._section("coverage", 200_000, ready=False),
        }
        hint = _build_cplcache_hint("abc123", 201_000, "checkpoint", sections)
        assert ">>> RESPONSE CACHED" in hint
        assert "Sections" in hint
        # All sections present
        assert "passed" in hint
        assert "lint" in hint
        assert "coverage" in hint

    # --- Priority ordering ---

    def test_checkpoint_priority_ordering(self) -> None:
        """Checkpoint sections ordered: passed, summary, agentic_hint, lint, tests, commit."""
        sections = {
            "commit": self._section("commit", 500),
            "tests": self._section("tests", 800),
            "passed": self._section("passed", 6, type_desc="bool", item_count=None),
            "agentic_hint": self._section("agentic_hint", 50),
            "lint": self._section("lint", 400),
            "summary": self._section("summary", 100),
        }
        hint = _build_cplcache_hint("abc123", 50000, "checkpoint", sections)
        lines = hint.split("\n")
        # Section entries are indented with key at start
        section_lines = [
            ln.strip() for ln in lines if ln.startswith("  ") and "(" in ln and "b)" in ln
        ]
        keys = [ln.split(" ")[0] for ln in section_lines]
        assert keys == ["passed", "summary", "agentic_hint", "lint", "tests", "commit"]

    def test_recon_priority_ordering(self) -> None:
        """Recon sections ordered: agentic_hint, scaffold_files, lite_files, repo_map."""
        sections = {
            "lite_files": self._section("lite_files", 300),
            "scaffold_files": self._section("scaffold_files", 40000),
            "repo_map": self._section("repo_map", 20000),
            "agentic_hint": self._section("agentic_hint", 100),
        }
        hint = _build_cplcache_hint("abc123", 60000, "recon_result", sections)
        lines = hint.split("\n")
        section_lines = [
            ln.strip() for ln in lines if ln.startswith("  ") and "(" in ln and "b)" in ln
        ]
        keys = [ln.split(" ")[0] for ln in section_lines]
        # agentic_hint first, then scaffold_files, lite_files, repo_map from priority
        assert keys == ["agentic_hint", "scaffold_files", "lite_files", "repo_map"]

    def test_unknown_kind_sections_no_reorder(self) -> None:
        """Unknown resource kind preserves insertion order (no strategy)."""
        sections = {
            "z_key": self._section("z_key", 100),
            "a_key": self._section("a_key", 200),
        }
        hint = _build_cplcache_hint("abc123", 5000, "unknown_kind", sections)
        lines = hint.split("\n")
        section_lines = [
            ln.strip() for ln in lines if ln.startswith("  ") and "(" in ln and "b)" in ln
        ]
        keys = [ln.split(" ")[0] for ln in section_lines]
        assert keys == ["z_key", "a_key"]

    # --- Pre-chunked sub-slice hints ---

    def test_chunked_section_lists_sub_slices(self) -> None:
        """Pre-chunked parent lists each sub-slice command individually."""
        parent = CacheSection(
            key="scaffold_files",
            byte_size=120_000,
            type_desc="list(80 items)",
            item_count=80,
            ready=False,
            chunk_total=3,
        )
        sub0 = CacheSection(
            key="scaffold_files.0",
            byte_size=45_000,
            type_desc="list(30 items)",
            item_count=30,
            ready=True,
            parent_key="scaffold_files",
            chunk_index=0,
            chunk_total=3,
            chunk_items=30,
        )
        sub1 = CacheSection(
            key="scaffold_files.1",
            byte_size=40_000,
            type_desc="list(28 items)",
            item_count=28,
            ready=True,
            parent_key="scaffold_files",
            chunk_index=1,
            chunk_total=3,
            chunk_items=28,
        )
        sub2 = CacheSection(
            key="scaffold_files.2",
            byte_size=35_000,
            type_desc="list(22 items)",
            item_count=22,
            ready=True,
            parent_key="scaffold_files",
            chunk_index=2,
            chunk_total=3,
            chunk_items=22,
        )
        sections: dict[str, Any] = {
            "agentic_hint": self._section("agentic_hint", 200),
            "scaffold_files": parent,
            "scaffold_files.0": sub0,
            "scaffold_files.1": sub1,
            "scaffold_files.2": sub2,
        }
        hint = _build_cplcache_hint("abc123", 121_000, "recon_result", sections)
        # Parent shown with chunk count
        assert "scaffold_files" in hint
        assert "3 chunks" in hint
        # Each sub-slice is listed
        assert "scaffold_files.0" in hint
        assert "scaffold_files.1" in hint
        assert "scaffold_files.2" in hint
        # Item counts shown
        assert "30 items" in hint
        assert "28 items" in hint
        assert "22 items" in hint

    def test_hint_header_urgency(self) -> None:
        """Hint starts with an unmissable CACHED header."""
        hint = _build_cplcache_hint("abc123", 50000, "recon_result")
        assert hint.startswith(">>> RESPONSE CACHED")

    def test_retrieval_plan_from_strategy(self) -> None:
        """Known resource kind shows Plan with flow text."""
        hint = _build_cplcache_hint("abc123", 50000, "recon_result")
        assert "Plan:" in hint
        assert "scaffold_files" in hint


class TestOrderSections:
    """Tests for _order_sections helper."""

    def _section(self, key: str, byte_size: int = 100) -> CacheSection:
        return CacheSection(
            key=key, byte_size=byte_size, type_desc="dict(1 keys)", item_count=1, ready=True
        )

    def test_no_strategy_preserves_order(self) -> None:
        sections = {"b": self._section("b"), "a": self._section("a")}
        result = _order_sections(sections, None)
        assert [k for k, _ in result] == ["b", "a"]

    def test_strategy_priority_first(self) -> None:
        sections = {
            "c": self._section("c"),
            "a": self._section("a"),
            "b": self._section("b"),
        }
        strategy = SliceStrategy(flow="test", priority=("b", "a"))
        result = _order_sections(sections, strategy)
        assert [k for k, _ in result] == ["b", "a", "c"]

    def test_missing_priority_keys_skipped(self) -> None:
        sections = {"x": self._section("x"), "y": self._section("y")}
        strategy = SliceStrategy(flow="test", priority=("missing", "x"))
        result = _order_sections(sections, strategy)
        assert [k for k, _ in result] == ["x", "y"]

    def test_remaining_sorted_alphabetically(self) -> None:
        sections = {
            "z": self._section("z"),
            "m": self._section("m"),
            "a": self._section("a"),
        }
        strategy = SliceStrategy(flow="test", priority=("m",))
        result = _order_sections(sections, strategy)
        assert [k for k, _ in result] == ["m", "a", "z"]

    def test_empty_priority_preserves_order(self) -> None:
        sections = {"b": self._section("b"), "a": self._section("a")}
        strategy = SliceStrategy(flow="test", priority=())
        result = _order_sections(sections, strategy)
        assert [k for k, _ in result] == ["b", "a"]


class TestSliceStrategies:
    """Tests for the _SLICE_STRATEGIES registry."""

    def test_all_endpoint_kinds_covered(self) -> None:
        expected = {
            "recon_result",
            "resolve_result",
            "checkpoint",
            "semantic_diff",
            "refactor_preview",
        }
        assert set(_SLICE_STRATEGIES.keys()) == expected

    def test_every_strategy_has_flow(self) -> None:
        for kind, strategy in _SLICE_STRATEGIES.items():
            assert strategy.flow, f"{kind} has empty flow"

    def test_every_strategy_has_priority(self) -> None:
        for kind, strategy in _SLICE_STRATEGIES.items():
            assert len(strategy.priority) > 0, f"{kind} has empty priority"

    def test_checkpoint_priority_keys(self) -> None:
        s = _SLICE_STRATEGIES["checkpoint"]
        assert s.priority[:3] == ("passed", "summary", "agentic_hint")


# =============================================================================
# _build_inline_summary Tests
# =============================================================================


class TestBuildInlineSummary:
    """Tests for inline summary generation."""

    def test_recon_result_summary(self) -> None:
        payload: dict[str, Any] = {
            "scaffold_files": [1, 2],
            "lite_files": [1, 2, 3],
            "repo_map": {"overview": "..."},
        }
        s = _build_inline_summary("recon_result", payload)
        assert s is not None
        assert "2 scaffold(s)" in s
        assert "3 lite(s)" in s
        assert "repo_map included" in s

    def test_checkpoint_summary_passed(self) -> None:
        payload: dict[str, Any] = {
            "passed": True,
            "summary": "lint: clean",
            "commit": {"oid": "abcdef1234567"},
        }
        s = _build_inline_summary("checkpoint", payload)
        assert s is not None
        assert "PASSED" in s
        assert "abcdef1" in s

    def test_checkpoint_summary_failed(self) -> None:
        s = _build_inline_summary("checkpoint", {"passed": False, "summary": "2 failures"})
        assert s is not None
        assert "FAILED" in s

    def test_semantic_diff_summary(self) -> None:
        s = _build_inline_summary("semantic_diff", {"summary": "3 changes"})
        assert s == "3 changes"

    def test_resolve_result_summary(self) -> None:
        payload: dict[str, Any] = {
            "resolved": [{"path": "a.py"}, {"path": "b.py"}],
            "errors": [{"path": "c.py", "error": "not found"}],
        }
        s = _build_inline_summary("resolve_result", payload)
        assert s is not None
        assert "2 file(s) resolved" in s
        assert "1 error(s)" in s

    def test_refactor_preview_summary(self) -> None:
        payload: dict[str, Any] = {
            "preview": {
                "files_affected": 3,
                "edits": [{}, {}, {}, {}],
            },
        }
        s = _build_inline_summary("refactor_preview", payload)
        assert s is not None
        assert "4 edit(s)" in s
        assert "3 file(s)" in s

    def test_unknown_kind_returns_none(self) -> None:
        s = _build_inline_summary("never_heard_of_this", {"data": 1})
        assert s is None


# =============================================================================
# Chunk-aware Section Hint Tests
# =============================================================================


class TestChunkAwareSectionHints:
    """Tests that all resource kinds use chunk-aware section-level hints."""

    def _section(
        self,
        key: str,
        byte_size: int,
        type_desc: str = "dict(1 keys)",
        item_count: int | None = 1,
        ready: bool = True,
    ) -> CacheSection:
        return CacheSection(
            key=key,
            byte_size=byte_size,
            type_desc=type_desc,
            item_count=item_count,
            ready=ready,
        )

    def test_resolve_uses_section_level_hints(self) -> None:
        """resolve_result uses section-level hints, not per-file dot-paths."""
        sections = {
            "resolved": self._section("resolved", 5000),
            "agentic_hint": self._section("agentic_hint", 200),
        }
        hint = _build_cplcache_hint(
            "abc123",
            5200,
            "resolve_result",
            sections=sections,
        )
        # Section listed
        assert "resolved" in hint
        assert "agentic_hint" in hint
        # No per-file dot-paths
        assert 'resolved.0"' not in hint

    def test_recon_uses_section_level_hints(self) -> None:
        """recon_result uses section-level hints, not per-file dot-paths."""
        sections = {
            "agentic_hint": self._section("agentic_hint", 100),
            "scaffold_files": self._section("scaffold_files", 40000),
            "lite_files": self._section("lite_files", 300),
        }
        hint = _build_cplcache_hint(
            "abc123",
            40400,
            "recon_result",
            sections=sections,
        )
        assert "scaffold_files" in hint
        # No per-file dot-paths
        assert 'scaffold_files.0"' not in hint

    def test_recon_chunked_scaffold_shows_chunks(self) -> None:
        """Chunked scaffold_files section shows chunk commands."""
        parent = CacheSection(
            key="scaffold_files",
            byte_size=120_000,
            type_desc="list(25 items)",
            item_count=25,
            ready=False,
            chunk_total=2,
        )
        sub0 = CacheSection(
            key="scaffold_files.0",
            byte_size=50_000,
            type_desc="list(15 items)",
            item_count=15,
            ready=True,
            parent_key="scaffold_files",
            chunk_index=0,
            chunk_total=2,
            chunk_items=15,
        )
        sub1 = CacheSection(
            key="scaffold_files.1",
            byte_size=70_000,
            type_desc="list(10 items)",
            item_count=10,
            ready=True,
            parent_key="scaffold_files",
            chunk_index=1,
            chunk_total=2,
            chunk_items=10,
        )
        sections: dict[str, Any] = {
            "agentic_hint": self._section("agentic_hint", 200),
            "scaffold_files": parent,
            "scaffold_files.0": sub0,
            "scaffold_files.1": sub1,
        }
        hint = _build_cplcache_hint("abc123", 120_200, "recon_result", sections)
        assert "scaffold_files" in hint
        assert "2 chunks" in hint
        assert "scaffold_files.0" in hint
        assert "scaffold_files.1" in hint
        assert "15 items" in hint
        assert "10 items" in hint

    def test_resolve_without_sections_shows_fallback(self) -> None:
        """resolve_result without sections shows generic fallback."""
        hint = _build_cplcache_hint("abc123", 5000, "resolve_result")
        assert "cplcache" in hint
        assert "<SLICE>" in hint

    def test_checkpoint_uses_section_level(self) -> None:
        """Non-file resource kinds use section-level hints even with payload."""
        sections = {
            "passed": self._section("passed", 6, type_desc="bool", item_count=None),
        }
        hint = _build_cplcache_hint(
            "abc123",
            500,
            "checkpoint",
            sections=sections,
        )
        assert "passed" in hint


# =============================================================================
# Scope Budget Tests (preserved)
# =============================================================================


class TestScopeBudget:
    """Tests for ScopeBudget and ScopeManager."""

    def test_scope_creation(self) -> None:
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        assert isinstance(budget, ScopeBudget)

    def test_scope_usage_tracked(self) -> None:
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_read(1000)
        budget.increment_read(2000)
        usage = budget.to_usage_dict()
        assert usage["read_bytes"] == 3000

    def test_full_read_counter(self) -> None:
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_full_read("a.py", 100)
        budget.increment_full_read("b.py", 200)
        usage = budget.to_usage_dict()
        assert usage["full_reads"] == 2

    def test_search_call_counter(self) -> None:
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_search(10)
        usage = budget.to_usage_dict()
        assert usage["search_calls"] == 1
        assert usage["search_hits"] == 10

    def test_multiple_scopes_independent(self) -> None:
        mgr = ScopeManager()
        b1 = mgr.get_or_create("scope-a")
        b2 = mgr.get_or_create("scope-b")
        b1.increment_read(1000)
        b2.increment_read(5000)
        assert b1.to_usage_dict()["read_bytes"] == 1000
        assert b2.to_usage_dict()["read_bytes"] == 5000

    def test_duplicate_read_detection(self) -> None:
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_full_read("same.py", 100)
        budget.increment_full_read("same.py", 100)
        warning = budget.check_duplicate_read("same.py")
        assert warning is not None
        assert warning["code"] == "DUPLICATE_FULL_READ"
        assert warning["count"] == 2

    def test_no_warning_after_mutation(self) -> None:
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_full_read("file.py", 100)
        budget.record_mutation()
        budget.increment_full_read("file.py", 100)
        warning = budget.check_duplicate_read("file.py")
        assert warning is None
