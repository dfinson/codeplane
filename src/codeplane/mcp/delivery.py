"""Unified delivery envelope for MCP tool responses.

Provides:
- DeliveryEnvelope: uniform response shape for all endpoints
- ResourceCache: LRU cache with TTL for resource-mode payloads
- ClientProfile: static client capability profiles
- build_envelope: decide inline/resource/paged delivery
- resolve_profile: select client profile from connection info

Design modeled on _DiffCache in diff.py (LRU OrderedDict, thread-safe, TTL).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import structlog

from codeplane.config.constants import (
    INLINE_CAP_BYTES,
    RESOURCE_CACHE_MAX,
    RESOURCE_CACHE_TTL,
)

log = structlog.get_logger(__name__)


# =============================================================================
# Client Profiles
# =============================================================================


@dataclass(frozen=True)
class ClientProfile:
    """Static client capability profile."""

    name: str
    supports_resources: bool | None  # None = auto-detect from capabilities
    inline_cap_bytes: int = INLINE_CAP_BYTES
    prefer_delivery: str = "resource"  # "resource" | "paged"


PROFILES: dict[str, ClientProfile] = {
    "default": ClientProfile(
        name="default",
        supports_resources=None,  # auto from capabilities
        inline_cap_bytes=INLINE_CAP_BYTES,
        prefer_delivery="resource",
    ),
    "copilot_coding_agent": ClientProfile(
        name="copilot_coding_agent",
        supports_resources=False,
        inline_cap_bytes=INLINE_CAP_BYTES,
        prefer_delivery="paged",
    ),
    "vscode_chat": ClientProfile(
        name="vscode_chat",
        supports_resources=True,
        inline_cap_bytes=INLINE_CAP_BYTES,
        prefer_delivery="resource",
    ),
}


def resolve_profile(
    client_info: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    config_override: str | None = None,
) -> ClientProfile:
    """Resolve client profile from connection info.

    Priority: explicit config override > clientInfo.name > capabilities.resources > default.
    """
    # 1. Explicit override
    if config_override and config_override in PROFILES:
        profile = PROFILES[config_override]
        log.debug(
            "profile_resolved",
            source="config_override",
            profile=profile.name,
            supports_resources=profile.supports_resources,
        )
        return profile

    # 2. clientInfo.name match
    if client_info:
        name = client_info.get("name", "")
        if name in PROFILES:
            profile = PROFILES[name]
            log.debug(
                "profile_resolved",
                source="client_name",
                profile=profile.name,
                supports_resources=profile.supports_resources,
            )
            return profile

    # 3. Auto-detect from capabilities
    default = PROFILES["default"]
    has_resources = False
    if capabilities and capabilities.get("resources"):
        has_resources = True

    resolved_supports = (
        default.supports_resources if default.supports_resources is not None else has_resources
    )

    profile = ClientProfile(
        name="default",
        supports_resources=resolved_supports,
        inline_cap_bytes=default.inline_cap_bytes,
        prefer_delivery=default.prefer_delivery,
    )
    log.debug(
        "profile_resolved",
        source="auto",
        profile=profile.name,
        supports_resources=profile.supports_resources,
        prefer_delivery=profile.prefer_delivery,
    )
    return profile


# =============================================================================
# Resource Cache
# =============================================================================


@dataclass
class ResourceMeta:
    """Metadata for a cached resource."""

    byte_size: int
    sha256: str
    content_type: str
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "content_type": self.content_type,
            "expires_at": self.expires_at,
        }


class _CacheEntry:
    """A cached resource payload with metadata."""

    __slots__ = ("payload", "meta", "created_at")

    def __init__(self, payload: bytes, meta: ResourceMeta) -> None:
        self.payload = payload
        self.meta = meta
        self.created_at = time.monotonic()

    def is_expired(self) -> bool:
        return time.monotonic() > self.meta.expires_at


class ResourceCache:
    """Thread-safe LRU cache for resource payloads.

    Modeled on _DiffCache in diff.py.
    """

    def __init__(
        self,
        max_entries: int = RESOURCE_CACHE_MAX,
        ttl_seconds: float = RESOURCE_CACHE_TTL,
    ) -> None:
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries
        self._ttl = ttl_seconds

    def store(
        self,
        payload: Any,
        kind: str,
        scope_id: str,
        content_type: str = "application/json",
    ) -> tuple[str, ResourceMeta]:
        """Store a payload and return (resource_uri, meta)."""
        if isinstance(payload, dict | list):
            raw = json.dumps(payload, indent=2).encode("utf-8")
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            raw = payload
        else:
            raw = json.dumps(payload, indent=2, default=str).encode("utf-8")

        sha = hashlib.sha256(raw).hexdigest()
        resource_id = uuid.uuid4().hex[:12]
        uri = f"codeplane://{scope_id}/cache/{kind}/{resource_id}"
        expires_at = time.monotonic() + self._ttl

        meta = ResourceMeta(
            byte_size=len(raw),
            sha256=sha,
            content_type=content_type,
            expires_at=expires_at,
        )

        entry = _CacheEntry(raw, meta)

        with self._lock:
            self._entries[resource_id] = entry
            # Evict oldest if over capacity
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

        log.debug(
            "resource_cached",
            resource_id=resource_id,
            kind=kind,
            scope_id=scope_id,
            byte_size=len(raw),
        )
        return uri, meta

    def get(self, resource_id: str) -> bytes | None:
        """Retrieve cached payload (returns None if expired or missing)."""
        with self._lock:
            entry = self._entries.get(resource_id)
            if entry is None:
                return None
            if entry.is_expired():
                self._entries.pop(resource_id, None)
                return None
            # Move to end (LRU)
            self._entries.move_to_end(resource_id)
            return entry.payload

    def get_meta(self, resource_id: str) -> ResourceMeta | None:
        """Get metadata for a cached resource."""
        with self._lock:
            entry = self._entries.get(resource_id)
            if entry is None or entry.is_expired():
                return None
            return entry.meta

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)


# Global resource cache instance
_resource_cache = ResourceCache()


def get_resource_cache() -> ResourceCache:
    """Get the global resource cache."""
    return _resource_cache


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
    pagination_cursor: str | None = None,
    has_more: bool = False,
    total_estimate: int | None = None,
) -> dict[str, Any]:
    """Build a delivery envelope around a payload.

    Rules:
    - Payload fits inline -> delivery="inline"
    - Doesn't fit + resources supported -> delivery="resource", synopsis only
    - Doesn't fit + no resources -> delivery="paged" with cursor
    """
    profile = client_profile or PROFILES["default"]
    inline_cap = profile.inline_cap_bytes

    # Measure payload size
    payload_bytes = len(json.dumps(payload, indent=2).encode("utf-8"))

    # Build base envelope
    envelope: dict[str, Any] = {
        "resource_kind": resource_kind,
    }

    fits_inline = payload_bytes <= inline_cap

    if fits_inline and not has_more:
        # Inline delivery
        envelope["delivery"] = "inline"
        envelope.update(payload)
        envelope["inline_budget_bytes_used"] = payload_bytes
        envelope["inline_budget_bytes_limit"] = inline_cap
        if inline_summary:
            envelope["inline_summary"] = inline_summary
    elif not fits_inline and _profile_supports_resources(profile):
        # Resource delivery
        effective_scope = scope_id or "default"
        uri, meta = _resource_cache.store(payload, resource_kind, effective_scope)
        envelope["delivery"] = "resource"
        envelope["resource_uri"] = uri
        envelope["resource_meta"] = meta.to_dict()
        envelope["capability_used"] = "resources"
        # Synopsis only — no partial page, no cursor
        if inline_summary:
            envelope["inline_summary"] = inline_summary
        envelope["inline_budget_bytes_used"] = len(
            json.dumps(envelope, indent=2, default=str).encode("utf-8")
        )
        envelope["inline_budget_bytes_limit"] = inline_cap
    else:
        # Paged delivery
        envelope["delivery"] = "paged"
        envelope.update(payload)
        envelope["inline_budget_bytes_used"] = payload_bytes
        envelope["inline_budget_bytes_limit"] = inline_cap
        envelope["capability_used"] = "pagination"
        if inline_summary:
            envelope["inline_summary"] = inline_summary
        pagination: dict[str, Any] = {"truncated": has_more}
        if has_more and pagination_cursor:
            pagination["next_cursor"] = pagination_cursor
        if total_estimate is not None:
            pagination["total_estimate"] = total_estimate
        envelope["pagination"] = pagination

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


def _profile_supports_resources(profile: ClientProfile) -> bool:
    """Check if profile supports resources."""
    if profile.supports_resources is None:
        return False
    return profile.supports_resources


def wrap_existing_response(
    result: dict[str, Any],
    *,
    resource_kind: str,
    scope_id: str | None = None,
    scope_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add delivery envelope fields to an existing handler response.

    For handlers that already manage their own pagination via BudgetAccumulator.
    Adds delivery metadata without restructuring the response.
    """
    payload_bytes = len(json.dumps(result, indent=2, default=str).encode("utf-8"))
    has_pagination = "pagination" in result
    is_truncated = has_pagination and result.get("pagination", {}).get("truncated", False)

    result["resource_kind"] = resource_kind
    result["delivery"] = "paged" if is_truncated else "inline"
    result["inline_budget_bytes_used"] = payload_bytes
    result["inline_budget_bytes_limit"] = INLINE_CAP_BYTES

    if scope_id:
        result["scope_id"] = scope_id
    if scope_usage:
        result["scope_usage"] = scope_usage

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
        """Reset duplicate read tracking after a mutation."""
        self._mutation_epoch += 1
        self._full_read_history.clear()

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
        return {
            "read_bytes": self.read_bytes_total,
            "full_reads": self.full_file_reads,
            "read_calls": self.read_calls,
            "search_calls": self.search_calls,
            "search_hits_returned_total": self.search_hits_returned_total,
            "paged_continuations": self.paged_continuations,
        }

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
        """Record a mutation event for duplicate read tracking."""
        with self._lock:
            budget = self._scopes.get(scope_id)
            if budget:
                budget.record_mutation()

    def cleanup_expired(self) -> int:
        with self._lock:
            to_remove = [sid for sid, b in self._scopes.items() if b.is_expired(self._ttl)]
            for sid in to_remove:
                del self._scopes[sid]
            return len(to_remove)
