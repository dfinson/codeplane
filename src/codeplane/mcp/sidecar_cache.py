"""In-memory sidecar cache for oversized MCP tool responses.

Design:
- Keyed by (session_id, endpoint_key) where endpoint_key = tool name
- Last-3 FIFO per key (deque(maxlen=3))
- No TTL, no disk, no global LRU — strictly bounded by per-key deque
- Each entry has a unique cache_id + metadata (byte_size, created_at)

Sidecar HTTP routes in daemon/routes.py expose:
  /sidecar/cache/list   — list cached entries for a (session, endpoint) pair
  /sidecar/cache/slice  — extract a JSON sub-path with pagination
  /sidecar/cache/meta   — schema/stats for a cached entry
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Maximum entries per (session_id, endpoint_key) pair
_MAX_PER_KEY = 3

# Maximum total keys tracked (prevent unbounded growth across sessions)
_MAX_KEYS = 500

# Sections ≤ this size are considered "ready" — pre-computed, instant retrieval
_SECTION_CAP_BYTES = 50_000


@dataclass
class CacheSection:
    """Pre-computed section metadata for a top-level payload key."""

    key: str
    byte_size: int
    type_desc: str  # e.g. "list(42 items)", "dict(5 keys)", "str(1200 chars)"
    item_count: int | None  # element count for containers, None for scalars
    ready: bool  # True if byte_size ≤ _SECTION_CAP_BYTES

    def to_dict(self) -> dict[str, Any]:
        """Serializable metadata."""
        d: dict[str, Any] = {
            "key": self.key,
            "byte_size": self.byte_size,
            "type": self.type_desc,
            "ready": self.ready,
        }
        if self.item_count is not None:
            d["item_count"] = self.item_count
        return d


@dataclass
class CacheEntry:
    """A single cached payload with pre-computed section metadata."""

    cache_id: str
    session_id: str
    endpoint_key: str
    payload: dict[str, Any] | list[Any]
    byte_size: int
    sections: dict[str, CacheSection] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)

    def meta(self) -> dict[str, Any]:
        """Return metadata dict (excludes payload)."""
        d: dict[str, Any] = {
            "cache_id": self.cache_id,
            "session_id": self.session_id,
            "endpoint_key": self.endpoint_key,
            "byte_size": self.byte_size,
            "created_at": self.created_at,
            "top_keys": _top_keys(self.payload) if isinstance(self.payload, dict) else None,
        }
        if self.sections:
            d["sections"] = {k: s.to_dict() for k, s in self.sections.items()}
        return d


def _top_keys(d: dict[str, Any]) -> list[str]:
    """Return top-level keys of a dict."""
    return list(d.keys())


def _build_sections(payload: dict[str, Any]) -> dict[str, CacheSection]:
    """Pre-compute section metadata for each top-level key in a dict payload.

    Each section records byte_size, type descriptor, and whether the section
    is 'ready' (≤ _SECTION_CAP_BYTES) for instant retrieval.
    """
    sections: dict[str, CacheSection] = {}
    for key, value in payload.items():
        raw = json.dumps(value, indent=2, default=str).encode("utf-8")
        byte_size = len(raw)

        if isinstance(value, dict):
            type_desc = f"dict({len(value)} keys)"
            item_count = len(value)
        elif isinstance(value, list):
            type_desc = f"list({len(value)} items)"
            item_count = len(value)
        elif isinstance(value, str):
            type_desc = f"str({len(value)} chars)"
            item_count = len(value)
        elif isinstance(value, bool):
            type_desc = "bool"
            item_count = None
        elif isinstance(value, int | float):
            type_desc = type(value).__name__
            item_count = None
        else:
            type_desc = type(value).__name__
            item_count = None

        sections[key] = CacheSection(
            key=key,
            byte_size=byte_size,
            type_desc=type_desc,
            item_count=item_count,
            ready=byte_size <= _SECTION_CAP_BYTES,
        )
    return sections


class SidecarCache:
    """Thread-safe in-memory cache for oversized MCP responses.

    Key space: (session_id, endpoint_key) → deque(maxlen=3)
    """

    def __init__(
        self,
        max_per_key: int = _MAX_PER_KEY,
        max_keys: int = _MAX_KEYS,
    ) -> None:
        self._store: dict[tuple[str, str], deque[CacheEntry]] = {}
        self._index: dict[str, CacheEntry] = {}  # cache_id → entry (fast lookup)
        self._lock = threading.Lock()
        self._max_per_key = max_per_key
        self._max_keys = max_keys

    def put(
        self,
        session_id: str,
        endpoint_key: str,
        payload: dict[str, Any] | list[Any],
    ) -> str:
        """Cache a payload and return a unique cache_id.

        If the per-key deque is full, the oldest entry is evicted (FIFO).
        """
        raw = json.dumps(payload, indent=2, default=str).encode("utf-8")
        byte_size = len(raw)
        cache_id = uuid.uuid4().hex[:12]

        entry = CacheEntry(
            cache_id=cache_id,
            session_id=session_id,
            endpoint_key=endpoint_key,
            payload=payload,
            byte_size=byte_size,
            sections=_build_sections(payload) if isinstance(payload, dict) else {},
        )

        key = (session_id, endpoint_key)

        with self._lock:
            if key not in self._store:
                # Evict oldest key if at capacity
                if len(self._store) >= self._max_keys:
                    oldest_key = next(iter(self._store))
                    evicted = self._store.pop(oldest_key)
                    for e in evicted:
                        self._index.pop(e.cache_id, None)
                self._store[key] = deque(maxlen=self._max_per_key)

            dq = self._store[key]
            # If deque is full, the leftmost (oldest) will be auto-evicted
            if len(dq) >= self._max_per_key:
                evicted_entry = dq[0]  # will be popped by deque
                self._index.pop(evicted_entry.cache_id, None)

            dq.append(entry)
            self._index[cache_id] = entry

        log.debug(
            "sidecar_cache_put",
            cache_id=cache_id,
            session_id=session_id,
            endpoint_key=endpoint_key,
            byte_size=byte_size,
        )
        return cache_id

    def list_entries(
        self,
        session_id: str,
        endpoint_key: str,
    ) -> list[dict[str, Any]]:
        """List cached entries (metadata only) for a (session, endpoint) pair.

        Returns newest-first, up to max_per_key entries.
        """
        key = (session_id, endpoint_key)
        with self._lock:
            dq = self._store.get(key)
            if not dq:
                return []
            return [e.meta() for e in reversed(dq)]

    def get_entry(self, cache_id: str) -> CacheEntry | None:
        """Look up an entry by cache_id."""
        with self._lock:
            return self._index.get(cache_id)

    def slice_payload(
        self,
        cache_id: str,
        path: str | None = None,
        max_bytes: int = 60_000,
        offset: int = 0,
    ) -> dict[str, Any] | None:
        """Extract a sub-path from a cached payload with byte-limited output.

        Args:
            cache_id: The unique cache entry ID.
            path: Dot-separated JSON path (e.g. "files.0.content"). None = root.
            max_bytes: Maximum bytes to return in the slice.
            offset: Character offset for string values (for pagination).

        Returns:
            Dict with slice data, or None if cache_id not found.
        """
        entry = self.get_entry(cache_id)
        if entry is None:
            return None

        # Fast path: top-level key with a pre-computed ready section
        if (
            path
            and "." not in path
            and isinstance(entry.payload, dict)
            and path in entry.sections
            and entry.sections[path].ready
        ):
            section = entry.sections[path]
            return {
                "cache_id": cache_id,
                "path": path,
                "type": section.type_desc,
                "value": entry.payload[path],
                "byte_size": section.byte_size,
                "truncated": False,
                "ready": True,
            }

        # Navigate to sub-path
        value: Any = entry.payload
        resolved_path = ""
        if path:
            for segment in path.split("."):
                resolved_path += ("." if resolved_path else "") + segment
                if isinstance(value, dict):
                    if segment not in value:
                        return {
                            "error": f"key '{segment}' not found at '{resolved_path}'",
                            "available_keys": list(value.keys())[:20],
                        }
                    value = value[segment]
                elif isinstance(value, list):
                    try:
                        idx = int(segment)
                    except ValueError:
                        return {
                            "error": f"expected integer index, got '{segment}' at '{resolved_path}'",
                            "length": len(value),
                        }
                    if idx < 0 or idx >= len(value):
                        return {
                            "error": f"index {idx} out of range at '{resolved_path}'",
                            "length": len(value),
                        }
                    value = value[idx]
                else:
                    return {
                        "error": f"cannot traverse into {type(value).__name__} at '{resolved_path}'",
                    }

        # Serialize and paginate
        if isinstance(value, str):
            chunk = value[offset : offset + max_bytes]
            has_more = (offset + max_bytes) < len(value)
            return {
                "cache_id": cache_id,
                "path": path or "(root)",
                "type": "string",
                "value": chunk,
                "offset": offset,
                "total_length": len(value),
                "has_more": has_more,
            }

        serialized = json.dumps(value, indent=2, default=str)
        if len(serialized) <= max_bytes:
            return {
                "cache_id": cache_id,
                "path": path or "(root)",
                "type": type(value).__name__,
                "value": value,
                "truncated": False,
            }

        # Truncate: for lists, return items that fit; for dicts, return keys
        if isinstance(value, list):
            items: list[Any] = []
            used = 2  # []
            for item in value:
                item_json = json.dumps(item, indent=2, default=str)
                if used + len(item_json) + 2 > max_bytes:
                    break
                items.append(item)
                used += len(item_json) + 2
            return {
                "cache_id": cache_id,
                "path": path or "(root)",
                "type": "list",
                "value": items,
                "returned": len(items),
                "total": len(value),
                "truncated": len(items) < len(value),
            }

        # Dict: return what fits
        truncated = serialized[:max_bytes]
        return {
            "cache_id": cache_id,
            "path": path or "(root)",
            "type": "dict",
            "value_preview": truncated,
            "total_bytes": len(serialized),
            "truncated": True,
        }

    def get_meta(self, cache_id: str) -> dict[str, Any] | None:
        """Get metadata + schema info for a cached entry."""
        entry = self.get_entry(cache_id)
        if entry is None:
            return None

        meta = entry.meta()
        # For list payloads (no sections), add legacy schema info
        if isinstance(entry.payload, list):
            meta["schema"] = {
                "type": "list",
                "length": len(entry.payload),
                "sample_item": _describe_value(entry.payload[0]) if entry.payload else None,
            }
        # For dict payloads without pre-computed sections (fallback)
        elif isinstance(entry.payload, dict) and not entry.sections:
            meta["schema"] = {k: _describe_value(v) for k, v in entry.payload.items()}
        # Dict payloads with sections: sections already included via entry.meta()
        return meta

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._store.clear()
            self._index.clear()


def _describe_value(v: Any) -> dict[str, Any]:
    """Produce a compact type/shape descriptor for a value."""
    if isinstance(v, dict):
        return {"type": "dict", "keys": list(v.keys())[:10], "key_count": len(v)}
    if isinstance(v, list):
        return {
            "type": "list",
            "length": len(v),
            "sample": _describe_value(v[0]) if v else None,
        }
    if isinstance(v, str):
        return {"type": "str", "length": len(v)}
    if isinstance(v, bool):
        return {"type": "bool", "value": v}
    if isinstance(v, int | float):
        return {"type": type(v).__name__, "value": v}
    return {"type": type(v).__name__}


# Global singleton
_sidecar_cache = SidecarCache()


def cache_put(
    session_id: str,
    endpoint_key: str,
    payload: dict[str, Any] | list[Any],
) -> str:
    """Module-level convenience: cache a payload, return cache_id."""
    return _sidecar_cache.put(session_id, endpoint_key, payload)


def cache_list(session_id: str, endpoint_key: str) -> list[dict[str, Any]]:
    """Module-level convenience: list entries for a (session, endpoint) pair."""
    return _sidecar_cache.list_entries(session_id, endpoint_key)


def cache_slice(
    cache_id: str,
    path: str | None = None,
    max_bytes: int = 60_000,
    offset: int = 0,
) -> dict[str, Any] | None:
    """Module-level convenience: slice a cached entry."""
    return _sidecar_cache.slice_payload(cache_id, path, max_bytes, offset)


def cache_meta(cache_id: str) -> dict[str, Any] | None:
    """Module-level convenience: get metadata for a cached entry."""
    return _sidecar_cache.get_meta(cache_id)


def get_sidecar_cache() -> SidecarCache:
    """Return the global sidecar cache instance."""
    return _sidecar_cache
