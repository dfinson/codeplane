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
    _SECTION_CAP_BYTES,
    CacheSection,
    SidecarCache,
    _chunk_list,
    _chunk_string,
    _describe_value,
    _find_largest_field,
    _split_oversized_item,
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
        assert result["has_more"] is False
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
        # Top-level "text" key is a ready section, fast path returns it whole
        result = cache.slice_payload(cid, path="text")
        assert result is not None
        assert result["ready"] is True
        assert result["value"] == long_str

        # For paginated string slicing, go through a nested path
        cid2 = cache.put("s1", "ep2", {"wrapper": {"text": long_str}})
        result2 = cache.slice_payload(cid2, path="wrapper.text", max_bytes=100)
        assert result2 is not None
        assert result2["type"] == "string"
        assert len(result2["value"]) == 100
        assert result2["has_more"] is True
        assert result2["total_length"] == 1000

        # Get next chunk
        result3 = cache.slice_payload(cid2, path="wrapper.text", max_bytes=100, offset=100)
        assert result3 is not None
        assert result3["offset"] == 100

    def test_slice_list_pagination(self) -> None:
        cache = SidecarCache()
        big_list = [{"data": "x" * 500} for _ in range(100)]
        cid = cache.put("s1", "ep", {"items": big_list})
        result = cache.slice_payload(cid, path="items", max_bytes=2000)
        assert result is not None
        assert result["has_more"] is True
        assert result["returned"] < 100
        assert result["total"] == 100
        assert result["offset"] == 0
        assert result["next_offset"] is not None

        # Get next page using next_offset
        result2 = cache.slice_payload(
            cid, path="items", max_bytes=2000, offset=result["next_offset"]
        )
        assert result2 is not None
        assert result2["offset"] == result["next_offset"]
        assert result2["returned"] > 0
        # Eventually exhaust the list
        all_returned = result["returned"] + result2["returned"]
        if all_returned < 100:
            assert result2["has_more"] is True
        else:
            assert result2["has_more"] is False
            assert result2["next_offset"] is None


class TestSidecarCacheMeta:
    """Tests for get_meta."""

    def test_meta_dict_payload(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"files": [1, 2], "count": 2})
        meta = cache.get_meta(cid)
        assert meta is not None
        assert meta["cache_id"] == cid
        assert "sections" in meta
        assert meta["sections"]["files"]["type"] == "list(2 items)"
        assert meta["sections"]["count"]["type"] == "int"

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


class TestSections:
    """Tests for pre-computed section metadata."""

    def test_sections_computed_on_put(self) -> None:
        """put() pre-computes sections for dict payloads."""
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"lint": {"status": "clean"}, "tests": {"passed": 42}})
        entry = cache.get_entry(cid)
        assert entry is not None
        assert "lint" in entry.sections
        assert "tests" in entry.sections
        assert isinstance(entry.sections["lint"], CacheSection)

    def test_section_byte_size(self) -> None:
        """Each section has correct byte_size."""
        import json

        cache = SidecarCache()
        payload: dict[str, Any] = {"data": [1, 2, 3]}
        cid = cache.put("s1", "ep", payload)
        entry = cache.get_entry(cid)
        assert entry is not None
        sec = entry.sections["data"]
        expected = len(json.dumps([1, 2, 3], indent=2, default=str).encode("utf-8"))
        assert sec.byte_size == expected

    def test_section_type_desc_dict(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"nested": {"a": 1, "b": 2}})
        entry = cache.get_entry(cid)
        assert entry is not None
        assert entry.sections["nested"].type_desc == "dict(2 keys)"
        assert entry.sections["nested"].item_count == 2

    def test_section_type_desc_list(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"items": [1, 2, 3]})
        entry = cache.get_entry(cid)
        assert entry is not None
        assert entry.sections["items"].type_desc == "list(3 items)"
        assert entry.sections["items"].item_count == 3

    def test_section_type_desc_string(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"text": "hello world"})
        entry = cache.get_entry(cid)
        assert entry is not None
        assert entry.sections["text"].type_desc == "str(11 chars)"

    def test_section_type_desc_bool(self) -> None:
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"passed": True})
        entry = cache.get_entry(cid)
        assert entry is not None
        assert entry.sections["passed"].type_desc == "bool"
        assert entry.sections["passed"].item_count is None

    def test_section_ready_under_cap(self) -> None:
        """Sections ≤ 50KB are marked ready."""
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"small": {"key": "value"}})
        entry = cache.get_entry(cid)
        assert entry is not None
        assert entry.sections["small"].ready is True

    def test_section_not_ready_over_cap(self) -> None:
        """Sections > 50KB are NOT marked ready."""
        cache = SidecarCache()
        big_value = "x" * (_SECTION_CAP_BYTES + 10_000)
        cid = cache.put("s1", "ep", {"big": big_value})
        entry = cache.get_entry(cid)
        assert entry is not None
        assert entry.sections["big"].ready is False

    def test_sections_in_meta(self) -> None:
        """meta() includes section metadata."""
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"a": 1, "b": [1, 2]})
        entry = cache.get_entry(cid)
        assert entry is not None
        meta = entry.meta()
        assert "sections" in meta
        assert "a" in meta["sections"]
        assert "b" in meta["sections"]
        assert meta["sections"]["a"]["ready"] is True
        assert "byte_size" in meta["sections"]["a"]

    def test_section_to_dict(self) -> None:
        """CacheSection.to_dict() returns serializable metadata."""
        sec = CacheSection(
            key="items",
            byte_size=5000,
            type_desc="list(10 items)",
            item_count=10,
            ready=True,
        )
        d = sec.to_dict()
        assert d["key"] == "items"
        assert d["byte_size"] == 5000
        assert d["type"] == "list(10 items)"
        assert d["ready"] is True
        assert d["item_count"] == 10

    def test_list_payload_no_sections(self) -> None:
        """List payloads have empty sections dict."""
        cache = SidecarCache()
        cid = cache.put("s1", "ep", [{"a": 1}, {"a": 2}])
        entry = cache.get_entry(cid)
        assert entry is not None
        assert entry.sections == {}

    def test_fast_path_ready_section(self) -> None:
        """slice_payload uses fast path for ready top-level sections."""
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"lint": {"status": "clean"}, "tests": {"passed": 42}})
        result = cache.slice_payload(cid, path="lint")
        assert result is not None
        assert result["ready"] is True
        assert result["value"] == {"status": "clean"}
        assert result["has_more"] is False
        assert "byte_size" in result

    def test_fast_path_not_used_for_nested(self) -> None:
        """Fast path only applies to single-segment top-level keys."""
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"nested": {"deep": {"key": "val"}}})
        result = cache.slice_payload(cid, path="nested.deep")
        assert result is not None
        assert "ready" not in result  # dynamic path traversal, no ready flag
        assert result["value"] == {"key": "val"}

    def test_sections_in_get_meta(self) -> None:
        """get_meta includes sections for dict payloads."""
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"a": 1, "b": "hello"})
        meta = cache.get_meta(cid)
        assert meta is not None
        assert "sections" in meta
        assert meta["sections"]["a"]["ready"] is True
        assert meta["sections"]["b"]["type"] == "str(5 chars)"


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


class TestChunkList:
    """Tests for _chunk_list helper."""

    def test_single_chunk_when_small(self) -> None:
        items = [{"a": i} for i in range(5)]
        chunks = _chunk_list(items, 50_000)
        assert len(chunks) == 1
        assert len(chunks[0][0]) == 5  # all items in one chunk

    def test_splits_oversized_list(self) -> None:
        # Each item ~500 bytes when serialized, 200 items ~100KB
        items = [{"data": "x" * 450, "idx": i} for i in range(200)]
        chunks = _chunk_list(items, 50_000)
        assert len(chunks) > 1
        # All items accounted for
        total_items = sum(len(c[0]) for c in chunks)
        assert total_items == 200
        # Each chunk ≤ cap
        for _items, chunk_bytes in chunks:
            assert chunk_bytes <= 55_000  # allow minor overhead tolerance

    def test_preserves_item_order(self) -> None:
        items = list(range(50))
        chunks = _chunk_list(items, 200)
        reconstructed = []
        for items_chunk, _ in chunks:
            reconstructed.extend(items_chunk)
        assert reconstructed == items

    def test_splits_oversized_single_item_semantically(self) -> None:
        """A single dict item larger than cap is split by its largest field."""
        # Simulate a lite_files entry with a huge symbols list
        big_item: dict[str, Any] = {
            "path": "src/big_module.py",
            "similarity": 0.9,
            "artifact_kind": "source",
            "summary": {
                "total_lines": 5000,
                "imports": ["os", "sys"],
                "symbols": [f"function symbol_{i}" for i in range(500)],
            },
        }
        # This single item serializes well over 10KB
        item_bytes = len(json.dumps(big_item, indent=2).encode())
        assert item_bytes > 10_000

        # Chunk with a 5KB cap — must split within the item
        chunks = _chunk_list([big_item], 5_000)
        assert len(chunks) > 1

        # Each chunk is a list of 1 partial item
        all_symbols: list[str] = []
        for chunk_items, _chunk_bytes in chunks:
            assert len(chunk_items) == 1
            partial = chunk_items[0]
            # Envelope preserved
            assert partial["path"] == "src/big_module.py"
            assert partial["similarity"] == 0.9
            # _split metadata present
            assert "_split" in partial
            assert partial["_split"]["field"] == "summary.symbols"
            assert partial["_split"]["total"] == len(chunks)
            # Collect symbols
            all_symbols.extend(partial["summary"]["symbols"])

        # All 500 symbols recovered
        assert len(all_symbols) == 500
        assert all_symbols == [f"function symbol_{i}" for i in range(500)]

    def test_splits_oversized_scaffold_string(self) -> None:
        """A scaffold item with a large string field is split semantically."""
        big_scaffold: dict[str, Any] = {
            "path": "src/huge.py",
            "language": "python",
            "total_lines": 10000,
            "imports": ["os"],
            "symbols": [],
            "scaffold_text": "\n".join(f"def func_{i}(): pass" for i in range(500)),
        }
        chunks = _chunk_list([big_scaffold], 3_000)
        assert len(chunks) > 1
        # Each part has _split metadata
        for chunk_items, _ in chunks:
            assert len(chunk_items) == 1
            partial = chunk_items[0]
            assert partial["path"] == "src/huge.py"
            assert "_split" in partial
            assert partial["_split"]["field"] == "scaffold_text"

    def test_mixed_normal_and_oversized_items(self) -> None:
        """Normal items chunk normally; oversized items split semantically."""
        small_items = [{"path": f"small_{i}.py", "data": "x"} for i in range(5)]
        big_item: dict[str, Any] = {
            "path": "big.py",
            "summary": {"symbols": [f"sym_{i}" for i in range(500)]},
        }
        items: list[Any] = small_items + [big_item]
        chunks = _chunk_list(items, 5_000)
        # Check the big item was split (has _split metadata in some chunks)
        split_chunks = [
            c for c in chunks if any("_split" in item for item in c[0] if isinstance(item, dict))
        ]
        assert len(split_chunks) > 0
        # Check small items preserved
        all_paths = [
            item["path"]
            for chunk_items, _ in chunks
            for item in chunk_items
            if isinstance(item, dict) and "path" in item and "_split" not in item
        ]
        assert all(f"small_{i}.py" in all_paths for i in range(5))

    def test_split_chunks_stay_within_cap(self) -> None:
        """Every chunk from _chunk_list — including split parts — stays within cap."""
        big_item: dict[str, Any] = {
            "path": "src/module.py",
            "similarity": 0.9,
            "artifact_kind": "source",
            "summary": {
                "total_lines": 2000,
                "imports": ["os", "sys"],
                "symbols": [f"def func_{i}(a, b, c): ..." for i in range(600)],
            },
        }
        cap = 5_000
        chunks = _chunk_list([big_item], cap)
        assert len(chunks) > 1

        for chunk_items, chunk_bytes in chunks:
            # Verify reported bytes match actual
            actual = len(json.dumps(chunk_items, indent=2, default=str).encode("utf-8"))
            assert chunk_bytes == actual
            assert actual <= cap, (
                f"Chunk with {len(chunk_items)} items is {actual} bytes, exceeds cap {cap}"
            )


class TestFindLargestField:
    """Tests for _find_largest_field helper."""

    def test_finds_top_level_list(self) -> None:
        obj = {"small": "x", "big": list(range(100))}
        path, val = _find_largest_field(obj)
        assert path == "big"
        assert val == list(range(100))

    def test_finds_nested_list(self) -> None:
        obj = {"meta": "x", "summary": {"imports": ["os"], "symbols": list(range(200))}}
        path, val = _find_largest_field(obj)
        assert path == "summary.symbols"
        assert len(val) == 200

    def test_finds_large_string(self) -> None:
        obj = {"name": "x", "content": "y" * 10000}
        path, val = _find_largest_field(obj)
        assert path == "content"
        assert len(val) == 10000

    def test_returns_none_for_scalars_only(self) -> None:
        obj = {"a": 1, "b": True, "c": 3.14}
        path, val = _find_largest_field(obj)
        assert path is None

    def test_skips_underscore_keys(self) -> None:
        obj = {"_private": list(range(1000)), "public": [1, 2]}
        path, val = _find_largest_field(obj)
        assert path == "public"


class TestSplitOversizedItem:
    """Tests for _split_oversized_item."""

    def test_splits_item_with_big_nested_list(self) -> None:
        item: dict[str, Any] = {
            "path": "file.py",
            "summary": {
                "total_lines": 100,
                "symbols": [{"name": f"s{i}", "kind": "function"} for i in range(100)],
            },
        }
        parts = _split_oversized_item(item, 3_000)
        assert len(parts) > 1
        # All parts have _split metadata
        for idx, part in enumerate(parts):
            assert part["_split"]["field"] == "summary.symbols"
            assert part["_split"]["part"] == idx
            assert part["_split"]["total"] == len(parts)
            # Envelope preserved
            assert part["path"] == "file.py"
            assert part["summary"]["total_lines"] == 100

        # All symbols recovered
        all_syms = []
        for part in parts:
            all_syms.extend(part["summary"]["symbols"])
        assert len(all_syms) == 100

    def test_returns_single_for_small_item(self) -> None:
        item: dict[str, Any] = {"path": "small.py", "data": "x"}
        parts = _split_oversized_item(item, 50_000)
        assert len(parts) == 1
        assert "_split" not in parts[0]

    def test_returns_single_for_scalar_only(self) -> None:
        item: dict[str, Any] = {"a": 1, "b": 2.0, "c": True}
        parts = _split_oversized_item(item, 10)
        assert len(parts) == 1

    def test_envelope_aware_cap_enforcement(self) -> None:
        """Each split part, including envelope, must stay within the cap."""
        item: dict[str, Any] = {
            "path": "src/heavy_module.py",
            "similarity": 0.95,
            "combined_score": 0.88,
            "artifact_kind": "source",
            "summary": {
                "total_lines": 3000,
                "imports": ["os", "sys", "json", "pathlib"],
                "symbols": [
                    f"def very_long_function_name_for_symbol_{i}(arg1, arg2, arg3): ..."
                    for i in range(400)
                ],
            },
        }
        cap = 5_000
        parts = _split_oversized_item(item, cap)
        assert len(parts) > 1

        for part in parts:
            # Verify with [part] wrapping — this is how _chunk_list emits
            wrapped_bytes = len(json.dumps([part], indent=2, default=str).encode("utf-8"))
            assert wrapped_bytes <= cap, (
                f"Split part {part['_split']['part']} wrapped is {wrapped_bytes} bytes, "
                f"exceeds cap {cap}"
            )

        # All symbols recovered
        all_syms = []
        for part in parts:
            all_syms.extend(part["summary"]["symbols"])
        assert len(all_syms) == 400


class TestChunkString:
    """Tests for _chunk_string helper."""

    def test_single_chunk_when_small(self) -> None:
        chunks = _chunk_string("hello world", 50_000)
        assert len(chunks) == 1
        assert chunks[0][0] == "hello world"

    def test_splits_on_newlines(self) -> None:
        # Create a string with many lines, each ~100 chars
        lines = [f"line-{i}: " + "x" * 90 for i in range(100)]
        text = "\n".join(lines)
        chunks = _chunk_string(text, 2_000)
        assert len(chunks) > 1
        # Reconstructed text should match (joining back with \n)
        reconstructed = "\n".join(c[0] for c in chunks)
        assert reconstructed == text

    def test_each_chunk_within_cap(self) -> None:
        lines = [f"line-{i}: " + "x" * 90 for i in range(100)]
        text = "\n".join(lines)
        chunks = _chunk_string(text, 2_000)
        for _, chunk_bytes in chunks:
            assert chunk_bytes <= 2_500  # allow minor JSON overhead tolerance


class TestSubSlices:
    """Tests for pre-chunked sub-slice creation and retrieval."""

    def test_oversized_list_creates_sub_slices(self) -> None:
        """Large list section is split into sub-slices at PUT time."""
        cache = SidecarCache()
        # Create a list oversized for the cap
        big_list = [{"file": f"f{i}.py", "content": "x" * 400} for i in range(200)]
        cid = cache.put("s1", "ep", {"scaffold_files": big_list})
        entry = cache.get_entry(cid)
        assert entry is not None

        # Parent section is not ready, has chunk_total
        parent = entry.sections["scaffold_files"]
        assert parent.ready is False
        assert parent.chunk_total is not None
        assert parent.chunk_total > 1

        # Sub-slices exist
        for idx in range(parent.chunk_total):
            sub_key = f"scaffold_files.{idx}"
            assert sub_key in entry.sections
            sub = entry.sections[sub_key]
            assert sub.ready is True
            assert sub.parent_key == "scaffold_files"
            assert sub.chunk_index == idx
            assert sub.chunk_total == parent.chunk_total
            assert sub.chunk_items is not None
            assert sub.chunk_items > 0

    def test_sub_slice_fast_path_retrieval(self) -> None:
        """Sub-slices are served via the fast path in slice_payload."""
        cache = SidecarCache()
        big_list = [{"file": f"f{i}.py", "content": "x" * 400} for i in range(200)]
        cid = cache.put("s1", "ep", {"scaffold_files": big_list})
        entry = cache.get_entry(cid)
        assert entry is not None

        chunk_total = entry.sections["scaffold_files"].chunk_total
        assert chunk_total is not None

        # Retrieve each sub-slice
        all_items: list[Any] = []
        for idx in range(chunk_total):
            result = cache.slice_payload(cid, path=f"scaffold_files.{idx}")
            assert result is not None
            assert result["ready"] is True
            assert result["has_more"] is False
            assert result["chunk_index"] == idx
            assert result["chunk_total"] == chunk_total
            assert result["parent_key"] == "scaffold_files"
            all_items.extend(result["value"])

        # All items recovered
        assert len(all_items) == 200

    def test_oversized_string_creates_sub_slices(self) -> None:
        """Large string section is split into sub-slices at PUT time."""
        cache = SidecarCache()
        lines = [f"line {i}: " + "x" * 100 for i in range(1000)]
        big_text = "\n".join(lines)
        cid = cache.put("s1", "ep", {"log_output": big_text})
        entry = cache.get_entry(cid)
        assert entry is not None

        parent = entry.sections["log_output"]
        assert parent.ready is False
        assert parent.chunk_total is not None
        assert parent.chunk_total > 1

        # Retrieve and reconstruct
        parts: list[str] = []
        for idx in range(parent.chunk_total):
            result = cache.slice_payload(cid, path=f"log_output.{idx}")
            assert result is not None
            assert result["ready"] is True
            parts.append(result["value"])

        reconstructed = "\n".join(parts)
        assert reconstructed == big_text

    def test_small_section_no_sub_slices(self) -> None:
        """Small sections don't get sub-sliced."""
        cache = SidecarCache()
        cid = cache.put("s1", "ep", {"small": [1, 2, 3]})
        entry = cache.get_entry(cid)
        assert entry is not None
        assert entry.sections["small"].ready is True
        assert entry.sections["small"].chunk_total is None
        assert len(entry.sub_slices) == 0

    def test_sub_slice_to_dict_metadata(self) -> None:
        """Sub-slice CacheSection.to_dict() includes chunk metadata."""
        sec = CacheSection(
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
        d = sec.to_dict()
        assert d["parent_key"] == "scaffold_files"
        assert d["chunk_index"] == 0
        assert d["chunk_total"] == 3
        assert d["chunk_items"] == 30
