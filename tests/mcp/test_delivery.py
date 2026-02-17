"""Tests for MCP delivery envelope, resource cache, and client profiles.

Covers:
- ResourceCache: disk-backed store/retrieve
- ClientProfile + resolve_profile: profile selection logic
- build_envelope: inline/resource delivery decisions
"""

from __future__ import annotations

import hashlib
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

    def test_store_and_retrieve(self) -> None:
        """Store payload, get by id and kind, verify content matches."""
        cache = ResourceCache()
        uri, meta = cache.store(b"hello world", "source", "scope1")
        rid = uri.split("/")[-1]
        result = cache.get(rid, kind="source")
        assert result is not None
        assert result == b"hello world"

    def test_sha256_computed_at_store(self) -> None:
        """Verify sha256 in resource_meta matches hashlib of stored content."""
        cache = ResourceCache()
        payload = b"test payload data"
        uri, meta = cache.store(payload, "source", "scope1")
        expected_sha = hashlib.sha256(payload).hexdigest()
        assert meta.sha256 == expected_sha

    def test_immutability(self) -> None:
        """Store, retrieve twice, verify identical bytes."""
        cache = ResourceCache()
        payload = b"immutable data 12345"
        uri, meta = cache.store(payload, "source", "scope1")
        rid = uri.split("/")[-1]
        r1 = cache.get(rid, kind="source")
        r2 = cache.get(rid, kind="source")
        assert r1 == r2 == payload

    def test_get_without_kind_scans_dirs(self) -> None:
        """Fallback scan finds resource without explicit kind."""
        cache = ResourceCache()
        uri, _ = cache.store(b"findme", "source", "s1")
        rid = uri.split("/")[-1]
        result = cache.get(rid)  # no kind
        assert result == b"findme"

    def test_get_missing_returns_none(self) -> None:
        """Non-existent resource returns None."""
        cache = ResourceCache()
        assert cache.get("nonexistent", kind="source") is None

    def test_scope_id_in_uri(self) -> None:
        """Verify URI format is codeplane://{scope_id}/cache/{kind}/{id}."""
        cache = ResourceCache()
        uri, _ = cache.store(b"data", "source", "my-scope-123")
        assert uri.startswith("codeplane://my-scope-123/cache/source/")

    def test_meta_byte_size(self) -> None:
        """Verify byte_size in meta matches payload size."""
        cache = ResourceCache()
        payload = b"exactly 25 bytes of data!"
        _, meta = cache.store(payload, "source", "s1")
        assert meta.byte_size == len(payload)

    def test_dict_payload_stored_compact(self) -> None:
        """Dict payloads are serialized as compact JSON on disk."""
        cache = ResourceCache()
        payload = {"key": "value", "nested": [1, 2, 3]}
        uri, _ = cache.store(payload, "test", "s1")
        rid = uri.split("/")[-1]
        raw = cache.get(rid, kind="test")
        assert raw is not None
        # Compact = no spaces after separators
        text = raw.decode("utf-8")
        assert " " not in text
        assert json.loads(text) == payload

    def test_no_cache_dir_returns_none(self) -> None:
        """When _cache_dir is None, get returns None."""
        _delivery_mod._cache_dir = None
        cache = ResourceCache()
        assert cache.get("anything") is None

    """Tests for ClientProfile and resolve_profile."""

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

    def _profile(self, inline_cap: int = 7500) -> ClientProfile:
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
        assert "resource_uri" in env
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
        assert usage["search_hits_returned_total"] == 10

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
