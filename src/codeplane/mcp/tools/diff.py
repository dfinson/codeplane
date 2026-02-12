"""Layer 4: MCP tool for semantic diff.

Orchestrates the full pipeline: sources -> engine -> enrichment -> output.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

from codeplane.config.constants import DIFF_CHANGES_MAX
from codeplane.core.languages import detect_language_family, has_grammar
from codeplane.git.models import _DELTA_STATUS_MAP
from codeplane.index._internal.diff.engine import compute_structural_diff
from codeplane.index._internal.diff.enrichment import enrich_diff
from codeplane.index._internal.diff.models import (
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


def register_tools(mcp: FastMCP, app_ctx: AppContext) -> None:
    """Register semantic_diff MCP tool."""

    @mcp.tool
    async def semantic_diff(
        ctx: Context,
        base: str = Field("HEAD", description="Base ref (commit, branch, tag) or epoch:N"),
        target: str | None = Field(None, description="Target ref (None = working tree)"),
        paths: list[str] | None = Field(None, description="Limit to specific paths"),
        cursor: str | None = Field(None, description="Pagination cursor"),
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

        return _result_to_dict(result, cursor=cursor)


def _run_git_diff(
    app_ctx: AppContext,
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

    for cf in changed_files:
        if not cf.has_grammar:
            continue

        # Target: current index state
        with db.session() as session:
            target_facts[cf.path] = snapshots_from_index(session, cf.path)

        # Base: parse from git blob
        if base_commit and cf.status != "added":
            base_facts[cf.path] = snapshots_from_blob(repo, base_commit, cf.path)
        else:
            base_facts[cf.path] = []

    # Run engine
    raw = compute_structural_diff(base_facts, target_facts, changed_files, hunks)

    # Enrich
    with db.session() as session:
        result = enrich_diff(raw, session, app_ctx.repo_root)

    # Annotate with change previews from the actual patch lines
    _annotate_change_previews(result, pygit2_diff)

    result.base_description = base or "HEAD"
    result.target_description = target or "working tree"

    return result


def _parse_epoch_ref(ref: str) -> int:
    """Parse an epoch reference like 'epoch:3' into an integer.

    Only numeric epoch IDs are supported (e.g. epoch:1, epoch:42).
    Named aliases like 'epoch:previous' are not implemented.

    Raises:
        ValueError: If the epoch value is not a valid integer.
    """
    parts = ref.split(":", 1)
    if len(parts) != 2 or parts[0] != "epoch":
        msg = f"Invalid epoch reference: {ref!r}. Expected format: epoch:<int>"
        raise ValueError(msg)
    try:
        return int(parts[1])
    except ValueError:
        msg = (
            f"Invalid epoch value: {parts[1]!r}. "
            f"Only numeric epoch IDs are supported (e.g. epoch:1, epoch:42)."
        )
        raise ValueError(msg) from None


def _run_epoch_diff(
    app_ctx: AppContext,
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

    # Enrich
    with db.session() as session:
        result = enrich_diff(raw, session, app_ctx.repo_root)

    result.base_description = f"epoch {base_epoch}"
    result.target_description = f"epoch {target_epoch}" if target_epoch else "current index"

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


def _build_agentic_hint(result: SemanticDiffResult) -> str:
    """Build priority-ordered action list for the agent."""
    if not result.structural_changes:
        return "No actionable changes detected."

    hints: list[str] = []

    # Priority 1: Signature changes with references
    for c in result.structural_changes:
        if c.change == "signature_changed" and c.impact:
            ref_count = c.impact.reference_count or 0
            tiers = c.impact.ref_tiers
            name = c.qualified_name or f"{c.name}()"
            tier_detail = ""
            if tiers and tiers.total > 0:
                tier_detail = (
                    f" (proven={tiers.proven}, strong={tiers.strong}, "
                    f"anchored={tiers.anchored}, unknown={tiers.unknown})"
                )
            hints.append(
                f"Signature of {name} changed — {ref_count} references{tier_detail} in "
                f"{len(c.impact.referencing_files or [])} files may need updating."
            )

    # Priority 2: Removed symbols
    for c in result.structural_changes:
        if c.change == "removed":
            hints.append(f"{c.name} was removed — check for broken references.")

    # Priority 3: Body changes with risk assessment
    body_changes = [c for c in result.structural_changes if c.change == "body_changed"]
    if body_changes:
        high_risk = [c for c in body_changes if c.behavior_change_risk in ("high", "medium")]
        if high_risk:
            hints.append(
                f"{len(body_changes)} function bodies changed "
                f"({len(high_risk)} with elevated behavior-change risk) — review for correctness."
            )
        else:
            hints.append(f"{len(body_changes)} function bodies changed — review for correctness.")

    # Priority 4: Affected tests
    all_test_files: set[str] = set()
    for c in result.structural_changes:
        if c.impact and c.impact.affected_test_files:
            all_test_files.update(c.impact.affected_test_files)
    if all_test_files:
        hints.append(
            "Affected test files:\n" + "\n".join(f"  - {f}" for f in sorted(all_test_files))
        )

    return "\n".join(hints)


def _result_to_dict(
    result: SemanticDiffResult,
    *,
    cursor: str | None = None,
    limit: int = DIFF_CHANGES_MAX,
) -> dict[str, Any]:
    """Convert SemanticDiffResult to a serializable dict with pagination."""

    def _change_to_dict(c: StructuralChange) -> dict[str, Any]:
        d: dict[str, Any] = {
            "path": c.path,
            "kind": c.kind,
            "name": c.name,
            "change": c.change,
            "structural_severity": c.structural_severity,
            "behavior_change_risk": c.behavior_change_risk,
        }
        if c.qualified_name:
            d["qualified_name"] = c.qualified_name
        if c.entity_id:
            d["entity_id"] = c.entity_id
        if c.old_sig:
            d["old_signature"] = c.old_sig
        if c.new_sig:
            d["new_signature"] = c.new_sig
        if c.start_line is not None:
            d["start_line"] = c.start_line
            if c.start_col is not None:
                d["start_col"] = c.start_col
        if c.end_line is not None:
            d["end_line"] = c.end_line
            if c.end_col is not None:
                d["end_col"] = c.end_col
        if c.lines_changed is not None:
            d["lines_changed"] = c.lines_changed
        if c.delta_tags:
            d["delta_tags"] = c.delta_tags
        if c.change_preview:
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

    # Compute agentic_hint from ALL changes before pagination
    agentic_hint = _build_agentic_hint(result)

    # Paginate structural_changes
    all_changes = result.structural_changes
    offset = int(cursor) if cursor else 0
    page = all_changes[offset : offset + limit]
    has_more = offset + limit < len(all_changes)

    pagination: dict[str, Any] = {}
    if has_more:
        pagination["next_cursor"] = str(offset + limit)
        pagination["total_estimate"] = len(all_changes)

    return {
        "summary": result.summary,
        "breaking_summary": result.breaking_summary,
        "files_analyzed": result.files_analyzed,
        "base": result.base_description,
        "target": result.target_description,
        "structural_changes": [_change_to_dict(c) for c in page],
        "non_structural_changes": [asdict(f) for f in result.non_structural_changes],
        "agentic_hint": agentic_hint,
        "pagination": pagination,
    }
