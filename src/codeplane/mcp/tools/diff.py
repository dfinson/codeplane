"""Layer 4: MCP tool for semantic diff.

Orchestrates the full pipeline: sources -> engine -> enrichment -> output.
"""

# Removed: from __future__ import annotations - breaks FastMCP+pydantic Literal resolution

import contextlib
import re
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

import structlog
from fastmcp import Context
from pydantic import Field

from codeplane.core.languages import detect_language_family, has_grammar
from codeplane.git.models import _DELTA_STATUS_MAP
from codeplane.index._internal.diff.engine import compute_structural_diff
from codeplane.index._internal.diff.enrichment import enrich_diff
from codeplane.index._internal.diff.models import (
    AnalysisScope,
    ChangedFile,
    DefSnapshot,
    SemanticDiffResult,
    StructuralChange,
)
from codeplane.index._internal.diff.sources import (
    snapshots_from_blob,
    snapshots_from_epoch,
    snapshots_from_index,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register semantic_diff MCP tool."""

    @mcp.tool(
        annotations={
            "title": "Diff: structural change summary",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def semantic_diff(
        ctx: Context,
        base: str = Field("HEAD", description="Base ref (commit, branch, tag) or epoch:N"),
        target: str | None = Field(None, description="Target ref (None = working tree)"),
        paths: list[str] | None = Field(None, description="Limit to specific paths"),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
        gate_token: str | None = Field(
            None,
            description="Gate confirmation token from a previous gate block.",
        ),
        gate_reason: str | None = Field(
            None,
            description="Justification for passing the gate (min chars per gate spec).",
        ),
    ) -> dict[str, Any]:
        """Structural change summary from index facts.

        Compares definitions between two states and reports what changed
        structurally (added, removed, signature_changed, body_changed,
        renamed) with blast-radius enrichment.

        Modes:
        - Git mode (default): base/target are git refs
        - Epoch mode: base="epoch:N", target="epoch:M"
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if base.startswith("epoch:"):
            result = _run_epoch_diff(app_ctx, base, target, paths)
        else:
            result = _run_git_diff(app_ctx, base, target, paths)

        from codeplane.mcp.delivery import wrap_response

        result_dict = _result_to_text(result)

        # Track scope usage
        scope_usage = None
        if scope_id:
            from codeplane.mcp.tools.files import _scope_manager

            budget = _scope_manager.get_or_create(scope_id)
            scope_usage = budget.to_usage_dict()

        return wrap_response(
            result_dict,
            resource_kind="semantic_diff",
            scope_id=scope_id,
            scope_usage=scope_usage,
        )


def _run_git_diff(
    app_ctx: "AppContext",
    base: str,
    target: str | None,
    paths: list[str] | None,
) -> SemanticDiffResult:
    """Run semantic diff in git mode."""
    from codeplane.git._internal.planners import DiffPlanner

    git_ops = app_ctx.git_ops
    repo = git_ops._access.repo
    planner = DiffPlanner(git_ops._access)

    plan = planner.plan(base=base, target=target, staged=False)
    pygit2_diff = planner.execute(plan)

    # Extract changed files and hunks
    changed_files: list[ChangedFile] = []
    hunks: dict[str, list[tuple[int, int]]] = {}

    for patch in pygit2_diff:
        file_path = patch.delta.new_file.path or patch.delta.old_file.path  # type: ignore[union-attr]
        if paths and file_path not in paths:
            continue

        status = _DELTA_STATUS_MAP.get(patch.delta.status, "modified")  # type: ignore[union-attr]

        lang = detect_language_family(file_path)
        file_has_grammar = bool(lang and has_grammar(lang))

        changed_files.append(ChangedFile(file_path, status, file_has_grammar, language=lang))

        # Extract hunks
        file_hunks: list[tuple[int, int]] = []
        for hunk in patch.hunks:  # type: ignore[union-attr]
            start = hunk.new_start
            end = start + hunk.new_lines - 1
            if end >= start:
                file_hunks.append((start, end))
        hunks[file_path] = file_hunks

    # Build snapshots for each file
    base_facts: dict[str, list[DefSnapshot]] = {}
    target_facts: dict[str, list[DefSnapshot]] = {}

    # Resolve base commit for blob parsing
    base_commit = None
    if plan.base_oid:
        base_commit = repo[plan.base_oid]

    coordinator = app_ctx.coordinator
    db = coordinator.db

    # Single session for all index lookups + enrichment
    with db.session() as session:
        for cf in changed_files:
            if not cf.has_grammar:
                continue

            # Target: current index state
            target_facts[cf.path] = snapshots_from_index(session, cf.path)

            # Base: parse from git blob (CPU, no DB)
            if base_commit and cf.status != "added":
                base_facts[cf.path] = snapshots_from_blob(repo, base_commit, cf.path)
            else:
                base_facts[cf.path] = []

        # Run engine
        raw = compute_structural_diff(base_facts, target_facts, changed_files, hunks)

        # Enrich (reuse same session)
        result = enrich_diff(raw, session, app_ctx.repo_root)

    # Annotate with change previews from the actual patch lines
    _annotate_change_previews(result, pygit2_diff)

    result.base_description = base or "HEAD"
    result.target_description = target or "working tree"

    # Build analysis scope
    files_parsed = len([cf for cf in changed_files if cf.has_grammar])
    files_no_grammar = len([cf for cf in changed_files if not cf.has_grammar])
    languages = sorted({cf.language for cf in changed_files if cf.language})

    # Detect worktree dirty state (target is worktree when target param is None)
    worktree_dirty: bool | None = None
    if target is None:
        with contextlib.suppress(Exception):
            worktree_dirty = repo.status() != {}

    result.scope = AnalysisScope(
        base_sha=str(plan.base_oid) if plan.base_oid else None,
        target_sha=str(plan.target_oid) if plan.target_oid else None,
        worktree_dirty=worktree_dirty,
        mode="git",
        entity_id_scheme="def_uid_v1",
        files_parsed=files_parsed,
        files_no_grammar=files_no_grammar,
        languages_analyzed=languages,
    )

    return result


def _parse_epoch_ref(ref: str) -> int:
    """Parse an epoch reference like 'epoch:3' into a non-negative integer.

    Only non-negative numeric epoch IDs are supported (e.g. epoch:0, epoch:1, epoch:42).
    Named aliases like 'epoch:previous' are not implemented.

    Raises:
        ValueError: If the epoch value is not a valid non-negative integer.
    """
    parts = ref.split(":", 1)
    if len(parts) != 2 or parts[0] != "epoch":
        msg = f"Invalid epoch reference: {ref!r}. Expected format: epoch:<int>"
        raise ValueError(msg)
    try:
        value = int(parts[1])
    except ValueError:
        msg = (
            f"Invalid epoch value: {parts[1]!r}. "
            f"Only numeric epoch IDs are supported (e.g. epoch:1, epoch:42)."
        )
        raise ValueError(msg) from None
    if value < 0:
        msg = f"Epoch ID must be non-negative, got {value}."
        raise ValueError(msg)
    return value


def _run_epoch_diff(
    app_ctx: "AppContext",
    base: str,
    target: str | None,
    paths: list[str] | None,
) -> SemanticDiffResult:
    """Run semantic diff in epoch mode."""
    from codeplane.index.models import DefSnapshotRecord

    base_epoch = _parse_epoch_ref(base)
    target_epoch = _parse_epoch_ref(target) if target and target.startswith("epoch:") else None

    coordinator = app_ctx.coordinator
    db = coordinator.db

    with db.session() as session:
        from sqlmodel import select

        # Reconstruct file state at each epoch by finding all files
        # that have any snapshot at or before the epoch
        base_files_stmt = (
            select(DefSnapshotRecord.file_path)
            .where(DefSnapshotRecord.epoch_id <= base_epoch)
            .distinct()
        )
        base_file_paths = set(session.exec(base_files_stmt).all())

        target_file_paths: set[str] = set()
        if target_epoch is not None:
            target_files_stmt = (
                select(DefSnapshotRecord.file_path)
                .where(DefSnapshotRecord.epoch_id <= target_epoch)
                .distinct()
            )
            target_file_paths = set(session.exec(target_files_stmt).all())

        all_paths = base_file_paths | target_file_paths
        if paths:
            all_paths = all_paths & set(paths)

        # Build changed files and facts
        changed_files: list[ChangedFile] = []
        base_facts: dict[str, list[DefSnapshot]] = {}
        target_facts: dict[str, list[DefSnapshot]] = {}

        for fp in sorted(all_paths):
            lang = detect_language_family(fp)
            file_has_grammar = bool(lang and has_grammar(lang))

            # Reconstruct per-epoch state via snapshots_from_epoch
            # (which uses epoch_id <= target to get full state)
            base_snaps = snapshots_from_epoch(session, base_epoch, fp)
            if target_epoch is not None:
                target_snaps = snapshots_from_epoch(session, target_epoch, fp)
            else:
                target_snaps = snapshots_from_index(session, fp)

            base_exists = bool(base_snaps)
            target_exists = bool(target_snaps)

            if base_exists and not target_exists:
                status = "deleted"
            elif not base_exists and target_exists:
                status = "added"
            else:
                status = "modified"

            changed_files.append(ChangedFile(fp, status, file_has_grammar, language=lang))

            base_facts[fp] = base_snaps
            target_facts[fp] = target_snaps

        # Run engine (no hunks in epoch mode)
        raw = compute_structural_diff(base_facts, target_facts, changed_files, hunks=None)

        # Enrich (reuse same session)
        result = enrich_diff(raw, session, app_ctx.repo_root)

    result.base_description = f"epoch {base_epoch}"
    result.target_description = f"epoch {target_epoch}" if target_epoch else "current index"

    # Build analysis scope for epoch mode
    files_parsed = len([cf for cf in changed_files if cf.has_grammar])
    files_no_grammar = len([cf for cf in changed_files if not cf.has_grammar])
    languages = sorted({cf.language for cf in changed_files if cf.language})

    result.scope = AnalysisScope(
        base_sha=None,
        target_sha=None,
        worktree_dirty=None,
        mode="epoch",
        entity_id_scheme="def_uid_v1",
        files_parsed=files_parsed,
        files_no_grammar=files_no_grammar,
        languages_analyzed=languages,
    )

    return result


_PREVIEW_MAX_LINES = 5  # Max changed lines to include in preview


def _extract_patch_lines(
    pygit2_diff: object,
) -> dict[str, list[tuple[str, int, str]]]:
    """Extract per-file patch lines from a pygit2 diff.

    Returns a dict mapping file_path -> list of (origin, line_number, content)
    where origin is '+' for additions, '-' for deletions.
    """
    import pygit2

    assert isinstance(pygit2_diff, pygit2.Diff)

    result: dict[str, list[tuple[str, int, str]]] = {}
    for patch in pygit2_diff:
        file_path = patch.delta.new_file.path or patch.delta.old_file.path  # type: ignore[union-attr]
        lines: list[tuple[str, int, str]] = []
        for hunk in patch.hunks:  # type: ignore[union-attr]
            for line in hunk.lines:
                if line.origin in ("+", "-"):
                    lineno = line.new_lineno if line.origin == "+" else line.old_lineno
                    lines.append((line.origin, lineno, line.content.rstrip("\n")))
        result[file_path] = lines
    return result


def _annotate_change_previews(
    result: SemanticDiffResult,
    pygit2_diff: object,
) -> None:
    """Annotate structural changes with a text preview of what changed.

    Only applies to body_changed and signature_changed entries in git mode.
    Patches each StructuralChange.change_preview in place.
    """
    try:
        patch_lines = _extract_patch_lines(pygit2_diff)
    except Exception:
        log.debug("patch_line_extraction_failed", exc_info=True)
        return

    for change in result.structural_changes:
        if change.change not in ("body_changed", "signature_changed"):
            continue
        file_lines = patch_lines.get(change.path)
        if not file_lines:
            continue

        # Filter lines within this entity's span
        relevant: list[str] = []
        for origin, lineno, content in file_lines:
            if change.start_line <= lineno <= change.end_line:
                relevant.append(f"{origin} {content}")
                if len(relevant) >= _PREVIEW_MAX_LINES:
                    break

        if relevant:
            change.change_preview = "\n".join(relevant)

        # Also annotate nested changes
        if change.nested_changes:
            for nc in change.nested_changes:
                if nc.change not in ("body_changed", "signature_changed"):
                    continue
                nc_lines = patch_lines.get(nc.path)
                if not nc_lines:
                    continue
                nc_relevant: list[str] = []
                for origin, lineno, content in nc_lines:
                    if nc.start_line <= lineno <= nc.end_line:
                        nc_relevant.append(f"{origin} {content}")
                        if len(nc_relevant) >= _PREVIEW_MAX_LINES:
                            break
                if nc_relevant:
                    nc.change_preview = "\n".join(nc_relevant)


# =============================================================================
# Domain Classification
# =============================================================================

# Default: group by first 2 path segments (e.g. src/codeplane → "src/codeplane")
_DOMAIN_PREFIX_DEPTH = 2


def _domain_key(path: str) -> str:
    """Derive a domain key from a file path.

    Uses the first ``_DOMAIN_PREFIX_DEPTH`` path segments as the domain.
    Falls back to the directory name for shallow paths.
    """
    parts = path.split("/")
    if len(parts) <= _DOMAIN_PREFIX_DEPTH:
        return "/".join(parts[:-1]) if len(parts) > 1 else "(root)"
    return "/".join(parts[:_DOMAIN_PREFIX_DEPTH])


def _classify_domains(
    changes: list[StructuralChange],
    non_structural: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Group structural changes by directory-prefix domain.

    Each domain dict contains:
      - name: human-readable domain label (directory prefix)
      - root_path: common path prefix for files in this domain
      - files: unique file paths in this domain
      - change_count: total structural changes
      - breaking_count: changes with structural_severity == "breaking"
      - high_risk_count: body changes with risk in ("high", "medium")
      - additions: count of added symbols
      - removals: count of removed symbols
      - review_priority: int (1 = most critical, higher = less critical)
    """
    # Group changes by domain key
    domain_changes: dict[str, list[StructuralChange]] = {}
    domain_files: dict[str, set[str]] = {}
    for c in changes:
        key = _domain_key(c.path)
        domain_changes.setdefault(key, []).append(c)
        domain_files.setdefault(key, set()).add(c.path)

    # Also include non-structural files in domain file sets
    if non_structural:
        for f in non_structural:
            path = f.path if hasattr(f, "path") else f.get("path", "")
            if path:
                key = _domain_key(path)
                domain_files.setdefault(key, set()).add(path)

    # Build domain dicts with risk metrics
    domains: list[dict[str, Any]] = []
    for key in sorted(domain_changes):
        dcs = domain_changes[key]
        breaking = sum(1 for c in dcs if c.structural_severity == "breaking")
        high_risk = sum(
            1
            for c in dcs
            if c.change == "body_changed" and c.behavior_change_risk in ("high", "medium")
        )
        additions = sum(1 for c in dcs if c.change == "added")
        removals = sum(1 for c in dcs if c.change == "removed")

        domains.append(
            {
                "name": key,
                "root_path": key,
                "files": sorted(domain_files.get(key, set())),
                "change_count": len(dcs),
                "breaking_count": breaking,
                "high_risk_count": high_risk,
                "additions": additions,
                "removals": removals,
            }
        )

    # Assign review priority: breaking first, then high-risk, then by count
    domains.sort(key=lambda d: (-d["breaking_count"], -d["high_risk_count"], -d["change_count"]))
    for i, d in enumerate(domains, 1):
        d["review_priority"] = i

    return domains


def _detect_cross_domain_edges(
    changes: list[StructuralChange],
    domains: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Detect import-based edges between domains.

    Uses the ``impact.importing_files`` data already present on each
    StructuralChange to find cross-domain relationships without any
    additional DB queries.
    """
    # Build file → domain lookup
    file_to_domain: dict[str, str] = {}
    for d in domains:
        for f in d["files"]:
            file_to_domain[f] = d["name"]

    # Changed files set for filtering
    changed_files = {c.path for c in changes}

    edges: set[tuple[str, str]] = set()
    for c in changes:
        src_domain = file_to_domain.get(c.path)
        if not src_domain or not c.impact:
            continue
        importers = c.impact.importing_files or []
        for imp_file in importers:
            imp_domain = file_to_domain.get(imp_file)
            # Only report edges where the importer is also a changed file
            # in a different domain (otherwise it's just normal dependency)
            if imp_domain and imp_domain != src_domain and imp_file in changed_files:
                edges.add((src_domain, imp_domain))

    return [{"from_domain": a, "to_domain": b, "relationship": "import"} for a, b in sorted(edges)]


# =============================================================================
# Agentic Hint
# =============================================================================


def _build_agentic_hint(
    result: SemanticDiffResult,
    domains: list[dict[str, Any]] | None = None,
    cross_edges: list[dict[str, str]] | None = None,
) -> str:
    """Build action hint for the agent.

    When changes span multiple domains, emits a prioritized review plan.
    For single-domain changes, returns a compact counts summary.
    """
    if not result.structural_changes:
        return "No structural changes detected."

    # Count change types
    sig_changes = sum(1 for c in result.structural_changes if c.change == "signature_changed")
    removals = sum(1 for c in result.structural_changes if c.change == "removed")
    body_changes = [c for c in result.structural_changes if c.change == "body_changed"]
    high_risk = sum(1 for c in body_changes if c.behavior_change_risk in ("high", "medium"))
    additions = sum(1 for c in result.structural_changes if c.change == "added")

    # Count affected tests
    all_test_files: set[str] = set()
    for c in result.structural_changes:
        if c.impact and c.impact.affected_test_files:
            all_test_files.update(c.impact.affected_test_files)

    # Build compact counts line
    count_parts: list[str] = []
    if sig_changes:
        count_parts.append(f"{sig_changes} signature changes")
    if removals:
        count_parts.append(f"{removals} removals")
    if body_changes:
        risk_note = f" ({high_risk} high-risk)" if high_risk else ""
        count_parts.append(f"{len(body_changes)} body changes{risk_note}")
    if additions:
        count_parts.append(f"{additions} additions")

    if not count_parts:
        return "No actionable changes."

    counts_line = ", ".join(count_parts) + "."
    if all_test_files:
        counts_line += f" {len(all_test_files)} affected test files."

    # Single domain or no domain info → compact hint
    if not domains or len(domains) <= 1:
        return counts_line

    # Multi-domain → structured review plan
    lines: list[str] = []
    lines.append(f"REVIEW PLAN: {len(domains)} domains, {counts_line}")
    lines.append("")
    lines.append("Priority order (review breaking/high-risk first):")

    for d in domains:
        priority = d["review_priority"]
        risk_parts: list[str] = []
        if d["breaking_count"]:
            risk_parts.append(f"{d['breaking_count']} BREAKING")
        if d["high_risk_count"]:
            risk_parts.append(f"{d['high_risk_count']} high-risk")
        risk_str = f" ({', '.join(risk_parts)})" if risk_parts else ""
        files_str = ", ".join(f.split("/")[-1] for f in d["files"][:5])
        if len(d["files"]) > 5:
            files_str += f" +{len(d['files']) - 5} more"

        lines.append("")
        lines.append(f"{priority}. {d['name']} — {d['change_count']} changes{risk_str}")
        lines.append(f"   Files: {files_str}")
        lines.append(
            f'   → recon(task="review {d["name"]} changes", '
            f"read_only=True, pinned_paths={d['files'][:8]})"
        )

    if cross_edges:
        lines.append("")
        lines.append("CROSS-DOMAIN EDGES (verify interface compatibility):")
        for edge in cross_edges:
            lines.append(f"  {edge['from_domain']} → {edge['to_domain']}")

    lines.append("")
    lines.append(
        "WORKFLOW: For each domain above, call recon(read_only=True, "
        "pinned_paths=...) to get context, then recon_resolve to read "
        "changed files. Focus on breaking changes and high-risk body changes."
    )

    return "\n".join(lines)


def _result_to_text(result: SemanticDiffResult) -> dict[str, Any]:
    """Convert SemanticDiffResult to losslessly-compressed text format.

    Structural changes are rendered as flat text lines with three compression
    strategies applied:

    1. **Nested path dedup** — nested entries (methods inside a class) omit the
       file path and show only ``:start-end`` since the parent already carries it.
    2. **risk:unknown omitted** — the default risk level is dropped; a header
       comment documents the convention.
    3. **Test aliases** — test file paths that appear 3+ times are replaced with
       short aliases (``t1``, ``t2``, …) defined in a header line.

    Format per top-level change:
        {change} {kind} {name}  {path}:{start}-{end}  Δ{lines}  risk:{risk}  refs:{N}  tests:{list}
    Format per nested change:
        {change} {kind} {name}  :{start}-{end}  Δ{lines}  …
    """
    from collections import Counter

    from codeplane.mcp.tools.index import _change_to_text

    # --- Domain classification ---
    domains = _classify_domains(result.structural_changes, result.non_structural_changes)
    cross_edges = _detect_cross_domain_edges(result.structural_changes, domains)
    agentic_hint = _build_agentic_hint(result, domains, cross_edges)

    # --- Phase 1: render raw lines ---
    raw_lines: list[str] = []
    for c in result.structural_changes:
        raw_lines.extend(_change_to_text(c))

    # --- Phase 2: build test aliases (paths appearing 3+ times) ---
    test_counter: Counter[str] = Counter()
    for line in raw_lines:
        idx = line.find("tests:")
        if idx != -1:
            tests_str = line[idx + 6 :].split("  ")[0]  # stop at next double-space field
            for t in tests_str.split(","):
                if t:
                    test_counter[t] += 1

    aliases: dict[str, str] = {}
    alias_idx = 0
    for test_path, count in test_counter.most_common():
        if count >= 3 and (len(test_path) - 2) * count > 50:
            alias_idx += 1
            aliases[test_path] = f"t{alias_idx}"

    # --- Phase 3: apply nested path dedup + test aliases ---
    structural_lines: list[str] = []

    if raw_lines:
        # Header comments (lossless documentation)
        structural_lines.append("# entries without risk: have unknown risk")
        if aliases:
            alias_defs = ", ".join(f"{v}={k}" for k, v in aliases.items())
            structural_lines.append(f"# test aliases: {alias_defs}")

    for line in raw_lines:
        new_line = line

        # Nested path dedup: indented lines keep only :start-end
        if new_line.startswith("  "):
            m = re.search(r"  ([\w/.]+\.\w+):(\d+-\d+)", new_line)
            if m:
                # Replace "  path/to/file.py:10-20" with "  :10-20"
                new_line = new_line[: m.start()] + "  :" + m.group(2) + new_line[m.end() :]

        # Apply test aliases
        for test_path, alias in aliases.items():
            new_line = new_line.replace(test_path, alias)

        structural_lines.append(new_line)

    non_structural_lines: list[str] = []
    for f in result.non_structural_changes:
        parts = [f"{f.status} {f.path}  {f.category}"]
        if f.language:
            parts.append(f"  {f.language}")
        non_structural_lines.append("".join(parts))

    response: dict[str, Any] = {
        "summary": result.summary,
        "breaking_summary": result.breaking_summary,
        "files_analyzed": result.files_analyzed,
        "base": result.base_description,
        "target": result.target_description,
        "structural_changes": structural_lines,
        "non_structural_changes": non_structural_lines,
        "agentic_hint": agentic_hint,
    }

    if len(domains) > 1:
        response["domains"] = domains
        if cross_edges:
            response["cross_domain_edges"] = cross_edges

    if result.scope:
        scope_d = {k: v for k, v in asdict(result.scope).items() if v is not None}
        response["scope"] = scope_d

    return response


def _result_to_dict(
    result: SemanticDiffResult,
    *,
    verbosity: Literal["full", "standard", "minimal"] = "full",
) -> dict[str, Any]:
    """Convert SemanticDiffResult to a serializable dict.

    Verbosity levels:
    - full: Everything (default)
    - standard: Omit change_preview
    - minimal: Just path/kind/name/change (no impact, nested_changes, signatures, etc.)
    """

    def _change_to_dict(c: StructuralChange) -> dict[str, Any]:
        # Minimal: just essential fields
        if verbosity == "minimal":
            return {
                "path": c.path,
                "kind": c.kind,
                "name": c.name,
                "change": c.change,
            }

        d: dict[str, Any] = {
            "path": c.path,
            "kind": c.kind,
            "name": c.name,
            "change": c.change,
            "structural_severity": c.structural_severity,
            "behavior_change_risk": c.behavior_change_risk,
            "classification_confidence": c.classification_confidence,
        }
        # Schema invariant: risk_basis present when risk != low
        if c.risk_basis:
            d["risk_basis"] = c.risk_basis
        elif c.behavior_change_risk != "low":
            d["risk_basis"] = "unclassified_change"
        if c.qualified_name:
            d["qualified_name"] = c.qualified_name
        if c.entity_id:
            d["entity_id"] = c.entity_id
        # Rename-specific fields for correlation
        if c.change == "renamed":
            if c.old_name:
                d["old_name"] = c.old_name
            if c.previous_entity_id:
                d["previous_entity_id"] = c.previous_entity_id
        # Schema invariant: signature_changed requires both sigs
        if c.change == "signature_changed":
            d["old_signature"] = c.old_sig or ""
            d["new_signature"] = c.new_sig or ""
        else:
            if c.old_sig:
                d["old_signature"] = c.old_sig
            if c.new_sig:
                d["new_signature"] = c.new_sig
        if c.start_line:
            d["start_line"] = c.start_line
            if c.start_col:
                d["start_col"] = c.start_col
        if c.end_line:
            d["end_line"] = c.end_line
            if c.end_col:
                d["end_col"] = c.end_col
        # Schema invariant: body_changed requires lines_changed
        if c.change == "body_changed":
            d["lines_changed"] = c.lines_changed if c.lines_changed is not None else 0
        elif c.lines_changed is not None:
            d["lines_changed"] = c.lines_changed
        if c.delta_tags:
            d["delta_tags"] = c.delta_tags
        # Omit change_preview in standard mode (saves ~50% for body_changed)
        if c.change_preview and verbosity == "full":
            d["change_preview"] = c.change_preview
        if c.impact:
            impact_d: dict[str, Any] = {}
            for k, v in asdict(c.impact).items():
                if v is None:
                    continue
                if k == "ref_tiers" and v is not None:
                    # RefTierBreakdown is a dataclass; asdict already made it a dict
                    impact_d[k] = v
                else:
                    impact_d[k] = v
            d["impact"] = impact_d
        if c.nested_changes:
            d["nested_changes"] = [_change_to_dict(nc) for nc in c.nested_changes]
        return d

    agentic_hint = _build_agentic_hint(result)

    structural_items = [_change_to_dict(c) for c in result.structural_changes]
    non_structural_items = [asdict(f) for f in result.non_structural_changes]

    response: dict[str, Any] = {
        "summary": result.summary,
        "breaking_summary": result.breaking_summary,
        "files_analyzed": result.files_analyzed,
        "base": result.base_description,
        "target": result.target_description,
        "structural_changes": structural_items,
        "non_structural_changes": non_structural_items,
        **(
            {"scope": {k: v for k, v in asdict(result.scope).items() if v is not None}}
            if result.scope
            else {}
        ),
        "agentic_hint": agentic_hint,
    }

    return response
