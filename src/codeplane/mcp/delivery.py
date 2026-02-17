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
import hashlib
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


def _build_fetch_hint(resource_id: str, byte_size: int, kind: str) -> str:
    """Build an agentic hint with a file read command for a cached resource."""
    rel_path = f".codeplane/cache/{kind}/{resource_id}.json"
    if sys.platform == "win32":
        cmd = f"type {rel_path.replace('/', os.sep)}"
    else:
        cmd = f"cat {rel_path}"
    return f"Full result ({byte_size:,} bytes) cached to disk. Retrieve with: {cmd}"


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


@dataclass
class ResourceMeta:
    """Metadata for a cached resource."""

    byte_size: int
    sha256: str
    content_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "content_type": self.content_type,
        }


class ResourceCache:
    """Disk-backed resource cache.

    All payloads are written as compact JSON to _cache_dir/{kind}/{id}.json.
    No in-memory state, no TTL, no LRU — the filesystem IS the cache.
    """

    def store(
        self,
        payload: Any,
        kind: str,
        scope_id: str,
        content_type: str = "application/json",
    ) -> tuple[str, ResourceMeta]:
        """Serialize payload to disk and return (resource_uri, meta)."""
        if isinstance(payload, dict | list):
            raw = json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8")
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            raw = payload
        else:
            raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")

        sha = hashlib.sha256(raw).hexdigest()
        resource_id = uuid.uuid4().hex[:12]
        uri = f"codeplane://{scope_id}/cache/{kind}/{resource_id}"

        meta = ResourceMeta(
            byte_size=len(raw),
            sha256=sha,
            content_type=content_type,
        )

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
            scope_id=scope_id,
            byte_size=len(raw),
        )

        return uri, meta

    def get(self, resource_id: str, kind: str | None = None) -> bytes | None:
        """Read a cached resource from disk.

        If kind is provided, looks directly in _cache_dir/{kind}/{id}.json.
        Otherwise scans all kind subdirectories (slower fallback).
        """
        if _cache_dir is None:
            return None
        if kind:
            path = _cache_dir / kind / f"{resource_id}.json"
            return path.read_bytes() if path.exists() else None
        # Fallback: scan all subdirectories
        for kind_dir in _cache_dir.iterdir():
            if kind_dir.is_dir():
                path = kind_dir / f"{resource_id}.json"
                if path.exists():
                    return path.read_bytes()
        return None


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
        # Resource delivery — write full payload to disk
        effective_scope = scope_id or "default"
        uri, meta = _resource_cache.store(payload, resource_kind, effective_scope)
        envelope["delivery"] = "resource"
        envelope["resource_uri"] = uri
        envelope["resource_meta"] = meta.to_dict()
        if inline_summary:
            envelope["inline_summary"] = inline_summary
        envelope["inline_budget_bytes_used"] = len(
            json.dumps(envelope, indent=2, default=str).encode("utf-8")
        )
        envelope["inline_budget_bytes_limit"] = inline_cap
        resource_id = uri.rsplit("/", 1)[-1]
        envelope["agentic_hint"] = _build_fetch_hint(resource_id, meta.byte_size, resource_kind)

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

    For handlers that already manage their own pagination via BudgetAccumulator.
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
        # Resource delivery — store full result on disk, return synopsis
        effective_scope = scope_id or "default"
        uri, meta = _resource_cache.store(result, resource_kind, effective_scope)
        envelope: dict[str, Any] = {
            "resource_kind": resource_kind,
            "delivery": "resource",
            "resource_uri": uri,
            "resource_meta": meta.to_dict(),
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
        resource_id = uri.rsplit("/", 1)[-1]
        envelope["agentic_hint"] = _build_fetch_hint(resource_id, meta.byte_size, resource_kind)

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
