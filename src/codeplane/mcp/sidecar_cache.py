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

# Compact JSON separators — no indent, no extra whitespace.
# All size calculations use this to match the actual wire format
# (Starlette’s JSONResponse also uses indent=None + compact separators).
_COMPACT: tuple[str, str] = (",", ":")


@dataclass
class CacheSection:
    """Pre-computed section metadata for a top-level payload key.

    For sections that fit within _SECTION_CAP_BYTES, ``ready=True`` and the
    value can be served instantly via the fast path.

    For oversized list/string sections the parent is marked ``ready=False``
    and its content is pre-chunked into sub-slices (``scaffold_files.0``,
    ``scaffold_files.1``, …) each ≤ _SECTION_CAP_BYTES.  Sub-slices are
    stored as additional ``CacheSection`` entries with ``ready=True`` and
    ``parent_key`` set to the original key.
    """

    key: str
    byte_size: int
    type_desc: str  # e.g. "list(42 items)", "dict(5 keys)", "str(1200 chars)"
    item_count: int | None  # element count for containers, None for scalars
    ready: bool  # True if byte_size ≤ _SECTION_CAP_BYTES
    parent_key: str | None = None  # set on sub-slices; None on top-level sections
    chunk_index: int | None = None  # sub-slice ordinal (0, 1, …)
    chunk_total: int | None = None  # total sub-slices for the parent
    chunk_items: int | None = None  # items in this sub-slice (lists only)
    content_summary: str | None = None  # semantic description of chunk contents

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
        if self.parent_key is not None:
            d["parent_key"] = self.parent_key
        if self.chunk_index is not None:
            d["chunk_index"] = self.chunk_index
        if self.chunk_total is not None:
            d["chunk_total"] = self.chunk_total
        if self.chunk_items is not None:
            d["chunk_items"] = self.chunk_items
        if self.content_summary is not None:
            d["content_summary"] = self.content_summary
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
    sub_slices: dict[str, Any] = field(default_factory=dict)  # sub-slice key → value
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


def _build_sections(
    payload: dict[str, Any],
) -> tuple[dict[str, CacheSection], dict[str, Any]]:
    """Pre-compute section metadata and pre-chunk oversized sections.

    Returns:
        (sections, sub_slices) where:
        - sections: key → CacheSection (includes both top-level and sub-slice entries)
        - sub_slices: sub-slice key → materialized value for fast retrieval

    Sections ≤ _SECTION_CAP_BYTES are marked ``ready=True``.
    Oversized list/string sections are split into sub-slices, each ≤ _SECTION_CAP_BYTES.
    """
    sections: dict[str, CacheSection] = {}
    sub_slices: dict[str, Any] = {}

    for key, value in payload.items():
        raw = json.dumps(value, separators=_COMPACT, default=str).encode("utf-8")
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

        if byte_size <= _SECTION_CAP_BYTES:
            # Small enough — ready as-is
            sections[key] = CacheSection(
                key=key,
                byte_size=byte_size,
                type_desc=type_desc,
                item_count=item_count,
                ready=True,
            )
        elif isinstance(value, list):
            # Pre-chunk list into sub-slices
            chunks = _chunk_list(value, _SECTION_CAP_BYTES)
            sections[key] = CacheSection(
                key=key,
                byte_size=byte_size,
                type_desc=type_desc,
                item_count=item_count,
                ready=False,
                chunk_total=len(chunks),
            )
            for idx, (chunk_items, chunk_bytes) in enumerate(chunks):
                sub_key = f"{key}.{idx}"
                sub_slices[sub_key] = chunk_items
                sections[sub_key] = CacheSection(
                    key=sub_key,
                    byte_size=chunk_bytes,
                    type_desc=f"list({len(chunk_items)} items)",
                    item_count=len(chunk_items),
                    ready=True,
                    parent_key=key,
                    chunk_index=idx,
                    chunk_total=len(chunks),
                    chunk_items=len(chunk_items),
                    content_summary=_summarize_list_chunk(chunk_items),
                )
        elif isinstance(value, str):
            # Pre-chunk string into sub-slices
            chunks_str = _chunk_string(value, _SECTION_CAP_BYTES)
            sections[key] = CacheSection(
                key=key,
                byte_size=byte_size,
                type_desc=type_desc,
                item_count=item_count,
                ready=False,
                chunk_total=len(chunks_str),
            )
            for idx, (chunk_text, chunk_bytes) in enumerate(chunks_str):
                sub_key = f"{key}.{idx}"
                sub_slices[sub_key] = chunk_text
                sections[sub_key] = CacheSection(
                    key=sub_key,
                    byte_size=chunk_bytes,
                    type_desc=f"str({len(chunk_text)} chars)",
                    item_count=len(chunk_text),
                    ready=True,
                    parent_key=key,
                    chunk_index=idx,
                    chunk_total=len(chunks_str),
                    chunk_items=len(chunk_text),
                )
        else:
            # Oversized dict or other — mark not ready, no sub-slicing
            # Agents can navigate via path traversal
            sections[key] = CacheSection(
                key=key,
                byte_size=byte_size,
                type_desc=type_desc,
                item_count=item_count,
                ready=False,
            )

    return sections, sub_slices


def _chunk_list(items: list[Any], cap: int) -> list[tuple[list[Any], int]]:
    """Split a list into chunks each serializing to ≤ cap bytes.

    Exercises semantic intelligence: when a single dict item exceeds the cap,
    finds its largest list or string field and splits the item into multiple
    partial items — each carrying the full envelope (path, metadata) plus a
    portion of the oversized field, annotated with ``_split`` metadata so
    consumers can reconstruct.

    Returns list of (chunk_items, chunk_byte_size) tuples.
    """
    chunks: list[tuple[list[Any], int]] = []
    current: list[Any] = []
    current_bytes = 2  # opening/closing []

    for item in items:
        item_json = json.dumps(item, separators=_COMPACT, default=str).encode("utf-8")
        item_bytes = len(item_json) + 1  # comma separator

        if item_bytes > cap and isinstance(item, dict):
            # Flush any accumulated items first
            if current:
                chunk_raw = json.dumps(current, separators=_COMPACT, default=str).encode("utf-8")
                chunks.append((current, len(chunk_raw)))
                current = []
                current_bytes = 2

            # Split this oversized item semantically
            parts = _split_oversized_item(item, cap)
            for part in parts:
                part_raw = json.dumps([part], separators=_COMPACT, default=str).encode("utf-8")
                chunks.append(([part], len(part_raw)))
            continue

        if current and current_bytes + item_bytes > cap:
            # Flush current chunk
            chunk_raw = json.dumps(current, separators=_COMPACT, default=str).encode("utf-8")
            chunks.append((current, len(chunk_raw)))
            current = []
            current_bytes = 2

        current.append(item)
        current_bytes += item_bytes

    if current:
        chunk_raw = json.dumps(current, separators=_COMPACT, default=str).encode("utf-8")
        chunks.append((current, len(chunk_raw)))

    return chunks


def _split_oversized_item(item: dict[str, Any], cap: int) -> list[dict[str, Any]]:
    """Split an oversized dict item by finding and partitioning its largest field.

    Recursively locates the largest list or string field (at any nesting depth)
    and splits it across multiple copies of the item, each annotated with
    ``_split`` metadata: ``{field, part, total}``.

    This preserves the item envelope (path, scores, metadata) in every part
    while distributing the heavy payload.

    Uses post-hoc verification: after building parts, checks that each part
    serializes to ≤ cap bytes.  If any part exceeds the cap, the effective
    budget is tightened and the field is re-split — up to 3 retries.
    """
    # Find the largest field path and its value
    field_path, field_val = _find_largest_field(item)

    if field_path is None:
        # Can't split further — return as-is
        return [item]

    # Calculate envelope overhead: serialize the item with an empty field,
    # then subtract from cap so chunks + envelope stay within budget.
    empty_val: list[Any] | str = [] if isinstance(field_val, list) else ""
    envelope_item = _clone_with_field_replaced(item, field_path, empty_val)
    # Add _split metadata size estimate (roughly constant)
    envelope_item["_split"] = {"field": field_path, "part": 0, "total": 99}
    envelope_bytes = len(
        json.dumps(envelope_item, separators=_COMPACT, default=str).encode("utf-8")
    )
    effective_cap = max(cap - envelope_bytes, 1024)  # floor at 1KB

    # Split → build parts → verify.  Retry with tighter budget if any part
    # exceeds cap (indent nesting adds per-element overhead not captured by
    # standalone list serialization).
    _MAX_RETRIES = 3
    for _attempt in range(_MAX_RETRIES):
        sub_chunks: list[Any]
        if isinstance(field_val, list):
            sub_chunks = _chunk_list_simple(field_val, effective_cap)
        elif isinstance(field_val, str):
            sub_chunks = _chunk_string_simple(field_val, effective_cap)
        else:
            return [item]

        if len(sub_chunks) <= 1:
            return [item]

        # Build partial items — each gets the full envelope + a slice
        parts: list[dict[str, Any]] = []
        for idx, chunk_val in enumerate(sub_chunks):
            partial = _clone_with_field_replaced(item, field_path, chunk_val)
            partial["_split"] = {
                "field": field_path,
                "part": idx,
                "total": len(sub_chunks),
            }
            parts.append(partial)

        # Post-hoc verification: every part wrapped in [part] must fit
        # within cap.
        max_part_bytes = max(
            len(json.dumps([p], separators=_COMPACT, default=str).encode("utf-8")) for p in parts
        )
        if max_part_bytes <= cap:
            return parts

        # Tighten budget by measured overshoot + safety margin
        overshoot = max_part_bytes - cap
        effective_cap = max(effective_cap - overshoot - 64, 512)

    # Exhausted retries — return best-effort parts
    return parts


def _find_largest_field(
    obj: dict[str, Any],
    prefix: str = "",
) -> tuple[str | None, Any]:
    """Find the dot-path to the largest serializable field in a nested dict.

    Only considers list and str fields as splittable targets.
    Returns (dot_path, value) or (None, None) if nothing splittable found.
    """
    best_path: str | None = None
    best_val: Any = None
    best_size = 0

    for key, val in obj.items():
        if key.startswith("_"):
            continue
        path = f"{prefix}.{key}" if prefix else key

        if isinstance(val, list | str):
            size = len(json.dumps(val, separators=_COMPACT, default=str).encode("utf-8"))
            if size > best_size:
                best_size = size
                best_path = path
                best_val = val
        elif isinstance(val, dict):
            # Recurse into nested dicts
            sub_path, sub_val = _find_largest_field(val, path)
            if sub_path is not None:
                sub_size = len(
                    json.dumps(sub_val, separators=_COMPACT, default=str).encode("utf-8")
                )
                if sub_size > best_size:
                    best_size = sub_size
                    best_path = sub_path
                    best_val = sub_val

    return best_path, best_val


def _clone_with_field_replaced(
    obj: dict[str, Any],
    field_path: str,
    new_value: Any,
) -> dict[str, Any]:
    """Deep-clone a dict, replacing the value at field_path with new_value."""
    import copy

    clone = copy.deepcopy(obj)
    parts = field_path.split(".")
    target = clone
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = new_value
    return clone


def _chunk_list_simple(items: list[Any], cap: int) -> list[list[Any]]:
    """Split a list into sub-lists each serializing to ≤ cap bytes.

    Simpler variant (returns values only, no byte counts) for intra-item splitting.
    """
    chunks: list[list[Any]] = []
    current: list[Any] = []
    current_bytes = 2

    for item in items:
        item_bytes = len(json.dumps(item, separators=_COMPACT, default=str).encode("utf-8")) + 1
        if current and current_bytes + item_bytes > cap:
            chunks.append(current)
            current = []
            current_bytes = 2
        current.append(item)
        current_bytes += item_bytes

    if current:
        chunks.append(current)
    return chunks


def _chunk_string_simple(text: str, cap: int) -> list[str]:
    """Split a string into sub-strings each ≤ cap bytes (JSON-serialized).

    Splits on newline boundaries for semantic awareness.
    """
    full_bytes = len(json.dumps(text, default=str).encode("utf-8"))
    if full_bytes <= cap:
        return [text]

    lines = text.split("\n")
    chunks: list[str] = []
    current_lines: list[str] = []
    current_bytes = 2

    for line in lines:
        line_bytes = len(line.encode("utf-8")) + 1
        if current_lines and current_bytes + line_bytes > cap:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_bytes = 2
        current_lines.append(line)
        current_bytes += line_bytes

    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks


def _chunk_string(text: str, cap: int) -> list[tuple[str, int]]:
    """Split a string into chunks each ≤ cap bytes when JSON-serialized.

    Tries to split on newline boundaries for semantic awareness.
    Returns list of (chunk_text, chunk_byte_size) tuples.
    """
    # JSON overhead: quotes + escaping.  Rough check: if the whole thing fits, skip.
    full_bytes = len(json.dumps(text, default=str).encode("utf-8"))
    if full_bytes <= cap:
        return [(text, full_bytes)]

    # Split on newline boundaries
    lines = text.split("\n")
    chunks: list[tuple[str, int]] = []
    current_lines: list[str] = []
    current_bytes = 2  # opening/closing quotes in JSON

    for line in lines:
        line_bytes = len(line.encode("utf-8")) + 1  # +1 for \n
        if current_lines and current_bytes + line_bytes > cap:
            chunk_text = "\n".join(current_lines)
            chunk_raw = len(json.dumps(chunk_text, default=str).encode("utf-8"))
            chunks.append((chunk_text, chunk_raw))
            current_lines = []
            current_bytes = 2

        current_lines.append(line)
        current_bytes += line_bytes

    if current_lines:
        chunk_text = "\n".join(current_lines)
        chunk_raw = len(json.dumps(chunk_text, default=str).encode("utf-8"))
        chunks.append((chunk_text, chunk_raw))

    return chunks


class SidecarCache:
    """Thread-safe in-memory cache for oversized MCP responses.

    Key space: (session_id, endpoint_key) → deque(maxlen=3)

    Text rendering:
      ``render_text_slice`` produces terminal-ready plain text for a given
      slice path.  All formatting logic lives here on the server — the
      ``cplcache`` client script is a thin facade that prints the response
      body verbatim.
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
        raw = json.dumps(payload, separators=_COMPACT, default=str).encode("utf-8")
        byte_size = len(raw)
        cache_id = uuid.uuid4().hex[:12]

        if isinstance(payload, dict):
            sections, sub_slices = _build_sections(payload)
        else:
            sections, sub_slices = {}, {}

        entry = CacheEntry(
            cache_id=cache_id,
            session_id=session_id,
            endpoint_key=endpoint_key,
            payload=payload,
            byte_size=byte_size,
            sections=sections,
            sub_slices=sub_slices,
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
        """Extract a sub-path from a cached payload with paginated output.

        Args:
            cache_id: The unique cache entry ID.
            path: Dot-separated JSON path (e.g. "files.0.content"). None = root.
            max_bytes: Maximum bytes to return per page.
            offset: Item offset for lists, character offset for strings.

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
                "has_more": False,
                "ready": True,
            }

        # Fast path: pre-chunked sub-slice (e.g. "scaffold_files.0")
        if path and path in entry.sub_slices and path in entry.sections:
            section = entry.sections[path]
            return {
                "cache_id": cache_id,
                "path": path,
                "type": section.type_desc,
                "value": entry.sub_slices[path],
                "byte_size": section.byte_size,
                "has_more": False,
                "ready": True,
                "chunk_index": section.chunk_index,
                "chunk_total": section.chunk_total,
                "parent_key": section.parent_key,
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

        serialized = json.dumps(value, separators=_COMPACT, default=str)
        if len(serialized) <= max_bytes:
            return {
                "cache_id": cache_id,
                "path": path or "(root)",
                "type": type(value).__name__,
                "value": value,
                "has_more": False,
            }

        # Paginate: for lists, return a page of items starting at offset
        if isinstance(value, list):
            items: list[Any] = []
            used = 2  # []
            for item in value[offset:]:
                item_json = json.dumps(item, separators=_COMPACT, default=str)
                if used + len(item_json) + 1 > max_bytes:
                    break
                items.append(item)
                used += len(item_json) + 1
            next_offset = offset + len(items)
            return {
                "cache_id": cache_id,
                "path": path or "(root)",
                "type": "list",
                "value": items,
                "offset": offset,
                "returned": len(items),
                "total": len(value),
                "has_more": next_offset < len(value),
                "next_offset": next_offset if next_offset < len(value) else None,
            }

        # Dict: return keys so caller can navigate with path
        if isinstance(value, dict):
            keys = list(value.keys())
            return {
                "cache_id": cache_id,
                "path": path or "(root)",
                "type": "dict",
                "keys": keys,
                "total_keys": len(keys),
                "total_bytes": len(serialized),
                "has_more": False,
                "hint": "Use path='<key>' or path='<parent>.<key>' to retrieve individual values.",
            }

        # Scalar fallback (int, float, bool, None)
        return {
            "cache_id": cache_id,
            "path": path or "(root)",
            "type": type(value).__name__,
            "value": value,
            "has_more": False,
        }

    def render_text_slice(
        self,
        cache_id: str,
        path: str | None = None,
    ) -> str | None:
        """Render a slice as terminal-ready plain text.

        This is the primary retrieval path for ``cplcache.py``.
        Returns pre-formatted text ready to print, or None if cache_id
        not found.  The client script prints the result verbatim —
        all formatting decisions are made here on the server.

        Includes a navigation footer for chunked slices showing what
        adjacent chunks contain.
        """
        entry = self.get_entry(cache_id)
        if entry is None:
            return None

        # -- Fast path: pre-chunked sub-slice (e.g. "resolved.0") --
        if path and path in entry.sub_slices and path in entry.sections:
            value = entry.sub_slices[path]
            section = entry.sections[path]
            body = _render_value(value)
            footer = _render_navigation_footer(
                cache_id,
                section,
                entry.sections,
            )
            if footer:
                return body + "\n" + footer
            return body

        # -- Fast path: top-level ready section --
        if (
            path
            and "." not in path
            and isinstance(entry.payload, dict)
            and path in entry.sections
            and entry.sections[path].ready
        ):
            value = entry.payload[path]
            return _render_value(value)

        # -- Top-level key that is NOT ready (chunked parent) --
        if (
            path
            and "." not in path
            and isinstance(entry.payload, dict)
            and path in entry.sections
            and not entry.sections[path].ready
        ):
            section = entry.sections[path]
            lines: list[str] = [
                f"Section '{path}' is {section.type_desc} "
                f"({section.byte_size:,} bytes) — too large for single retrieval.",
                f"Split into {section.chunk_total} chunks:",
                "",
            ]
            for cidx in range(section.chunk_total or 0):
                sub_key = f"{path}.{cidx}"
                sub_sec = entry.sections.get(sub_key)
                if sub_sec:
                    summary = sub_sec.content_summary or f"{sub_sec.chunk_items} item(s)"
                    cmd = (
                        f"python3 .codeplane/scripts/cplcache.py"
                        f' --cache-id "{cache_id}" --slice "{sub_key}"'
                    )
                    lines.append(f"  chunk {cidx}: {summary}")
                    lines.append(f"    {cmd}")
            return "\n".join(lines)

        # -- Navigate to sub-path for arbitrary path traversal --
        if path and isinstance(entry.payload, dict):
            value = entry.payload
            for segment in path.split("."):
                if isinstance(value, dict):
                    if segment not in value:
                        avail = ", ".join(list(value.keys())[:10])
                        return f"Key '{segment}' not found.  Available: {avail}"
                    value = value[segment]
                elif isinstance(value, list):
                    try:
                        idx = int(segment)
                    except ValueError:
                        return (
                            f"Expected integer index, got '{segment}' (list length: {len(value)})"
                        )
                    if idx < 0 or idx >= len(value):
                        return f"Index {idx} out of range (list length: {len(value)})"
                    value = value[idx]
                else:
                    return f"Cannot traverse into {type(value).__name__}"
            return _render_value(value)

        # -- Root or fallback --
        if isinstance(entry.payload, dict):
            # Show top-level key index
            lines_root: list[str] = ["Available sections:", ""]
            for key in entry.payload:
                sec = entry.sections.get(key)
                if sec:
                    status = "ready" if sec.ready else f"{sec.chunk_total} chunks"
                    lines_root.append(f"  {key}: {sec.type_desc} ({status})")
                else:
                    lines_root.append(f"  {key}")
            return "\n".join(lines_root)

        return _render_value(entry.payload)

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


def _summarize_list_chunk(items: list[Any]) -> str:
    """Build a semantic summary of items in a list chunk.

    For file-like items (with ``path``), lists the file paths.
    For scaffold items, lists paths with concise metadata.
    Falls back to item count for opaque items.
    """
    paths: list[str] = []
    for item in items:
        if isinstance(item, dict):
            path = item.get("path", "")
            if path:
                lc = item.get("line_count")
                suffix = f" ({lc} lines)" if lc is not None else ""
                paths.append(f"{path}{suffix}")
    if paths:
        return ", ".join(paths)
    return f"{len(items)} item(s)"


# =========================================================================
# Text Rendering — server-side formatting for terminal consumption
# =========================================================================
# All presentation logic lives here so that cplcache.py is a pure facade
# (fetch URL → print body → exit).  The /sidecar/cache/slice endpoint
# returns pre-formatted text/plain when format=text.


def _render_file_item(item: dict[str, Any]) -> str:
    """Render a resolved or scaffold file item with a metadata header.

    Produces ``# path | key=val ...`` header followed by raw content
    (resolved items) or formatted scaffold text.
    """
    parts: list[str] = []
    path = item.get("path", "")
    if path:
        parts.append(path)
    cid = item.get("candidate_id", "")
    if cid:
        parts.append(f"candidate_id={cid}")
    sha = item.get("file_sha256", "")
    if sha:
        parts.append(f"sha256={sha[:16]}")
    lc = item.get("line_count")
    if lc is not None:
        parts.append(f"{lc} lines")
    ticket = item.get("edit_ticket", "")
    if ticket:
        parts.append(f"edit_ticket={ticket}")
    span = item.get("span")
    if span:
        parts.append(f"span={span.get('start_line')}-{span.get('end_line')}")

    header = "# " + " | ".join(parts) if parts else ""

    content = item.get("content")
    if isinstance(content, str):
        body = content
    else:
        scaffold = item.get("scaffold")
        if isinstance(scaffold, dict):
            body = _render_scaffold(scaffold)
        elif isinstance(scaffold, str):
            body = scaffold
        else:
            body = ""

    if header:
        return header + "\n" + body
    return body


def _compact_symbol(sym: Any) -> str:
    """Render a single symbol as a compact one-liner."""
    if isinstance(sym, dict):
        name = sym.get("name", "")
        kind = sym.get("kind", "")
        line = sym.get("line")
        sig = sym.get("signature", "")
        if sig:
            # Collapse whitespace in multi-line signatures
            compact = " ".join(sig.split())
            loc = f"  [L{line}]" if line is not None else ""
            return compact + loc
        parts: list[str] = []
        if kind:
            parts.append(kind)
        if name:
            parts.append(name)
        if line is not None:
            parts.append(f"[L{line}]")
        return " ".join(parts)
    # Fallback: collapse multi-line str representations to single line
    return " ".join(str(sym).split())


def _render_scaffold(scaffold: dict[str, Any]) -> str:
    """Render a scaffold dict as concise readable text."""
    lines: list[str] = []
    summary = scaffold.get("summary", "")
    if summary:
        lines.append(summary)
    else:
        lang = scaffold.get("language", "")
        total = scaffold.get("total_lines")
        meta: list[str] = []
        if lang:
            meta.append(lang)
        if total is not None:
            meta.append(f"{total} lines")
        if meta:
            lines.append(" | ".join(meta))
    imports = scaffold.get("imports", [])
    if imports:
        lines.append(f"imports: {', '.join(str(i) for i in imports)}")
    symbols = scaffold.get("symbols", [])
    if symbols:
        for s in symbols:
            lines.append(f"  {_compact_symbol(s)}")
    return "\n".join(lines)


def _render_list(items: list[Any]) -> str:
    """Render a list of items to text.

    File-like items (with ``content`` or ``scaffold``) get metadata headers.
    Other dicts are compact JSON.  Non-dicts are str().
    """
    formatted: list[str] = []
    for item in items:
        if isinstance(item, dict) and ("content" in item or "scaffold" in item):
            formatted.append(_render_file_item(item))
        elif isinstance(item, dict):
            formatted.append(json.dumps(item, separators=_COMPACT))
        else:
            formatted.append(str(item))
    return "\n---\n".join(formatted)


def _render_value(value: Any) -> str:
    """Render an arbitrary value to terminal-ready text."""
    if isinstance(value, list):
        return _render_list(value)
    if isinstance(value, dict) and ("content" in value or "scaffold" in value):
        return _render_file_item(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=_COMPACT)


def _render_navigation_footer(
    cache_id: str,
    section: CacheSection | None,
    all_sections: dict[str, CacheSection],
) -> str:
    """Build a navigation footer for chunked slices.

    Shows chunk position (N/M) and lists what adjacent chunks contain
    using their content_summary.
    """
    if section is None or section.parent_key is None or section.chunk_total is None:
        return ""

    parts: list[str] = []
    parts.append(f"--- chunk {(section.chunk_index or 0) + 1}/{section.chunk_total} ---")

    # List adjacent chunks with their content summaries
    for cidx in range(section.chunk_total):
        if cidx == section.chunk_index:
            continue
        sub_key = f"{section.parent_key}.{cidx}"
        peer = all_sections.get(sub_key)
        if peer and peer.content_summary:
            cmd = (
                f"python3 .codeplane/scripts/cplcache.py"
                f' --cache-id "{cache_id}" --slice "{sub_key}"'
            )
            parts.append(f"  chunk {cidx}: {peer.content_summary}")
            parts.append(f"    {cmd}")

    return "\n".join(parts)


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


def cache_render(
    cache_id: str,
    path: str | None = None,
) -> str | None:
    """Module-level convenience: render a slice as terminal-ready text."""
    return _sidecar_cache.render_text_slice(cache_id, path)


def cache_meta(cache_id: str) -> dict[str, Any] | None:
    """Module-level convenience: get metadata for a cached entry."""
    return _sidecar_cache.get_meta(cache_id)


def get_sidecar_cache() -> SidecarCache:
    """Return the global sidecar cache instance."""
    return _sidecar_cache
