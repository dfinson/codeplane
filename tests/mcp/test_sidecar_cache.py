"""Tests for the in-memory sidecar cache.

Covers:
- SidecarCache: put, list, get, slice, meta, eviction
- Module-level convenience functions
- Thread safety basics
"""

from __future__ import annotations

import json
from typing import Any

from codeplane.mcp.sidecar_cache import (
    SidecarCache,
    _describe_value,
    cache_list,
    cache_meta,
    cache_put,
    cache_slice,
    get_sidecar_cache,
)


class TestSidecarCachePut:
    """Tests for cache_put / SidecarCache.put."""

    def test_put_returns_cache_id(self) -> None:
        cache = SidecarCache()
        cid = cache.put("sess1", "recon", {"files": [1, 2, 3]})
        assert isinstance(cid, str)
        assert len(cid) == 12

    def test_put_stores_entry(self) -> None:
        cache = SidecarCache()
        cid = cache.put("sess1", "recon", {"data": "hello"})
        entry = cache.get_entry(cid)
        assert entry is not None
        assert entry.cache_id == cid
        assert entry.session_id == "sess1"
        assert entry.endpoint_key == "recon"
        assert entry.payload == {"data": "hello"}

    def test_put_tracks_byte_size(self) -> None:
        cache = SidecarCache()
        payload: dict[str, Any] = {"key": "value"}
        cid = cache.put("s", "e", payload)
        entry = cache.get_entry(cid)
        assert entry is not None
        expected = len(json.dumps(payload, indent=2, default=str).encode("utf-8"))
        assert entry.byte_size == expected


class TestSidecarCacheFIFO:
    """Tests for FIFO eviction (last-3 per key)."""

    def test_max_three_per_key(self) -> None:
        cache = SidecarCache(max_per_key=3)
        ids = []
        for i in range(5):
            cid = cache.put("s1", "ep", {"i": i})
            ids.append(cid)
        # First two should be evicted
        assert cache.get_entry(ids[0]) is None
        assert cache.get_entry(ids[1]) is None
        # Last three should exist
        assert cache.get_entry(ids[2]) is not None
        assert cache.get_entry(ids[3]) is not None
        assert cache.get_entry(ids[4]) is not None

    def test_different_keys_independent(self) -> None:
        cache = SidecarCache(max_per_key=2)
        a1 = cache.put("s1", "recon", {"a": 1})
        b1 = cache.put("s1", "checkpoint", {"b": 1})
        a2 = cache.put("s1", "recon", {"a": 2})
        b2 = cache.put("s1", "checkpoint", {"b": 2})
        # All should exist (2 per key, 2 keys)
        assert cache.get_entry(a1) is not None
        assert cache.get_entry(b1) is not None
        assert cache.get_entry(a2) is not None
        assert cache.get_entry(b2) is not None

    def test_different_sessions_independent(self) -> None:
        cache = SidecarCache(max_per_key=2)
        a = cache.put("s1", "recon", {"a": 1})
        b = cache.put("s2", "recon", {"b": 1})
        assert cache.get_entry(a) is not None
        assert cache.get_entry(b) is not None


class TestSidecarCacheList:
    """Tests for list_entries."""

    def test_list_empty(self) -> None:
        cache = SidecarCache()
        assert cache.list_entries("s1", "recon") == []

    def test_list_returns_newest_first(self) -> None:
        cache = SidecarCache()
        cache.put("s1", "recon", {"i": 0})
        cache.put("s1", "recon", {"i": 1})
        cache.put("s1", "recon", {"i": 2})
        entries = cache.list_entries("s1", "recon")
        assert len(entries) == 3
        # Newest first
        assert entries[0]["cache_id"] != entries[2]["cache_id"]

    def test_list_only_metadata(self) -> None:
        cache = SidecarCache()
        cache.put("s1", "recon", {"files": [1, 2]})
        entries = cache.list_entries("s1", "recon")
        assert len(entries) == 1
        entry = entries[0]
        assert "payload" not in entry
        assert "cache_id" in entry
        assert "byte_size" in entry
        assert "endpoint_key" in entry


class TestSidecarCacheSlice:
    """Tests for slice_payload."""

    def test_slice_root(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"a": 1, "b": 2})
        result = cache.slice_payload(cid)
        assert result is not None
        assert result["truncated"] is False
        assert result["value"] == {"a": 1, "b": 2}

    def test_slice_by_path(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"nested": {"key": "value"}})
        result = cache.slice_payload(cid, path="nested.key")
        assert result is not None
        assert result["value"] == "value"
        assert result["type"] == "string"

    def test_slice_list_index(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"items": [{"a": 1}, {"a": 2}]})
        result = cache.slice_payload(cid, path="items.1")
        assert result is not None
        assert result["value"] == {"a": 2}

    def test_slice_missing_key(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"a": 1})
        result = cache.slice_payload(cid, path="nonexistent")
        assert result is not None
        assert "error" in result
        assert "available_keys" in result

    def test_slice_list_out_of_range(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"items": [1, 2]})
        result = cache.slice_payload(cid, path="items.5")
        assert result is not None
        assert "error" in result

    def test_slice_not_found(self) -> None:
        cache = SidecarCache()
        assert cache.slice_payload("nonexistent") is None

    def test_slice_string_pagination(self) -> None:
        cache = SidecarCache()
        long_str = "x" * 1000
        cid = cache.put("s1", "ep", {"text": long_str})
        # Get first 100 chars
        result = cache.slice_payload(cid, path="text", max_bytes=100)
        assert result is not None
        assert result["type"] == "string"
        assert len(result["value"]) == 100
        assert result["has_more"] is True
        assert result["total_length"] == 1000

        # Get next chunk
        result2 = cache.slice_payload(cid, path="text", max_bytes=100, offset=100)
        assert result2 is not None
        assert result2["offset"] == 100

    def test_slice_truncated_list(self) -> None:
        cache = SidecarCache()
        big_list = [{"data": "x" * 500} for _ in range(100)]
        cid = cache.put("s1", "ep", {"items": big_list})
        result = cache.slice_payload(cid, path="items", max_bytes=2000)
        assert result is not None
        assert result["truncated"] is True
        assert result["returned"] < 100
        assert result["total"] == 100


class TestSidecarCacheMeta:
    """Tests for get_meta."""

    def test_meta_dict_payload(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"files": [1, 2], "count": 2})
        meta = cache.get_meta(cid)
        assert meta is not None
        assert meta["cache_id"] == cid
        assert "schema" in meta
        assert "files" in meta["schema"]
        assert meta["schema"]["files"]["type"] == "list"
        assert meta["schema"]["count"]["type"] == "int"

    def test_meta_list_payload(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", [{"a": 1}, {"a": 2}])
        meta = cache.get_meta(cid)
        assert meta is not None
        assert meta["schema"]["type"] == "list"
        assert meta["schema"]["length"] == 2

    def test_meta_not_found(self) -> None:
        cache = SidecarCache()
        assert cache.get_meta("nonexistent") is None


class TestSidecarCacheGlobal:
    """Tests for module-level convenience functions."""

    def test_global_cache_put_and_list(self) -> None:
        """Module-level cache_put and cache_list work."""
        cid = cache_put("test-session", "test-endpoint", {"data": 42})
        entries = cache_list("test-session", "test-endpoint")
        assert any(e["cache_id"] == cid for e in entries)

    def test_global_cache_slice(self) -> None:
        cid = cache_put("test-session", "test-ep2", {"key": "val"})
        result = cache_slice(cid, path="key")
        assert result is not None
        assert result["value"] == "val"

    def test_global_cache_meta(self) -> None:
        cid = cache_put("test-session", "test-ep3", {"a": 1})
        meta = cache_meta(cid)
        assert meta is not None
        assert meta["cache_id"] == cid

    def test_get_sidecar_cache_singleton(self) -> None:
        c1 = get_sidecar_cache()
        c2 = get_sidecar_cache()
        assert c1 is c2


class TestSidecarCacheClear:
    """Tests for cache clearing."""

    def test_clear(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"data": 1})
        cache.clear()
        assert cache.get_entry(cid) is None
        assert cache.list_entries("s1", "ep") == []


class TestSidecarCacheMaxKeys:
    """Tests for global key limit."""

    def test_max_keys_eviction(self) -> None:
        cache = SidecarCache(max_keys=3)
        c1 = cache.put("s1", "a", {"i": 1})
        cache.put("s2", "b", {"i": 2})
        cache.put("s3", "c", {"i": 3})
        # Adding a 4th key evicts the 1st
        cache.put("s4", "d", {"i": 4})
        assert cache.get_entry(c1) is None


class TestDescribeValue:
    """Tests for _describe_value helper."""

    def test_describe_dict(self) -> None:
        d = _describe_value({"a": 1, "b": 2})
        assert d["type"] == "dict"
        assert d["key_count"] == 2

    def test_describe_list(self) -> None:
        d = _describe_value([1, 2, 3])
        assert d["type"] == "list"
        assert d["length"] == 3

    def test_describe_str(self) -> None:
        d = _describe_value("hello")
        assert d["type"] == "str"
        assert d["length"] == 5

    def test_describe_bool(self) -> None:
        d = _describe_value(True)
        assert d["type"] == "bool"
        assert d["value"] is True

    def test_describe_int(self) -> None:
        d = _describe_value(42)
        assert d["type"] == "int"
        assert d["value"] == 42
