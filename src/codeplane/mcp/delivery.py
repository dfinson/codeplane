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
        flow=(
            "Read agentic_hint first for next steps. "
            "scaffold_files has source for context files; "
            "lite_files for peripheral orientation; "
            "repo_map for repository structure."
        ),
        priority=(
            "agentic_hint",
            "scaffold_files",
            "lite_files",
            "repo_map",
            "summary",
            "scoring_summary",
        ),
        descriptions={
            "agentic_hint": "next-step instructions — read first",
            "scaffold_files": "imports + signatures for context files",
            "lite_files": "path + description for peripheral files",
            "repo_map": "repository structure overview (embedded in recon)",
            "summary": "file count summary",
            "scoring_summary": "pipeline scoring metadata and diagnostics",
            "coverage_hint": "guidance when explicitly-mentioned paths are missing",
            "recon_id": "unique identifier for this recon call",
            "diagnostics": "timing information",
        },
    ),
    "resolve_result": SliceStrategy(
        flow=(
            "Read resolved for file contents with SHA hashes; "
            "follow agentic_hint for edit/review workflow."
        ),
        priority=("resolved", "agentic_hint", "errors"),
        descriptions={
            "resolved": "file contents with path, content, file_sha256, line_count",
            "agentic_hint": "next-step routing (edit / rename / move / delete / review)",
            "errors": "resolution errors, if any",
        },
    ),
    "refactor_preview": SliceStrategy(
        flow=(
            "Check summary + display_to_user for overview; "
            "inspect preview.edits for per-file hunks; "
            "use refactor_id to apply or cancel."
        ),
        priority=("summary", "display_to_user", "preview", "refactor_id"),
        descriptions={
            "summary": "human-readable refactor summary",
            "display_to_user": "user-facing refactor description",
            "preview": "per-file edit hunks with certainty levels",
            "refactor_id": "ID for refactor_apply or refactor_cancel",
            "status": "pending / applied / cancelled",
            "divergence": "conflicting hunks and resolution options",
            "warning": "format or usage warnings",
        },
    ),
    "semantic_diff": SliceStrategy(
        flow=(
            "Read summary + breaking_summary for overview; "
            "structural_changes for per-symbol diffs; "
            "follow agentic_hint for next steps."
        ),
        priority=(
            "summary",
            "breaking_summary",
            "structural_changes",
            "non_structural_changes",
            "agentic_hint",
        ),
        descriptions={
            "summary": "high-level change overview",
            "breaking_summary": "breaking change summary",
            "structural_changes": "per-symbol structural diffs (compressed)",
            "non_structural_changes": "non-structural file changes (renames, deletes)",
            "agentic_hint": "next-step guidance",
            "files_analyzed": "number of files analyzed",
            "base": "base ref description",
            "target": "target ref description",
            "scope": "analysis scope boundaries",
        },
    ),
    "checkpoint": SliceStrategy(
        flow=(
            "Check passed + summary first; read agentic_hint for next steps; "
            "drill into lint/tests on failure; check commit for push status."
        ),
        priority=(
            "passed",
            "summary",
            "agentic_hint",
            "lint",
            "tests",
            "commit",
            "coverage_hint",
        ),
        descriptions={
            "passed": "overall pass/fail boolean — read first",
            "summary": "one-line result summary",
            "agentic_hint": "next-step instructions — always follow these",
            "lint": "linter diagnostics with status, issue count, and fixes",
            "tests": "test runner output — tiered results with pass/fail counts",
            "commit": "commit SHA, push status, and lean semantic diff",
            "coverage_hint": "test coverage extraction commands",
            "action": "always 'checkpoint'",
            "changed_files": "input file list",
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


def _cpl_cmd(cache_id: str, slice_key: str) -> str:
    """Format a single cplcache retrieval command."""
    return f'python3 .codeplane/scripts/cplcache.py --cache-id "{cache_id}" --slice "{slice_key}"'


def _build_cplcache_hint(
    cache_id: str,
    byte_size: int,
    resource_kind: str,
    sections: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    """Build cplcache terminal hints with aggressive clarity.

    The output is structured so agents cannot miss the retrieval commands:
    - Urgent header with CACHED / RETRIEVE framing
    - Per-file dot-path commands for resolve_result and recon_result
    - Section-level commands for other resource kinds
    - Strategy flow promotes the right consumption order
    """
    from codeplane.mcp.sidecar_cache import CacheSection

    strategy = _SLICE_STRATEGIES.get(resource_kind)

    parts: list[str] = [
        ">>> RESPONSE CACHED — EXECUTE COMMANDS BELOW TO RETRIEVE <<<",
        "",
        f"Cache ID: {cache_id}  |  Total: {byte_size:,} bytes  |  Kind: {resource_kind}",
    ]
    if strategy:
        parts.append(f"Retrieval plan: {strategy.flow}")
    parts.append("")

    # ---- Per-file dot-path hints for resolve_result ----
    if resource_kind == "resolve_result" and payload:
        resolved = payload.get("resolved", [])
        if resolved:
            parts.append(
                f"RESOLVED FILES ({len(resolved)} files) "
                "— manifest with metadata is in the envelope above"
            )
            parts.append("")
            parts.append("Retrieve content (run in terminal):")
            for idx, item in enumerate(resolved):
                path = item.get("path", f"file_{idx}")
                lc = item.get("line_count", "?")
                parts.append(f"  {path} ({lc} lines):")
                parts.append(f"    {_cpl_cmd(cache_id, f'resolved.{idx}.content')}")
            parts.append("")
        # Also list non-content sections (agentic_hint, errors)
        _append_non_content_sections(
            parts,
            cache_id,
            sections,
            strategy,
            skip_keys={"resolved"},
        )

    # ---- Per-file dot-path hints for recon_result ----
    elif resource_kind == "recon_result" and payload:
        scaffold_files = payload.get("scaffold_files", [])
        if scaffold_files:
            parts.append(f"SCAFFOLD FILES ({len(scaffold_files)} files) — imports + signatures")
            parts.append("")
            parts.append("Retrieve scaffolds (run in terminal):")
            for idx, item in enumerate(scaffold_files):
                path = item.get("path", f"file_{idx}")
                parts.append(f"  {path}:")
                parts.append(f"    {_cpl_cmd(cache_id, f'scaffold_files.{idx}.scaffold')}")
            parts.append("")
        # List remaining sections (agentic_hint, lite_files, repo_map, etc.)
        _append_non_content_sections(
            parts,
            cache_id,
            sections,
            strategy,
            skip_keys={"scaffold_files"},
        )

    # ---- Section-level hints for other resource kinds ----
    elif sections:
        ordered = _order_sections(sections, strategy)
        top_level = [
            (k, s) for k, s in ordered if isinstance(s, CacheSection) and s.parent_key is None
        ]
        if top_level:
            parts.append("COMMANDS — run each in terminal:")
            parts.append("")
            for key, sec in top_level:
                desc = strategy.descriptions.get(key, "") if strategy else ""
                desc_suffix = f"  ({desc})" if desc else ""
                if sec.ready:
                    parts.append(f"  [{key}] {sec.byte_size:,} bytes{desc_suffix}")
                    parts.append(f"    {_cpl_cmd(cache_id, key)}")
                    parts.append("")
                elif sec.chunk_total:
                    parts.append(
                        f"  [{key}] {sec.byte_size:,} bytes — {sec.chunk_total} chunks{desc_suffix}"
                    )
                    for cidx in range(sec.chunk_total):
                        sub_key = f"{key}.{cidx}"
                        sub_sec = sections.get(sub_key)
                        if isinstance(sub_sec, CacheSection):
                            item_hint = (
                                f" ({sub_sec.chunk_items} items)"
                                if sub_sec.chunk_items is not None
                                else ""
                            )
                            parts.append(
                                f"    chunk {cidx}: {sub_sec.byte_size:,} bytes{item_hint}"
                            )
                            parts.append(f"      {_cpl_cmd(cache_id, sub_key)}")
                    parts.append("")
                else:
                    parts.append(f"  [{key}] {sec.byte_size:,} bytes{desc_suffix}")
                    parts.append(f"    {_cpl_cmd(cache_id, key)}")
                    parts.append("")
    else:
        parts.append("COMMAND — run in terminal:")
        parts.append(f"  {_cpl_cmd(cache_id, '<SECTION>')}")
        parts.append("")

    parts.append("")

    return "\n".join(parts)


def _append_non_content_sections(
    parts: list[str],
    cache_id: str,
    sections: dict[str, Any] | None,
    strategy: SliceStrategy | None,
    *,
    skip_keys: set[str],
) -> None:
    """Append section-level commands for non-content keys.

    Used by per-file hint builders (resolve, recon) to list remaining
    sections like agentic_hint, errors, repo_map, lite_files, etc.
    """
    from codeplane.mcp.sidecar_cache import CacheSection

    if not sections:
        return

    ordered = _order_sections(sections, strategy)
    top_level = [
        (k, s)
        for k, s in ordered
        if isinstance(s, CacheSection) and s.parent_key is None and k not in skip_keys
    ]
    if not top_level:
        return

    parts.append("OTHER SECTIONS:")
    parts.append("")
    for key, sec in top_level:
        desc = strategy.descriptions.get(key, "") if strategy else ""
        desc_suffix = f"  ({desc})" if desc else ""
        if sec.ready:
            parts.append(f"  [{key}] {sec.byte_size:,} bytes{desc_suffix}")
            parts.append(f"    {_cpl_cmd(cache_id, key)}")
            parts.append("")
        elif sec.chunk_total:
            parts.append(
                f"  [{key}] {sec.byte_size:,} bytes — {sec.chunk_total} chunks{desc_suffix}"
            )
            for cidx in range(sec.chunk_total):
                sub_key = f"{key}.{cidx}"
                sub_sec = sections.get(sub_key)
                if isinstance(sub_sec, CacheSection):
                    parts.append(f"    chunk {cidx}: {sub_sec.byte_size:,} bytes")
                    parts.append(f"      {_cpl_cmd(cache_id, sub_key)}")
            parts.append("")
        else:
            parts.append(f"  [{key}] {sec.byte_size:,} bytes{desc_suffix}")
            parts.append(f"    {_cpl_cmd(cache_id, key)}")
            parts.append("")


def _build_inline_summary(
    resource_kind: str,
    payload: dict[str, Any],
) -> str | None:
    """Build a compact inline summary string for oversized payloads.

    Used in the envelope when the full payload goes to the sidecar cache.
    Returns None if no meaningful summary can be constructed.
    """
    if resource_kind == "recon_result":
        n_scaffold = len(payload.get("scaffold_files", []))
        n_lite = len(payload.get("lite_files", []))
        has_map = "repo_map" in payload
        parts: list[str] = [f"{n_scaffold} scaffold(s), {n_lite} lite(s)"]
        if has_map:
            parts.append("repo_map included")
        return ", ".join(parts)

    if resource_kind == "resolve_result":
        resolved = payload.get("resolved", [])
        errors = payload.get("errors", [])
        parts_: list[str] = [f"{len(resolved)} file(s) resolved"]
        if errors:
            parts_.append(f"{len(errors)} error(s)")
        return ", ".join(parts_)

    if resource_kind == "checkpoint":
        passed = payload.get("passed")
        parts_c: list[str] = []
        if passed is True:
            parts_c.append("PASSED")
        elif passed is False:
            parts_c.append("FAILED")
        summary_text = payload.get("summary", "")
        if summary_text:
            parts_c.append(str(summary_text))
        commit = payload.get("commit", {})
        if isinstance(commit, dict) and commit.get("oid"):
            parts_c.append(f"committed {commit['oid'][:7]}")
        return " | ".join(parts_c) if parts_c else None

    if resource_kind == "semantic_diff":
        summary = payload.get("summary")
        if summary:
            return str(summary)
        changes = payload.get("structural_changes", [])
        return f"{len(changes)} structural change(s)"

    if resource_kind == "refactor_preview":
        preview = payload.get("preview", {})
        if isinstance(preview, dict):
            af = preview.get("files_affected", 0)
            edits = preview.get("edits", [])
            return f"{len(edits)} edit(s) across {af} file(s)"
        summary = payload.get("summary")
        return str(summary) if summary else None

    return None


def _build_manifest(
    resource_kind: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a lightweight inline manifest for sidecar-cached payloads.

    Returns a dict of manifest keys to include in the sidecar envelope.
    Only metadata — no file content.  This allows agents to immediately
    see WHICH files are available + their edit_tickets / candidate_ids
    without fetching any content from the cache.

    Returns None if no manifest is applicable for this resource kind.
    """
    if resource_kind == "resolve_result":
        resolved = payload.get("resolved", [])
        manifest = []
        for idx, item in enumerate(resolved):
            entry: dict[str, Any] = {
                "idx": idx,
                "path": item.get("path", ""),
                "candidate_id": item.get("candidate_id", ""),
                "sha256": item.get("file_sha256", "")[:16],
                "line_count": item.get("line_count", 0),
            }
            if item.get("edit_ticket"):
                entry["edit_ticket"] = item["edit_ticket"]
            if item.get("span"):
                entry["span"] = item["span"]
            manifest.append(entry)
        return {"manifest": manifest}

    if resource_kind == "recon_result":
        result: dict[str, Any] = {}
        scaffold_files = payload.get("scaffold_files", [])
        if scaffold_files:
            result["scaffold_manifest"] = [
                {
                    "idx": idx,
                    "path": item.get("path", ""),
                    "candidate_id": item.get("candidate_id", ""),
                }
                for idx, item in enumerate(scaffold_files)
            ]
        lite_files = payload.get("lite_files", [])
        if lite_files:
            result["lite_manifest"] = [
                {
                    "idx": idx,
                    "path": item.get("path", ""),
                    "candidate_id": item.get("candidate_id", ""),
                }
                for idx, item in enumerate(lite_files)
            ]
        return result if result else None

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

    payload_bytes = len(json.dumps(result, separators=(",", ":"), default=str).encode("utf-8"))

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

        # Inject manifest (lightweight per-file metadata — no content)
        manifest_data = _build_manifest(resource_kind, result)
        if manifest_data:
            envelope.update(manifest_data)

        envelope["inline_budget_bytes_used"] = len(
            json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")
        )
        envelope["inline_budget_bytes_limit"] = inline_cap
        envelope["agentic_hint"] = _build_cplcache_hint(
            cache_id,
            payload_bytes,
            resource_kind,
            sections=entry.sections if entry else None,
            payload=result,
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
