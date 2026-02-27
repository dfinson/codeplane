"""Tests for MCP delivery envelope, client profiles, and cpljson hints.

Covers:
- ClientProfile + resolve_profile: profile selection logic
- wrap_response: inline vs sidecar-cache delivery
- cpljson hint builder: per-kind terminal commands
- ScopeBudget / ScopeManager (preserved from prior tests)
"""

from __future__ import annotations

from typing import Any

from codeplane.mcp.delivery import (
    ScopeBudget,
    ScopeManager,
    _build_cpljson_hint,
    _build_inline_summary,
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
        assert "cpljson" in result["agentic_hint"]
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
# cpljson Hint Tests
# =============================================================================


class TestCpljsonHints:
    """Tests for section-based cpljson hint builder."""

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

    def test_hint_has_cache_id(self) -> None:
        hint = _build_cpljson_hint("abc123", 50000, "recon_result", "sess1")
        assert "abc123" in hint

    def test_hint_has_byte_size(self) -> None:
        hint = _build_cpljson_hint("abc123", 50000, "recon_result", "sess1")
        assert "50,000" in hint

    def test_hint_has_list_command(self) -> None:
        hint = _build_cpljson_hint("abc123", 50000, "recon_result", "sess1")
        assert "cpljson list" in hint
        assert "--session sess1" in hint
        assert "--endpoint recon_result" in hint

    def test_hint_has_meta_command(self) -> None:
        hint = _build_cpljson_hint("abc123", 50000, "recon_result", "sess1")
        assert "cpljson meta --cache abc123" in hint

    def test_ready_sections_shown(self) -> None:
        """Sections ≤ 50KB show as ready with byte sizes."""
        sections = {
            "lint": self._section("lint", 1234),
            "tests": self._section("tests", 45000, type_desc="dict(5 keys)", item_count=5),
            "commit": self._section("commit", 890),
        }
        hint = _build_cpljson_hint("abc123", 50000, "checkpoint", "s1", sections)
        assert "ready, instant retrieval" in hint
        assert "cpljson slice --cache abc123 --path lint" in hint
        assert "cpljson slice --cache abc123 --path tests" in hint
        assert "cpljson slice --cache abc123 --path commit" in hint
        assert "1,234 bytes" in hint
        assert "45,000 bytes" in hint

    def test_oversized_sections_shown(self) -> None:
        """Sections > 50KB show with --max-bytes hint."""
        sections = {
            "small": self._section("small", 1000),
            "big": self._section("big", 120_000, ready=False),
        }
        hint = _build_cpljson_hint("abc123", 121_000, "source", "s1", sections)
        assert "Oversized sections" in hint
        assert "cpljson slice --cache abc123 --path big --max-bytes 50000" in hint
        assert "120,000 bytes" in hint

    def test_section_byte_sizes_in_hint(self) -> None:
        """Each section shows its byte size."""
        sections = {
            "lint": self._section("lint", 1234),
            "agentic_hint": self._section(
                "agentic_hint", 234, type_desc="str(200 chars)", item_count=200
            ),
        }
        hint = _build_cpljson_hint("abc123", 2000, "checkpoint", "s1", sections)
        assert "1,234 bytes" in hint
        assert "234 bytes" in hint

    def test_no_sections_fallback(self) -> None:
        """Without sections, generic slice command shown."""
        hint = _build_cpljson_hint("abc123", 5000, "unknown", "s1")
        assert "cpljson slice --cache abc123 --max-bytes 50000" in hint

    def test_mixed_ready_and_oversized(self) -> None:
        """Hint separates ready and oversized sections."""
        sections = {
            "passed": self._section("passed", 6, type_desc="bool", item_count=None),
            "lint": self._section("lint", 500),
            "coverage": self._section("coverage", 200_000, ready=False),
        }
        hint = _build_cpljson_hint("abc123", 201_000, "checkpoint", "s1", sections)
        assert "ready, instant retrieval" in hint
        assert "Oversized sections" in hint
        assert "--path passed" in hint
        assert "--path lint" in hint
        assert "--path coverage --max-bytes 50000" in hint


# =============================================================================
# _build_inline_summary Tests
# =============================================================================


class TestBuildInlineSummary:
    """Tests for inline summary generation."""

    def test_recon_result_summary(self) -> None:
        payload: dict[str, Any] = {
            "full_file": [1, 2],
            "min_scaffold": [1],
            "summary_only": [1, 2, 3],
        }
        s = _build_inline_summary("recon_result", payload)
        assert s is not None
        assert "2 full file(s)" in s
        assert "1 scaffold(s)" in s
        assert "3 summary(ies)" in s

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

    def test_diff_summary(self) -> None:
        diff_text = "diff --git a/x.py b/x.py\n+foo\ndiff --git a/y.py b/y.py\n-bar"
        s = _build_inline_summary("diff", {"diff": diff_text})
        assert s is not None
        assert "2 file(s)" in s

    def test_source_summary(self) -> None:
        s = _build_inline_summary("source", {"files": [1, 2, 3]})
        assert s == "3 files"

    def test_unknown_kind_returns_none(self) -> None:
        s = _build_inline_summary("never_heard_of_this", {"data": 1})
        assert s is None


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
