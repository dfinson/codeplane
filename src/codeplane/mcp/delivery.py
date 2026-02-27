"""Unified delivery envelope for MCP tool responses.

Provides:
- ClientProfile: static client capability profiles
- wrap_response: decide inline vs sidecar-cache delivery
- resolve_profile: select client profile from connection info
- ScopeBudget / ScopeManager: per-scope usage tracking

Oversized payloads are stored in the in-memory sidecar cache
(see sidecar_cache.py) and the agent is given cplcache commands
to retrieve slices from the running daemon.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import structlog

from codeplane.config.constants import INLINE_CAP_BYTES
from codeplane.config.user_config import DEFAULT_PORT

log = structlog.get_logger(__name__)

# Server port for cplcache hints (set during startup, fallback to default)
_server_port: int = DEFAULT_PORT


def set_server_port(port: int) -> None:
    """Set the server port for cplcache fetch hints."""
    global _server_port  # noqa: PLW0603
    _server_port = port


# =============================================================================
# Slice Strategies — resource-kind-specific consumption guidance
# =============================================================================


@dataclass
class SliceStrategy:
    """Resource-kind-specific guidance for consuming cached sections.

    Combines with pre-computed CacheSection metadata to produce
    context-aware hints showing byte sizes, priority order,
    and section descriptions.
    """

    flow: str  # one-line consumption guidance
    priority: tuple[str, ...] = ()  # sections to surface first, in order
    descriptions: dict[str, str] = field(default_factory=dict)  # key → contextual label


_SLICE_STRATEGIES: dict[str, SliceStrategy] = {
    "recon_result": SliceStrategy(
        flow="Read full_file for edit targets, min_scaffold for context, summary_only for orientation.",
        priority=("full_file", "min_scaffold", "summary_only"),
        descriptions={
            "full_file": "edit-target files with complete source",
            "min_scaffold": "imports + signatures for context files",
            "summary_only": "path + description for peripheral files",
            "agentic_hint": "next-step instructions from the server",
        },
    ),
    "source": SliceStrategy(
        flow="Slice files.N for individual file contents.",
        priority=("files", "summary", "page_info"),
        descriptions={
            "files": "file contents — slice files.N for each file",
            "summary": "result summary",
            "page_info": "pagination state",
            "cursor": "continuation cursor for next page",
        },
    ),
    "checkpoint": SliceStrategy(
        flow="Check passed + summary first; drill into lint/tests only on failure.",
        priority=("passed", "summary", "lint", "tests", "commit"),
        descriptions={
            "passed": "overall pass/fail boolean",
            "summary": "one-line result summary",
            "lint": "linter diagnostics",
            "tests": "test runner output and failures",
            "commit": "commit/push status and diff summary",
            "coverage_hint": "coverage extraction commands",
            "agentic_hint": "next-step instructions",
        },
    ),
    "semantic_diff": SliceStrategy(
        flow="Read summary for overview, then structural_changes for per-symbol details.",
        priority=("summary", "structural_changes", "changes"),
        descriptions={
            "summary": "high-level change overview",
            "structural_changes": "per-symbol structural diffs",
            "changes": "per-symbol structural diffs",
        },
    ),
    "repo_map": SliceStrategy(
        flow="Independent topic sections — browse what you need.",
        priority=("summary",),
        descriptions={
            "summary": "repository overview",
        },
    ),
    "search_hits": SliceStrategy(
        flow="Slice results.N to inspect individual search matches.",
        priority=("results", "total"),
        descriptions={
            "results": "match list — slice results.N for each hit",
            "total": "total match count",
        },
    ),
    "diff": SliceStrategy(
        flow="Raw diff text — large diffs are chunked automatically by the server.",
        priority=("diff",),
        descriptions={
            "diff": "unified diff output",
        },
    ),
    "log": SliceStrategy(
        flow="Slice results.N for individual commit details.",
        priority=("results",),
        descriptions={
            "results": "commit entries — slice results.N for each",
        },
    ),
    "blame": SliceStrategy(
        flow="Slice results.N for individual blame hunks.",
        priority=("results", "path"),
        descriptions={
            "results": "blame hunks — slice results.N for each",
            "path": "blamed file path",
        },
    ),
    "refactor_preview": SliceStrategy(
        flow="Review matches.N to inspect individual refactoring sites.",
        priority=("matches", "refactor_id"),
        descriptions={
            "matches": "refactor sites — slice matches.N for each",
            "refactor_id": "ID for apply/cancel",
        },
    ),
    "test_output": SliceStrategy(
        flow="Check pass/fail counts, then output for details.",
        priority=("passed", "failed", "output"),
        descriptions={
            "passed": "pass count",
            "failed": "fail count",
            "output": "raw test runner output",
        },
    ),
    "scaffold": SliceStrategy(
        flow="Structural skeleton — imports and signatures without source bodies.",
        priority=("files", "summary"),
        descriptions={
            "files": "scaffold content per file",
            "summary": "result summary",
        },
    ),
}


def _order_sections(
    sections: dict[str, Any],
    strategy: SliceStrategy | None,
) -> list[tuple[str, Any]]:
    """Order sections by strategy priority, then remaining keys alphabetically."""
    if not strategy or not strategy.priority:
        return list(sections.items())

    ordered: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for key in strategy.priority:
        if key in sections:
            ordered.append((key, sections[key]))
            seen.add(key)
    for key in sorted(sections.keys()):
        if key not in seen:
            ordered.append((key, sections[key]))
    return ordered


# =============================================================================
# cplcache Hint Builder
# =============================================================================


def _build_cplcache_hint(
    cache_id: str,
    byte_size: int,
    resource_kind: str,
    session_id: str,
    sections: dict[str, Any] | None = None,
) -> str:
    """Build cplcache terminal hints with resource-kind-specific slicing strategy.

    Combines pre-computed section metadata (byte sizes, ready flags) with
    per-resource-kind consumption guidance (priority ordering, descriptions).

    All hint commands use pre-computed server-side chunking — the client
    never needs to specify --max-bytes.
    """
    from codeplane.mcp.sidecar_cache import CacheSection

    strategy = _SLICE_STRATEGIES.get(resource_kind)

    parts: list[str] = [
        f"Response too large for inline delivery ({byte_size:,} bytes).",
        f"Cached as {cache_id} (kind: {resource_kind}).",
    ]
    if strategy:
        parts.append(f"Strategy: {strategy.flow}")
    parts.append("")

    if sections:
        ordered = _order_sections(sections, strategy)

        ready = [(k, s) for k, s in ordered if isinstance(s, CacheSection) and s.ready]
        oversized = [(k, s) for k, s in ordered if isinstance(s, CacheSection) and not s.ready]

        if ready:
            parts.append("Ready sections (instant retrieval):")
            for key, sec in ready:
                desc = strategy.descriptions.get(key, "") if strategy else ""
                desc_part = f" — {desc}" if desc else ""
                parts.append(
                    f"  {key:<24} {sec.byte_size:>8,} bytes{desc_part}  "
                    f"cplcache slice --cache {cache_id} --path {key}"
                )

        if oversized:
            if ready:
                parts.append("")
            parts.append("Oversized sections (chunked retrieval):")
            for key, sec in oversized:
                desc = strategy.descriptions.get(key, "") if strategy else ""
                desc_part = f" — {desc}" if desc else ""
                parts.append(
                    f"  {key:<24} {sec.byte_size:>8,} bytes{desc_part}  "
                    f"cplcache slice --cache {cache_id} --path {key}"
                )
    else:
        parts.append(f"  cplcache slice --cache {cache_id}")

    parts.extend(
        [
            "",
            f"All entries: cplcache list --session {session_id} --endpoint {resource_kind}",
            f"Full schema: cplcache meta --cache {cache_id}",
        ]
    )

    return "\n".join(parts)


def _build_inline_summary(
    resource_kind: str,
    payload: dict[str, Any],
) -> str | None:
    """Build a compact inline summary string for oversized payloads.

    Used in the envelope when the full payload goes to the sidecar cache.
    Returns None if no meaningful summary can be constructed.
    """
    if resource_kind == "recon_result":
        n_full = len(payload.get("full_file", []))
        n_scaffold = len(payload.get("min_scaffold", []))
        n_summary = len(payload.get("summary_only", []))
        return (
            f"{n_full} full file(s), {n_scaffold} scaffold(s), "
            f"{n_summary} summary(ies) across {n_full + n_scaffold + n_summary} file(s)"
        )

    if resource_kind == "checkpoint":
        passed = payload.get("passed")
        parts: list[str] = []
        if passed is True:
            parts.append("PASSED")
        elif passed is False:
            parts.append("FAILED")
        summary_text = payload.get("summary", "")
        if summary_text:
            parts.append(str(summary_text))
        commit = payload.get("commit", {})
        if isinstance(commit, dict) and commit.get("oid"):
            parts.append(f"committed {commit['oid'][:7]}")
        return " | ".join(parts) if parts else None

    if resource_kind == "semantic_diff":
        summary = payload.get("summary")
        if summary:
            return str(summary)
        changes = payload.get("structural_changes", payload.get("changes", []))
        return f"{len(changes)} structural change(s)"

    if resource_kind in ("source", "search_hits"):
        items_key = "files" if resource_kind == "source" else "results"
        return f"{len(payload.get(items_key, []))} {items_key}"

    if resource_kind == "diff":
        diff_text = payload.get("diff", "")
        n_files = diff_text.count("diff --git") if isinstance(diff_text, str) else 0
        return f"{n_files} file(s) changed"

    if resource_kind == "log":
        results = payload.get("results", [])
        return f"{len(results)} commit(s)"

    if resource_kind == "blame":
        results = payload.get("results", [])
        bp = payload.get("path", "")
        authors = {r.get("author", "") for r in results} - {""}
        return f"Blame {bp}: {len(results)} hunk(s), {len(authors)} author(s)"

    if resource_kind == "repo_map":
        skip = {"summary", "resource_kind", "delivery"}
        sections = [k for k in payload if k not in skip]
        return f"Sections: {', '.join(sections)}" if sections else None

    if resource_kind == "test_output":
        passed = payload.get("passed", 0)
        failed = payload.get("failed", 0)
        return f"{passed} passed, {failed} failed"

    if resource_kind == "refactor_preview":
        matches = payload.get("matches", [])
        af = len({m.get("path", "") for m in matches})
        return f"{len(matches)} match(es) across {af} file(s)"

    return None


def wrap_response(
    result: dict[str, Any],
    *,
    resource_kind: str,
    session_id: str = "default",
    scope_id: str | None = None,
    scope_usage: dict[str, Any] | None = None,
    client_profile: ClientProfile | None = None,
) -> dict[str, Any]:
    """Add delivery envelope fields to an existing handler response.

    If the payload fits within inline_cap, it is returned inline.
    Otherwise it is stored in the sidecar cache and the response
    contains a summary + cplcache fetch hints.
    """
    from codeplane.mcp.sidecar_cache import cache_put, get_sidecar_cache

    profile = client_profile or get_current_profile()
    inline_cap = profile.inline_cap_bytes

    payload_bytes = len(json.dumps(result, indent=2, default=str).encode("utf-8"))

    if payload_bytes <= inline_cap:
        # Inline delivery — full payload in the response
        result["resource_kind"] = resource_kind
        result["delivery"] = "inline"
        result["inline_budget_bytes_used"] = payload_bytes
        result["inline_budget_bytes_limit"] = inline_cap
    else:
        # Oversized — store in sidecar cache, return synopsis + cplcache hints
        cache_id = cache_put(session_id, resource_kind, result)
        entry = get_sidecar_cache().get_entry(cache_id)
        summary = _build_inline_summary(resource_kind, result)

        envelope: dict[str, Any] = {
            "resource_kind": resource_kind,
            "delivery": "sidecar_cache",
            "cache_id": cache_id,
        }
        if summary:
            envelope["summary"] = summary
        envelope["inline_budget_bytes_used"] = len(
            json.dumps(envelope, indent=2, default=str).encode("utf-8")
        )
        envelope["inline_budget_bytes_limit"] = inline_cap
        envelope["agentic_hint"] = _build_cplcache_hint(
            cache_id,
            payload_bytes,
            resource_kind,
            session_id,
            entry.sections if entry else None,
        )

        log.debug(
            "envelope_wrapped",
            delivery="sidecar_cache",
            resource_kind=resource_kind,
            payload_bytes=payload_bytes,
            inline_cap=inline_cap,
            cache_id=cache_id,
        )

        result = envelope

    if scope_id:
        result["scope_id"] = scope_id
    if scope_usage:
        result["scope_usage"] = scope_usage

    log.debug(
        "envelope_wrapped",
        delivery=result.get("delivery", "unknown"),
        resource_kind=resource_kind,
        payload_bytes=payload_bytes,
        inline_cap=inline_cap,
        scope_id=scope_id,
    )

    return result


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
