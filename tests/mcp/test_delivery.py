"""Tests for MCP delivery envelope, resource cache, and client profiles.

Covers:
- ResourceCache: disk-backed store/retrieve
- ClientProfile + resolve_profile: profile selection logic
- build_envelope: inline/resource delivery decisions
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest

import codeplane.mcp.delivery as _delivery_mod
from codeplane.mcp.delivery import (
    ClientProfile,
    ResourceCache,
    ScopeBudget,
    ScopeManager,
    build_envelope,
    resolve_profile,
)


class TestResourceCache:
    """Tests for disk-backed ResourceCache."""

    @pytest.fixture(autouse=True)
    def _setup_cache_dir(self, tmp_path: Path) -> Generator[None, None, None]:
        """Point _cache_dir at a temp directory for each test."""
        old = _delivery_mod._cache_dir
        _delivery_mod._cache_dir = tmp_path / ".codeplane" / "cache"
        yield
        _delivery_mod._cache_dir = old

    def test_store_writes_to_disk(self) -> None:
        """Store payload, verify file exists on disk with correct content."""
        cache = ResourceCache()
        resource_id, byte_size = cache.store(b"hello world", "source")
        assert _delivery_mod._cache_dir is not None
        disk_path = _delivery_mod._cache_dir / "source" / f"{resource_id}.json"
        assert disk_path.exists()
        assert disk_path.read_bytes() == b"hello world"
        assert byte_size == len(b"hello world")

    def test_immutability(self) -> None:
        """Store, read twice from disk, verify identical bytes."""
        cache = ResourceCache()
        payload = b"immutable data 12345"
        resource_id, _ = cache.store(payload, "source")
        assert _delivery_mod._cache_dir is not None
        disk_path = _delivery_mod._cache_dir / "source" / f"{resource_id}.json"
        r1 = disk_path.read_bytes()
        r2 = disk_path.read_bytes()
        assert r1 == r2 == payload

    def test_byte_size_returned(self) -> None:
        """Verify byte_size matches payload size."""
        cache = ResourceCache()
        payload = b"exactly 25 bytes of data!"
        _, byte_size = cache.store(payload, "source")
        assert byte_size == len(payload)

    def test_dict_payload_stored_pretty(self) -> None:
        """Dict payloads are serialized as pretty-printed JSON on disk."""
        cache = ResourceCache()
        payload = {"key": "value", "nested": [1, 2, 3]}
        resource_id, _ = cache.store(payload, "test")
        assert _delivery_mod._cache_dir is not None
        disk_path = _delivery_mod._cache_dir / "test" / f"{resource_id}.json"
        raw = disk_path.read_bytes()
        text = raw.decode("utf-8")
        # Pretty-printed with indent=2 for agent readability
        assert "\n" in text
        assert text.startswith("{")
        assert json.loads(text) == payload

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


class TestBuildEnvelope:
    """Tests for build_envelope."""

    @pytest.fixture(autouse=True)
    def _setup_cache_dir(self, tmp_path: Path) -> Generator[None, None, None]:
        """Point disk cache at tmp for resource delivery tests."""
        old = _delivery_mod._cache_dir
        _delivery_mod._cache_dir = tmp_path / ".codeplane" / "cache"
        _delivery_mod._cache_dir.mkdir(parents=True, exist_ok=True)
        yield
        _delivery_mod._cache_dir = old

    def _profile(self, inline_cap: int = 8000) -> ClientProfile:
        return ClientProfile(name="test", inline_cap_bytes=inline_cap)

    def test_small_payload_inline(self) -> None:
        """Small payload -> delivery='inline', no resource_uri."""
        payload = {"data": "x" * 500}
        env = build_envelope(payload, resource_kind="source", client_profile=self._profile())
        assert env["delivery"] == "inline"
        assert "resource_uri" not in env

    def test_large_payload_resource(self) -> None:
        """Large payload -> delivery='resource', written to disk."""
        payload = {"data": "x" * 20000}
        env = build_envelope(payload, resource_kind="source", client_profile=self._profile())
        assert env["delivery"] == "resource"
        assert "resource_uri" not in env
        assert "agentic_hint" in env
        assert "cat" in env["agentic_hint"] or "type" in env["agentic_hint"]

    def test_inline_budget_fields(self) -> None:
        """Verify inline_budget_bytes_used and _limit present."""
        payload = {"data": "small"}
        env = build_envelope(payload, resource_kind="source", client_profile=self._profile())
        assert "inline_budget_bytes_used" in env
        assert "inline_budget_bytes_limit" in env

    def test_resource_kind_set(self) -> None:
        """Verify resource_kind matches the tool type."""
        payload = {"data": "x"}
        env = build_envelope(payload, resource_kind="search_hits", client_profile=self._profile())
        assert env["resource_kind"] == "search_hits"

    def test_scope_id_echoed(self) -> None:
        """Pass scope_id, verify echoed in response."""
        payload = {"data": "x"}
        env = build_envelope(
            payload,
            resource_kind="source",
            client_profile=self._profile(),
            scope_id="s-123",
        )
        assert env["scope_id"] == "s-123"


class TestScopeBudget:
    """Tests for ScopeBudget and ScopeManager."""

    def test_scope_creation(self) -> None:
        """First call with scope_id creates scope."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        assert isinstance(budget, ScopeBudget)

    def test_scope_usage_tracked(self) -> None:
        """Multiple reads increment read_bytes."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_read(1000)
        budget.increment_read(2000)
        usage = budget.to_usage_dict()
        assert usage["read_bytes"] == 3000

    def test_full_read_counter(self) -> None:
        """read_file_full increments full_reads counter."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_full_read("a.py", 100)
        budget.increment_full_read("b.py", 200)
        usage = budget.to_usage_dict()
        assert usage["full_reads"] == 2

    def test_search_call_counter(self) -> None:
        """Search increments search_calls counter."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_search(10)
        usage = budget.to_usage_dict()
        assert usage["search_calls"] == 1
        assert usage["search_hits"] == 10

    def test_multiple_scopes_independent(self) -> None:
        """Two scope_ids track independently."""
        mgr = ScopeManager()
        b1 = mgr.get_or_create("scope-a")
        b2 = mgr.get_or_create("scope-b")
        b1.increment_read(1000)
        b2.increment_read(5000)
        assert b1.to_usage_dict()["read_bytes"] == 1000
        assert b2.to_usage_dict()["read_bytes"] == 5000

    def test_duplicate_read_detection(self) -> None:
        """Same full read twice, same scope -> warning."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_full_read("same.py", 100)
        budget.increment_full_read("same.py", 100)
        warning = budget.check_duplicate_read("same.py")
        assert warning is not None
        assert warning["code"] == "DUPLICATE_FULL_READ"
        assert warning["count"] == 2

    def test_no_warning_after_mutation(self) -> None:
        """read -> mutation -> read -> no warning."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_full_read("file.py", 100)
        budget.record_mutation()
        budget.increment_full_read("file.py", 100)
        warning = budget.check_duplicate_read("file.py")
        assert warning is None


class TestFetchHints:
    """Tests for OS-agnostic fetch hints and per-kind hint builders."""

    def test_source_hint_lists_files(self) -> None:
        """Source hint includes file paths and line ranges."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "files": [
                {"path": "src/a.py", "range": [1, 50], "content": "..."},
                {"path": "src/b.py", "range": [10, 30], "content": "..."},
            ],
            "summary": "2 files",
        }
        hint = _build_fetch_hint("abc123", 5000, "source", payload)
        assert "Contains 2 file(s)" in hint
        assert "src/a.py (L1-L50)" in hint
        assert "src/b.py (L10-L30)" in hint
        assert "cached to disk" in hint

    def test_search_hits_hint_counts(self) -> None:
        """Search hits hint reports result and file counts."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "results": [
                {"path": "a.py", "span": {}},
                {"path": "a.py", "span": {}},
                {"path": "b.py", "span": {}},
            ],
            "summary": "3 results",
        }
        hint = _build_fetch_hint("abc123", 3000, "search_hits", payload)
        assert "3 result(s) across 2 file(s)" in hint

    def test_diff_hint_counts_files(self) -> None:
        """Diff hint counts files from diff --git markers."""
        from codeplane.mcp.delivery import _build_fetch_hint

        diff_text = "diff --git a/x.py b/x.py\n+hello\ndiff --git a/y.py b/y.py\n-bye"
        payload = {"diff": diff_text}
        hint = _build_fetch_hint("abc123", 2000, "diff", payload)
        assert "2 file(s) changed" in hint

    def test_log_hint_shows_latest(self) -> None:
        """Log hint includes latest commit info."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "results": [
                {"short_sha": "abc1234", "message": "fix bug", "sha": "abc1234full"},
                {"short_sha": "def5678", "message": "add feature", "sha": "def5678full"},
            ],
            "summary": "2 commits",
        }
        hint = _build_fetch_hint("abc123", 4000, "log", payload)
        assert "2 commit(s)" in hint
        assert "abc1234" in hint
        assert "fix bug" in hint

    def test_blame_hint_shows_authors(self) -> None:
        """Blame hint reports hunk and author counts."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "path": "src/foo.py",
            "results": [
                {"author": "alice", "commit_sha": "aaa", "start_line": 1, "line_count": 10},
                {"author": "bob", "commit_sha": "bbb", "start_line": 11, "line_count": 5},
            ],
        }
        hint = _build_fetch_hint("abc123", 3000, "blame", payload)
        assert "2 hunk(s)" in hint
        assert "2 author(s)" in hint
        assert "src/foo.py" in hint

    def test_test_output_hint_shows_summary(self) -> None:
        """Test output hint shows pass/fail counts."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {"passed": 10, "failed": 2, "total": 12}
        hint = _build_fetch_hint("abc123", 5000, "test_output", payload)
        assert "10 passed" in hint
        assert "2 failed" in hint
        assert "FAILED" in hint  # grep hint for failures

    def test_semantic_diff_hint_counts_changes(self) -> None:
        """Semantic diff hint summarizes change types."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "changes": [
                {"change_type": "added"},
                {"change_type": "added"},
                {"change_type": "removed"},
            ],
        }
        hint = _build_fetch_hint("abc123", 4000, "semantic_diff", payload)
        assert "3 structural change(s)" in hint
        assert "2 added" in hint
        assert "1 removed" in hint

    def test_fallback_hint_for_unknown_kind(self) -> None:
        """Unknown kind gets a generic cat/type hint."""
        from codeplane.mcp.delivery import _build_fetch_hint

        hint = _build_fetch_hint("abc123", 1000, "unknown_kind", {"data": "x"})
        assert "cached to disk" in hint
        # Should contain a read command (cat on unix, type on windows)
        assert "cat" in hint or "type" in hint

    def test_hint_without_payload_falls_back(self) -> None:
        """No payload -> generic fallback hint."""
        from codeplane.mcp.delivery import _build_fetch_hint

        hint = _build_fetch_hint("abc123", 1000, "source")
        assert "cached to disk" in hint
        assert "cat" in hint or "type" in hint

    def test_nav_commands_os_agnostic(self) -> None:
        """Navigation commands adapt to the current platform."""
        import sys

        from codeplane.mcp.delivery import _nav_cmd_cat, _nav_cmd_grep

        cat_cmd = _nav_cmd_cat("some/path.json")
        grep_cmd = _nav_cmd_grep("pattern", "some/path.json")
        if sys.platform == "win32":
            assert "type" in cat_cmd
            assert "findstr" in grep_cmd
            assert "\\" in cat_cmd  # Windows path separators
        else:
            assert "cat" in cat_cmd
            assert "grep" in grep_cmd

    def test_repo_map_hint_lists_sections(self) -> None:
        """Repo map hint lists top-level sections."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "structure": {"tree": []},
            "dependencies": [],
            "test_layout": {},
            "summary": "repo map",
        }
        hint = _build_fetch_hint("abc123", 6000, "repo_map", payload)
        assert "structure" in hint
        assert "dependencies" in hint
        assert "test_layout" in hint

    def test_commit_hint_shows_sha_and_message(self) -> None:
        """Commit hint shows sha and message."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {"short_sha": "abc1234", "message": "fix important bug", "sha": "abc1234full"}
        hint = _build_fetch_hint("abc123", 3000, "commit", payload)
        assert "abc1234" in hint
        assert "fix important bug" in hint

    def test_refactor_preview_hint_counts_matches(self) -> None:
        """Refactor preview hint shows match and file counts."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "matches": [
                {"path": "a.py", "line": 10},
                {"path": "a.py", "line": 20},
                {"path": "b.py", "line": 5},
            ],
        }
        hint = _build_fetch_hint("abc123", 4000, "refactor_preview", payload)
        assert "3 match(es) across 2 file(s)" in hint
