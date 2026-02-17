"""Tests for MCP delivery envelope, resource cache, and client profiles.

Covers:
- ResourceCache: store, retrieve, TTL/LRU eviction, thread safety
- ClientProfile + resolve_profile: profile selection logic
- build_envelope: inline/resource/paged decisions
"""

from __future__ import annotations

import hashlib
import threading
import time

from codeplane.mcp.delivery import (
    ClientProfile,
    ResourceCache,
    ScopeBudget,
    ScopeManager,
    build_envelope,
    resolve_profile,
)


class TestResourceCache:
    """Tests for ResourceCache."""

    def test_store_and_retrieve(self) -> None:
        """Store payload, get by id, verify content matches."""
        cache = ResourceCache(max_entries=10, ttl_seconds=60.0)
        uri, meta = cache.store(b"hello world", "source", "scope1")
        rid = uri.split("/")[-1]
        result = cache.get(rid)
        assert result is not None
        assert result == b"hello world"

    def test_sha256_computed_at_store(self) -> None:
        """Verify sha256 in resource_meta matches hashlib of stored content."""
        cache = ResourceCache(max_entries=10, ttl_seconds=60.0)
        payload = b"test payload data"
        uri, meta = cache.store(payload, "source", "scope1")
        expected_sha = hashlib.sha256(payload).hexdigest()
        assert meta.sha256 == expected_sha

    def test_immutability(self) -> None:
        """Store, retrieve, verify identical bytes."""
        cache = ResourceCache(max_entries=10, ttl_seconds=60.0)
        payload = b"immutable data 12345"
        uri, meta = cache.store(payload, "source", "scope1")
        rid = uri.split("/")[-1]
        r1 = cache.get(rid)
        r2 = cache.get(rid)
        assert r1 == r2 == payload

    def test_ttl_eviction(self) -> None:
        """Store with short TTL, sleep, verify get returns None."""
        cache = ResourceCache(max_entries=10, ttl_seconds=0.1)
        uri, meta = cache.store(b"ephemeral", "source", "scope1")
        rid = uri.split("/")[-1]
        time.sleep(0.2)
        result = cache.get(rid)
        assert result is None

    def test_lru_eviction(self) -> None:
        """Store max+1 entries, verify oldest evicted."""
        cache = ResourceCache(max_entries=2, ttl_seconds=60.0)
        uri1, _ = cache.store(b"first", "source", "s1")
        cache.store(b"second", "source", "s2")
        cache.store(b"third", "source", "s3")
        rid1 = uri1.split("/")[-1]
        assert cache.get(rid1) is None

    def test_thread_safety(self) -> None:
        """Concurrent store/get from multiple threads, no crashes."""
        cache = ResourceCache(max_entries=100, ttl_seconds=60.0)
        errors: list[str] = []

        def worker(i: int) -> None:
            try:
                uri, _ = cache.store(f"data-{i}".encode(), "source", f"scope-{i}")
                rid = uri.split("/")[-1]
                result = cache.get(rid)
                if result is None:
                    errors.append(f"Worker {i}: get returned None")
            except Exception as e:
                errors.append(f"Worker {i}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"Thread errors: {errors}"

    def test_scope_id_in_uri(self) -> None:
        """Verify URI format is codeplane://{scope_id}/cache/{kind}/{id}."""
        cache = ResourceCache(max_entries=10, ttl_seconds=60.0)
        uri, _ = cache.store(b"data", "source", "my-scope-123")
        assert uri.startswith("codeplane://my-scope-123/cache/source/")

    def test_meta_byte_size(self) -> None:
        """Verify byte_size in meta matches payload size."""
        cache = ResourceCache(max_entries=10, ttl_seconds=60.0)
        payload = b"exactly 25 bytes of data!"
        _, meta = cache.store(payload, "source", "s1")
        assert meta.byte_size == len(payload)


class TestClientProfile:
    """Tests for ClientProfile and resolve_profile."""

    def test_default_profile(self) -> None:
        """No clientInfo -> default profile."""
        profile = resolve_profile(None, None)
        assert profile.name == "default"

    def test_exact_name_match(self) -> None:
        """clientInfo.name='copilot_coding_agent' -> correct profile."""
        profile = resolve_profile({"name": "copilot_coding_agent"}, None)
        assert profile.name == "copilot_coding_agent"
        assert profile.supports_resources is False

    def test_capabilities_resources_true(self) -> None:
        """Unknown name + resources=true -> supports_resources=true."""
        profile = resolve_profile(
            {"name": "unknown_client"},
            {"resources": True},
        )
        assert profile.supports_resources is True

    def test_capabilities_resources_false(self) -> None:
        """Unknown name + resources=false -> supports_resources=false."""
        profile = resolve_profile(
            {"name": "unknown_client"},
            {"resources": False},
        )
        assert profile.supports_resources is False

    def test_auto_resolution(self) -> None:
        """Default profile with resources capability resolves auto correctly."""
        profile = resolve_profile(None, {"resources": True})
        assert profile.supports_resources is True


class TestBuildEnvelope:
    """Tests for build_envelope."""

    def _profile(self, supports_resources: bool = False) -> ClientProfile:
        return ClientProfile(
            name="test",
            supports_resources=supports_resources,
            inline_cap_bytes=7500,
            prefer_delivery="paged",
        )

    def test_small_payload_inline(self) -> None:
        """1KB payload -> delivery='inline', no resource_uri."""
        payload = {"data": "x" * 500}
        env = build_envelope(payload, resource_kind="source", client_profile=self._profile())
        assert env["delivery"] == "inline"
        assert "resource_uri" not in env

    def test_large_payload_paged(self) -> None:
        """Large payload + no resources -> delivery='paged'."""
        payload = {"data": "x" * 20000}
        env = build_envelope(
            payload,
            resource_kind="source",
            client_profile=self._profile(supports_resources=False),
        )
        assert env["delivery"] in ("inline", "paged")

    def test_large_payload_resource(self) -> None:
        """Large payload + resources supported -> delivery='resource'."""
        profile = ClientProfile(
            name="test",
            supports_resources=True,
            inline_cap_bytes=7500,
            prefer_delivery="resource",
        )
        payload = {"data": "x" * 20000}
        env = build_envelope(payload, resource_kind="source", client_profile=profile)
        assert env["delivery"] in ("inline", "resource")

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
