"""Unified delivery envelope for MCP tool responses.

Provides:
- DeliveryEnvelope: uniform response shape for all endpoints
- ResourceCache: disk-backed cache for resource-mode payloads
- ClientProfile: static client capability profiles
- build_envelope: decide inline/resource delivery
- resolve_profile: select client profile from connection info
"""

from __future__ import annotations

import contextvars
import json
import os
import sys
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from codeplane.config.constants import INLINE_CAP_BYTES
from codeplane.config.user_config import DEFAULT_PORT

log = structlog.get_logger(__name__)

# Server port for resource fetch hints (set during startup, fallback to default)
_server_port: int = DEFAULT_PORT

# Cache directory for disk-persisted resources (set during startup)
_cache_dir: Path | None = None


def set_server_port(port: int) -> None:
    """Set the server port for resource fetch hints."""
    global _server_port  # noqa: PLW0603
    _server_port = port


def set_cache_dir(repo_root: Path) -> None:
    """Set and create the disk cache directory for resource payloads.

    Wipes any previous cache — cached resources are only valid for the
    current server session (random UUIDs, not persisted).
    """
    import shutil

    global _cache_dir  # noqa: PLW0603
    _cache_dir = repo_root / ".codeplane" / "cache"
    if _cache_dir.exists():
        shutil.rmtree(_cache_dir)
    _cache_dir.mkdir(parents=True, exist_ok=True)


# =============================================================================
# Cursor Store — server-side pagination state
# =============================================================================

_CURSOR_TTL_SECONDS = 300  # 5 minutes
_CURSOR_MAX_ENTRIES = 50


@dataclass
class PendingCursor:
    """Server-side state for a paginated response."""

    cursor_id: str
    resource_kind: str
    items: list[dict[str, Any]]  # all items (files for source, results for search)
    items_key: str  # 'files' or 'results' — the key in the payload
    extra_fields: dict[str, Any]  # non-paginated fields to echo in every page
    page_index: int  # next item index to return
    item_line_offset: int  # line offset within current item for content splitting
    created_at: float
    inline_cap: int

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.created_at) > _CURSOR_TTL_SECONDS

    @property
    def has_more(self) -> bool:
        return self.page_index < len(self.items)


# OrderedDict for LRU eviction
_CURSOR_STORE: OrderedDict[str, PendingCursor] = OrderedDict()
_cursor_lock = threading.Lock()


def _evict_cursors() -> None:
    """Remove expired cursors and enforce max entries. Caller holds lock."""
    now = time.monotonic()
    expired = [k for k, v in _CURSOR_STORE.items() if (now - v.created_at) > _CURSOR_TTL_SECONDS]
    for k in expired:
        del _CURSOR_STORE[k]
    while len(_CURSOR_STORE) > _CURSOR_MAX_ENTRIES:
        _CURSOR_STORE.popitem(last=False)


def _store_cursor(cursor: PendingCursor) -> None:
    """Store a cursor, evicting stale entries first."""
    with _cursor_lock:
        _evict_cursors()
        _CURSOR_STORE[cursor.cursor_id] = cursor


def _get_cursor(cursor_id: str) -> PendingCursor | None:
    """Retrieve and validate a cursor. Returns None if expired/missing."""
    with _cursor_lock:
        cursor = _CURSOR_STORE.get(cursor_id)
        if cursor is None or cursor.expired:
            if cursor is not None:
                del _CURSOR_STORE[cursor_id]
            return None
        # Move to end (LRU)
        _CURSOR_STORE.move_to_end(cursor_id)
        return cursor


def _remove_cursor(cursor_id: str) -> None:
    """Remove a cursor after final page."""
    with _cursor_lock:
        _CURSOR_STORE.pop(cursor_id, None)


# =============================================================================
# Paginated Envelope Builder
# =============================================================================

# Kinds that support cursor pagination (list-of-items payloads)
_PAGINATED_KINDS: dict[str, str] = {
    "source": "files",
    "search_hits": "results",
}


def _fit_items(
    items: list[dict[str, Any]],
    start: int,
    cap_bytes: int,
    overhead_bytes: int,
) -> int:
    """Return how many items from items[start:] fit within cap_bytes - overhead_bytes.

    Always returns at least 1 (a single item is never split).
    """
    available = cap_bytes - overhead_bytes
    total = 0
    count = 0
    for i in range(start, len(items)):
        item_size = len(json.dumps(items[i], indent=2).encode("utf-8"))
        if count > 0 and total + item_size > available:
            break
        total += item_size
        count += 1
    return max(1, count)


def _envelope_overhead(cursor: PendingCursor) -> int:
    """Estimate byte overhead of envelope fields (excluding items)."""
    return len(
        json.dumps(
            {
                "resource_kind": cursor.resource_kind,
                "delivery": "inline",
                "cursor": cursor.cursor_id,
                "has_more": True,
                "page_info": {"returned": 0, "remaining": 0, "total": 0},
                **{k: v for k, v in cursor.extra_fields.items() if k != cursor.items_key},
            },
            indent=2,
        ).encode("utf-8")
    )


def _split_content_item(
    item: dict[str, Any],
    line_offset: int,
    budget_bytes: int,
) -> tuple[dict[str, Any], int, bool]:
    """Split an oversized item's ``content`` field to fit within *budget_bytes*.

    Returns ``(partial_item, new_line_offset, item_complete)``.
    """
    content: str = item.get("content", "")
    lines = content.split("\n")
    total_lines = len(lines)

    remaining_lines = lines[line_offset:]

    # Build a template to estimate per-item overhead (everything except content)
    template = {k: v for k, v in item.items() if k != "content"}
    template["content"] = ""
    template["content_truncated"] = True
    template["content_lines_delivered"] = 0
    template["content_lines_total"] = total_lines
    item_overhead = len(json.dumps(template, indent=2).encode("utf-8"))
    available = max(budget_bytes - item_overhead, 0)

    # Pack as many lines as fit
    chunk: list[str] = []
    chunk_bytes = 0
    for line in remaining_lines:
        lb = len((line + "\n").encode("utf-8"))
        if chunk and chunk_bytes + lb > available:
            break
        chunk.append(line)
        chunk_bytes += lb
    # Always include at least 1 line
    if not chunk and remaining_lines:
        chunk = [remaining_lines[0]]

    new_offset = line_offset + len(chunk)
    item_complete = new_offset >= total_lines

    partial: dict[str, Any] = dict(item)
    partial["content"] = "\n".join(chunk)
    partial["line_count"] = len(chunk)

    # Adjust range to reflect delivered lines
    if "range" in partial:
        orig_start: int = partial["range"][0]
        partial["range"] = [orig_start + line_offset, orig_start + new_offset - 1]

    if not item_complete:
        partial["content_truncated"] = True
        partial["content_lines_delivered"] = len(chunk)
        partial["content_lines_total"] = total_lines
        partial["content_offset"] = line_offset

    return partial, new_offset, item_complete


def _build_page(
    cursor: PendingCursor,
) -> dict[str, Any]:
    """Build one page from a cursor, advancing the page_index.

    Handles two cases:
    1. Normal: pack as many whole items as fit.
    2. Oversized: a single item's content exceeds the inline cap —
       split its ``content`` field across pages by line.
    """
    overhead = _envelope_overhead(cursor)
    budget = cursor.inline_cap - overhead

    # --- Determine if we're mid-item (content-split in progress) ---
    if cursor.item_line_offset > 0:
        # Continue splitting current item
        item = cursor.items[cursor.page_index]
        partial, new_offset, item_complete = _split_content_item(
            item, cursor.item_line_offset, budget
        )
        page_items = [partial]
        if item_complete:
            cursor.item_line_offset = 0
            cursor.page_index += 1
        else:
            cursor.item_line_offset = new_offset
    else:
        count = _fit_items(cursor.items, cursor.page_index, cursor.inline_cap, overhead)
        page_items = cursor.items[cursor.page_index : cursor.page_index + count]

        # Check if the single item exceeds budget — need content splitting
        if count == 1:
            item_bytes = len(json.dumps(page_items[0], indent=2).encode("utf-8"))
            if item_bytes > budget and "content" in page_items[0]:
                partial, new_offset, item_complete = _split_content_item(page_items[0], 0, budget)
                page_items = [partial]
                if item_complete:
                    cursor.page_index += 1
                else:
                    cursor.item_line_offset = new_offset
                    # page_index stays (still on this item)
            else:
                cursor.page_index += count
        else:
            cursor.page_index += count

    has_more = cursor.has_more
    remaining = len(cursor.items) - cursor.page_index

    envelope: dict[str, Any] = {
        "resource_kind": cursor.resource_kind,
        "delivery": "inline",
        cursor.items_key: page_items,
    }

    # Echo extra fields (summary, not_found, etc.) in every page
    for k, v in cursor.extra_fields.items():
        if k != cursor.items_key:
            envelope[k] = v

    envelope["page_info"] = {
        "returned": len(page_items),
        "remaining": remaining,
        "total": len(cursor.items),
    }

    if has_more:
        envelope["cursor"] = cursor.cursor_id
        envelope["has_more"] = True
    else:
        envelope["has_more"] = False
        _remove_cursor(cursor.cursor_id)

    return envelope


def resume_cursor(cursor_id: str) -> dict[str, Any] | None:
    """Resume a paginated response. Returns next page or None if cursor expired/invalid."""
    cursor = _get_cursor(cursor_id)
    if cursor is None:
        return None
    return _build_page(cursor)


def _try_paginate(
    payload: dict[str, Any],
    resource_kind: str,
    inline_cap: int,
    inline_summary: str | None = None,
) -> dict[str, Any] | None:
    """If this kind supports pagination and the payload overflows, start a cursor.

    Returns the first page envelope, or None if pagination doesn't apply.
    Handles single oversized items via content-level splitting.
    """
    items_key = _PAGINATED_KINDS.get(resource_kind)
    if items_key is None:
        return None

    items = payload.get(items_key)
    if not isinstance(items, list) or len(items) == 0:
        return None

    cursor_id = uuid.uuid4().hex[:12]
    extra = {k: v for k, v in payload.items() if k != items_key}
    if inline_summary:
        extra["inline_summary"] = inline_summary

    cursor = PendingCursor(
        cursor_id=cursor_id,
        resource_kind=resource_kind,
        items=items,
        items_key=items_key,
        extra_fields=extra,
        page_index=0,
        item_line_offset=0,
        created_at=time.monotonic(),
        inline_cap=inline_cap,
    )

    _store_cursor(cursor)
    return _build_page(cursor)


# =============================================================================
# Disk-Cache Fetch Hints (for non-paginated overflow)
# =============================================================================


def _build_fetch_hint(
    resource_id: str,
    byte_size: int,
    kind: str,
    payload: Any = None,
) -> str:
    """Build a concise agentic hint with real data from the payload.

    No placeholder commands. No grep '<filename>'. Only actionable info:
    - Byte size + disk path
    - Extracted structural summary (counts, names, actual data)
    - OS-appropriate retrieval command
    """
    rel_path = f".codeplane/cache/{kind}/{resource_id}.json"
    cmd = f"type {rel_path.replace('/', os.sep)}" if sys.platform == "win32" else f"cat {rel_path}"
    header = f"Full result ({byte_size:,} bytes) cached to disk. Retrieve with: {cmd}"

    if not isinstance(payload, dict):
        return header

    detail = _extract_summary(kind, payload)
    if detail:
        return f"{header}\n{detail}"
    return header


def _extract_summary(kind: str, payload: dict[str, Any]) -> str:
    """Extract a compact, actionable summary from a payload.

    Returns real data — file paths, change counts, commit SHAs — not placeholders.
    """
    if kind == "source":
        files = payload.get("files", [])
        if not files:
            return ""
        parts = []
        for f in files:
            fp = f.get("path", "?")
            r = f.get("range")
            lc = f.get("line_count", "?")
            parts.append(f"  {fp} L{r[0]}-L{r[1]} ({lc} lines)" if r and len(r) == 2 else f"  {fp}")
        return f"{len(files)} file(s):\n" + "\n".join(parts)

    if kind == "search_hits":
        results = payload.get("results", [])
        by_file: dict[str, int] = {}
        for r in results:
            p = r.get("path", "?")
            by_file[p] = by_file.get(p, 0) + 1
        dist = ", ".join(f"{p} ({c})" for p, c in list(by_file.items())[:5])
        more = f" +{len(by_file) - 5} more" if len(by_file) > 5 else ""
        return f"{len(results)} hit(s) across {len(by_file)} file(s): {dist}{more}"

    if kind == "semantic_diff":
        changes = payload.get("structural_changes", payload.get("changes", []))
        summary = payload.get("summary", "")
        if summary:
            return str(summary)
        ct: dict[str, int] = {}
        for c in changes:
            t = c.get("change", c.get("change_type", "?"))
            ct[t] = ct.get(t, 0) + 1
        return f"{len(changes)} change(s): " + ", ".join(f"{v} {k}" for k, v in ct.items())

    if kind == "diff":
        diff_text = payload.get("diff", payload.get("diff_text", ""))
        if isinstance(diff_text, str):
            n = diff_text.count("diff --git")
            return f"{n} file(s) changed" if n else ""
        return ""

    if kind == "test_output":
        p, f_count = payload.get("passed", 0), payload.get("failed", 0)
        return f"{p} passed, {f_count} failed, {payload.get('total', p + f_count)} total"

    if kind == "refactor_preview":
        matches = payload.get("matches", [])
        af = len({m.get("path", "") for m in matches})
        return f"{len(matches)} match(es) across {af} file(s)"

    if kind == "log":
        results = payload.get("results", [])
        if not results:
            return "0 commits"
        first = results[0]
        sha = first.get("short_sha", first.get("sha", "?")[:7])
        msg = (first.get("message", "") or "")[:60]
        return f"{len(results)} commit(s), latest: {sha} {msg}"

    if kind == "commit":
        sha = payload.get("short_sha", payload.get("sha", "?")[:7])
        msg = (payload.get("message", "") or "")[:80]
        return f"Commit {sha}: {msg}"

    if kind == "blame":
        results = payload.get("results", [])
        bp = payload.get("path", "")
        authors = len({r.get("author", "") for r in results})
        return f"Blame {bp}: {len(results)} hunk(s), {authors} author(s)"

    if kind == "repo_map":
        skip = {"summary", "resource_kind", "delivery"}
        sections = [k for k in payload if k not in skip]
        return f"Sections: {', '.join(sections)}" if sections else ""

    return ""


# =============================================================================
# Client Profiles
# =============================================================================


@dataclass(frozen=True)
class ClientProfile:
    """Static client capability profile."""

    name: str
    inline_cap_bytes: int = INLINE_CAP_BYTES


PROFILES: dict[str, ClientProfile] = {
    "default": ClientProfile(name="default"),
    "copilot_coding_agent": ClientProfile(name="copilot_coding_agent"),
    "vscode_chat": ClientProfile(name="vscode_chat"),
    "Visual Studio Code": ClientProfile(name="Visual Studio Code"),
}


def resolve_profile(
    client_info: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,  # noqa: ARG001
    config_override: str | None = None,
) -> ClientProfile:
    """Resolve client profile from connection info.

    Priority: explicit config override > clientInfo.name > default.
    """
    # 1. Explicit override
    if config_override and config_override in PROFILES:
        profile = PROFILES[config_override]
        log.debug("profile_resolved", source="config_override", profile=profile.name)
        return profile

    # 2. clientInfo.name match
    if client_info:
        name = client_info.get("name", "")
        if name in PROFILES:
            profile = PROFILES[name]
            log.debug("profile_resolved", source="client_name", profile=profile.name)
            return profile

    # 3. Default
    profile = PROFILES["default"]
    log.debug("profile_resolved", source="default", profile=profile.name)
    return profile


# Per-request client profile (set by middleware, read by envelope builders)
_current_profile: contextvars.ContextVar[ClientProfile | None] = contextvars.ContextVar(
    "_current_profile", default=None
)


def set_current_profile(profile: ClientProfile) -> None:
    """Set the resolved client profile for the current request context."""
    _current_profile.set(profile)


def get_current_profile() -> ClientProfile:
    """Get the resolved client profile for the current request, or default."""
    return _current_profile.get() or PROFILES["default"]


# =============================================================================
# Resource Cache (disk-backed, no in-memory state)
# =============================================================================


class ResourceCache:
    """Disk-backed resource cache.

    All payloads are written as compact JSON to _cache_dir/{kind}/{id}.json.
    No in-memory state, no TTL, no LRU — the filesystem IS the cache.
    """

    def store(
        self,
        payload: Any,
        kind: str,
    ) -> tuple[str, int]:
        """Serialize payload to disk and return (resource_id, byte_size)."""
        if isinstance(payload, dict | list):
            raw = json.dumps(payload, indent=2, sort_keys=False).encode("utf-8")
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            raw = payload
        else:
            raw = json.dumps(payload, indent=2, default=str).encode("utf-8")

        resource_id = uuid.uuid4().hex[:12]

        if _cache_dir is None:
            log.warning("resource_cache_no_dir", resource_id=resource_id)
        else:
            kind_dir = _cache_dir / kind
            kind_dir.mkdir(parents=True, exist_ok=True)
            disk_path = kind_dir / f"{resource_id}.json"
            disk_path.write_bytes(raw)

        log.debug(
            "resource_cached",
            resource_id=resource_id,
            kind=kind,
            byte_size=len(raw),
        )

        return resource_id, len(raw)


# Global resource cache instance
_resource_cache = ResourceCache()


# =============================================================================
# Delivery Envelope
# =============================================================================


def build_envelope(
    payload: dict[str, Any],
    *,
    resource_kind: str,
    client_profile: ClientProfile | None = None,
    scope_id: str | None = None,
    scope_usage: dict[str, Any] | None = None,
    inline_summary: str | None = None,
) -> dict[str, Any]:
    """Build a delivery envelope around a payload.

    Rules:
    - Payload fits inline -> delivery="inline"
    - Doesn't fit -> delivery="resource", full payload written to disk
    """
    profile = client_profile or get_current_profile()
    inline_cap = profile.inline_cap_bytes

    # Measure payload size
    payload_bytes = len(json.dumps(payload, indent=2).encode("utf-8"))

    # Build base envelope
    envelope: dict[str, Any] = {
        "resource_kind": resource_kind,
    }

    if payload_bytes <= inline_cap:
        # Inline delivery
        envelope["delivery"] = "inline"
        envelope.update(payload)
        envelope["inline_budget_bytes_used"] = payload_bytes
        envelope["inline_budget_bytes_limit"] = inline_cap
        if inline_summary:
            envelope["inline_summary"] = inline_summary
    else:
        # Try cursor pagination first for supported kinds
        paginated = _try_paginate(payload, resource_kind, inline_cap, inline_summary)
        if paginated is not None:
            if scope_id:
                paginated["scope_id"] = scope_id
            if scope_usage:
                paginated["scope_usage"] = scope_usage
            log.debug(
                "envelope_built",
                delivery="paginated",
                resource_kind=resource_kind,
                payload_bytes=payload_bytes,
                inline_cap=inline_cap,
                scope_id=scope_id,
            )
            return paginated

        # Fallback: write full payload to disk
        resource_id, byte_size = _resource_cache.store(payload, resource_kind)
        envelope["delivery"] = "resource"
        if inline_summary:
            envelope["inline_summary"] = inline_summary
        envelope["inline_budget_bytes_used"] = len(
            json.dumps(envelope, indent=2, default=str).encode("utf-8")
        )
        envelope["inline_budget_bytes_limit"] = inline_cap
        envelope["agentic_hint"] = _build_fetch_hint(resource_id, byte_size, resource_kind, payload)

    # Echo scope
    if scope_id:
        envelope["scope_id"] = scope_id
    if scope_usage:
        envelope["scope_usage"] = scope_usage

    log.debug(
        "envelope_built",
        delivery=envelope["delivery"],
        resource_kind=resource_kind,
        payload_bytes=payload_bytes,
        inline_cap=inline_cap,
        scope_id=scope_id,
    )

    return envelope


def wrap_existing_response(
    result: dict[str, Any],
    *,
    resource_kind: str,
    scope_id: str | None = None,
    scope_usage: dict[str, Any] | None = None,
    client_profile: ClientProfile | None = None,
    inline_summary: str | None = None,
) -> dict[str, Any]:
    """Add delivery envelope fields to an existing handler response.

    Routes oversized payloads to disk via resource delivery.
    """
    profile = client_profile or get_current_profile()
    inline_cap = profile.inline_cap_bytes

    payload_bytes = len(json.dumps(result, indent=2, default=str).encode("utf-8"))

    if payload_bytes <= inline_cap:
        # Inline delivery
        result["resource_kind"] = resource_kind
        result["delivery"] = "inline"
        result["inline_budget_bytes_used"] = payload_bytes
        result["inline_budget_bytes_limit"] = inline_cap
        if inline_summary:
            result["inline_summary"] = inline_summary
    else:
        # Try cursor pagination first for supported kinds
        paginated = _try_paginate(result, resource_kind, inline_cap, inline_summary)
        if paginated is not None:
            if scope_id:
                paginated["scope_id"] = scope_id
            if scope_usage:
                paginated["scope_usage"] = scope_usage
            log.debug(
                "envelope_wrapped",
                delivery="paginated",
                resource_kind=resource_kind,
                payload_bytes=payload_bytes,
                inline_cap=inline_cap,
                scope_id=scope_id,
            )
            return paginated

        # Fallback: store full result on disk, return synopsis
        resource_id, byte_size = _resource_cache.store(result, resource_kind)
        envelope: dict[str, Any] = {
            "resource_kind": resource_kind,
            "delivery": "resource",
        }
        if inline_summary:
            envelope["inline_summary"] = inline_summary
        envelope["inline_budget_bytes_used"] = len(
            json.dumps(envelope, indent=2, default=str).encode("utf-8")
        )
        envelope["inline_budget_bytes_limit"] = inline_cap
        if scope_id:
            envelope["scope_id"] = scope_id
        if scope_usage:
            envelope["scope_usage"] = scope_usage
        envelope["agentic_hint"] = _build_fetch_hint(resource_id, byte_size, resource_kind, result)

        log.debug(
            "envelope_wrapped",
            delivery="resource",
            resource_kind=resource_kind,
            payload_bytes=payload_bytes,
            inline_cap=inline_cap,
            scope_id=scope_id,
        )
        return envelope

    if scope_id:
        result["scope_id"] = scope_id
    if scope_usage:
        result["scope_usage"] = scope_usage

    log.debug(
        "envelope_wrapped",
        delivery=result["delivery"],
        resource_kind=resource_kind,
        payload_bytes=payload_bytes,
        inline_cap=inline_cap,
        scope_id=scope_id,
    )

    return result


# =============================================================================
# Scope Budgets
# =============================================================================


@dataclass
class ScopeBudget:
    """Per-scope usage tracking with budget enforcement."""

    scope_id: str
    created_at: float = field(default_factory=time.monotonic)
    last_active: float = field(default_factory=time.monotonic)

    # Counters
    read_bytes_total: int = 0
    full_file_reads: int = 0
    read_calls: int = 0
    search_calls: int = 0
    search_hits_returned_total: int = 0
    paged_continuations: int = 0

    # Limits (defaults, can be overridden)
    max_read_bytes_total: int = 10_000_000  # 10MB
    max_full_file_reads: int = 50
    max_read_calls: int = 200
    max_search_calls: int = 100
    max_search_hits_returned_total: int = 5000
    max_paged_continuations: int = 500
    # Duplicate read tracking
    _full_read_history: dict[str, int] = field(default_factory=dict)
    _mutation_epoch: int = field(default=0)

    # Budget reset tracking
    _read_reset_eligible_at_epoch: int = field(default=-1)
    _search_reset_eligible_at_epoch: int = field(default=-1)
    _total_resets: int = field(default=0)
    _reset_log: list[dict[str, Any]] = field(default_factory=list)
    mutations_for_search_reset: int = field(default=3)

    def touch(self) -> None:
        self.last_active = time.monotonic()

    def increment_read(self, byte_count: int) -> None:
        self.read_bytes_total += byte_count
        self.read_calls += 1
        self.touch()

    def increment_full_read(self, path: str, byte_count: int) -> None:
        self.full_file_reads += 1
        self.read_bytes_total += byte_count
        self.read_calls += 1
        # Track for duplicate detection
        self._full_read_history[path] = self._full_read_history.get(path, 0) + 1
        self.touch()

    def increment_search(self, hits: int) -> None:
        self.search_calls += 1
        self.search_hits_returned_total += hits
        self.touch()

    def increment_paged(self) -> None:
        self.paged_continuations += 1
        self.touch()

    def record_mutation(self) -> None:
        """Record a mutation and update budget reset eligibility.

        - Read budget becomes eligible for reset immediately (next epoch)
        - Search budget becomes eligible every N mutations
        """
        self._mutation_epoch += 1
        self._full_read_history.clear()
        # Read reset: eligible after any mutation
        self._read_reset_eligible_at_epoch = self._mutation_epoch
        # Search reset: eligible every N mutations
        if self._mutation_epoch % self.mutations_for_search_reset == 0:
            self._search_reset_eligible_at_epoch = self._mutation_epoch

    def request_reset(self, category: str, justification: str) -> dict[str, Any]:
        """Request a budget reset. Requires eligibility and justification.

        Args:
            category: 'read' or 'search'
            justification: Why the reset is needed.
                Post-mutation: max 50 chars.
                No-mutation (ceiling reset): max 250 chars.

        Returns:
            Dict with reset result, counters before/after, and justification.

        Raises:
            ValueError: If category invalid, justification too short/long,
                or reset not eligible.
        """
        if category not in ("read", "search"):
            msg = f"Invalid reset category: {category!r}. Must be 'read' or 'search'."
            raise ValueError(msg)

        justification = justification.strip()
        if len(justification) < 50:
            msg = "Justification must be at least 50 characters."
            raise ValueError(msg)

        # Determine eligibility
        has_mutations = self._mutation_epoch > 0
        if category == "read":
            eligible = self._read_reset_eligible_at_epoch == self._mutation_epoch
            counters = ["read_bytes_total", "full_file_reads", "read_calls"]
            check_keys = ["read_bytes", "full_reads", "read_calls"]
            # No-mutation path: agent can request read reset at ceiling
            if not eligible and not has_mutations:
                at_ceiling = any(self.check_budget(c) is not None for c in check_keys)
                if at_ceiling and len(justification) >= 250:
                    eligible = True
                elif at_ceiling:
                    msg = (
                        "No-mutation read reset requires justification "
                        f"of at least 250 characters (got {len(justification)})."
                    )
                    raise ValueError(msg)
        else:  # search
            eligible = self._search_reset_eligible_at_epoch == self._mutation_epoch
            counters = ["search_calls", "search_hits_returned_total", "paged_continuations"]
            check_keys = ["search_calls", "search_hits", "paged_continuations"]
            # No-mutation path for search
            if not eligible and not has_mutations:
                at_ceiling = any(self.check_budget(c) is not None for c in check_keys)
                if at_ceiling and len(justification) >= 250:
                    eligible = True
                elif at_ceiling:
                    msg = (
                        "No-mutation search reset requires justification "
                        f"of at least 250 characters (got {len(justification)})."
                    )
                    raise ValueError(msg)

        if not eligible:
            if category == "read":
                msg = "Read budget reset requires at least one mutation since last reset."
            else:
                msg = (
                    f"Search budget reset requires {self.mutations_for_search_reset} "
                    f"mutations (current epoch: {self._mutation_epoch})."
                )
            raise ValueError(msg)

        # Capture before state
        before = {c: getattr(self, c) for c in counters}

        # Reset counters
        for c in counters:
            setattr(self, c, 0)
        if category == "read":
            self._full_read_history.clear()
            self._read_reset_eligible_at_epoch = -1
        else:
            self._search_reset_eligible_at_epoch = -1

        self._total_resets += 1
        self._reset_log.append(
            {
                "category": category,
                "justification": justification,
                "epoch": self._mutation_epoch,
                "before": before,
                "has_mutations": has_mutations,
            }
        )

        return {
            "reset": True,
            "category": category,
            "before": before,
            "after": dict.fromkeys(counters, 0),
            "total_resets": self._total_resets,
            "epoch": self._mutation_epoch,
        }

    def check_duplicate_read(self, path: str) -> dict[str, Any] | None:
        """Check for duplicate full read, return warning if detected."""
        count = self._full_read_history.get(path, 0)
        if count >= 2:
            return {
                "code": "DUPLICATE_FULL_READ",
                "path": path,
                "count": count,
                "scope_id": self.scope_id,
            }
        return None

    def check_budget(self, counter: str) -> str | None:
        """Check if a budget counter is exceeded. Returns hint or None."""
        checks = {
            "read_bytes": (
                self.read_bytes_total,
                self.max_read_bytes_total,
                "Reduce read scope or use search to find specific content.",
            ),
            "full_reads": (
                self.full_file_reads,
                self.max_full_file_reads,
                "Use read_source with spans instead of full file reads.",
            ),
            "read_calls": (
                self.read_calls,
                self.max_read_calls,
                "Batch reads into fewer calls with multiple targets.",
            ),
            "search_calls": (
                self.search_calls,
                self.max_search_calls,
                "Refine search queries to reduce call count.",
            ),
            "search_hits": (
                self.search_hits_returned_total,
                self.max_search_hits_returned_total,
                "Use filter_paths or filter_kinds to narrow results.",
            ),
            "paged_continuations": (
                self.paged_continuations,
                self.max_paged_continuations,
                "Reduce result sets or use more specific queries.",
            ),
        }
        if counter in checks:
            current, limit, hint = checks[counter]
            if current > limit:
                return hint
        return None

    def to_usage_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "read_bytes": self.read_bytes_total,
            "full_reads": self.full_file_reads,
            "read_calls": self.read_calls,
            "search_calls": self.search_calls,
            "search_hits": self.search_hits_returned_total,
            "paged_continuations": self.paged_continuations,
            "mutation_epoch": self._mutation_epoch,
            "total_resets": self._total_resets,
        }
        # Mutation-path availability
        read_available = self._read_reset_eligible_at_epoch == self._mutation_epoch
        search_available = self._search_reset_eligible_at_epoch == self._mutation_epoch
        # Pure-read path: available at ceiling when no mutations
        if self._mutation_epoch == 0:
            read_keys = ["read_bytes", "full_reads", "read_calls"]
            search_keys = ["search_calls", "search_hits", "paged_continuations"]
            if any(self.check_budget(c) is not None for c in read_keys):
                read_available = True
            if any(self.check_budget(c) is not None for c in search_keys):
                search_available = True
        if read_available:
            result["read_reset_available"] = True
        if search_available:
            result["search_reset_available"] = True
        return result

    def is_expired(self, ttl_seconds: float = 3600.0) -> bool:
        return (time.monotonic() - self.last_active) > ttl_seconds


class ScopeManager:
    """Manages per-scope budgets. Thread-safe, TTL-evicted."""

    def __init__(self, ttl_seconds: float = 3600.0, max_scopes: int = 100) -> None:
        self._scopes: OrderedDict[str, ScopeBudget] = OrderedDict()
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max = max_scopes

    def get_or_create(self, scope_id: str) -> ScopeBudget:
        with self._lock:
            if scope_id in self._scopes:
                budget = self._scopes[scope_id]
                if budget.is_expired(self._ttl):
                    del self._scopes[scope_id]
                else:
                    budget.touch()
                    self._scopes.move_to_end(scope_id)
                    return budget

            budget = ScopeBudget(scope_id=scope_id)
            self._scopes[scope_id] = budget
            # Evict oldest
            while len(self._scopes) > self._max:
                self._scopes.popitem(last=False)
            return budget

    def get(self, scope_id: str) -> ScopeBudget | None:
        with self._lock:
            budget = self._scopes.get(scope_id)
            if budget and not budget.is_expired(self._ttl):
                return budget
            return None

    def record_mutation(self, scope_id: str) -> None:
        """Record a mutation event and update reset eligibility."""
        with self._lock:
            budget = self._scopes.get(scope_id)
            if budget:
                budget.record_mutation()

    def request_reset(self, scope_id: str, category: str, justification: str) -> dict[str, Any]:
        """Request a budget reset for a scope. Thread-safe."""
        with self._lock:
            budget = self._scopes.get(scope_id)
            if not budget:
                msg = f"No budget found for scope '{scope_id}'."
                raise ValueError(msg)
            return budget.request_reset(category, justification)

    def cleanup_expired(self) -> int:
        with self._lock:
            to_remove = [sid for sid, b in self._scopes.items() if b.is_expired(self._ttl)]
            for sid in to_remove:
                del self._scopes[sid]
            return len(to_remove)
