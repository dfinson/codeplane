"""Layer 4: MCP tool for semantic diff.

Orchestrates the full pipeline: sources -> engine -> enrichment -> output.
"""

# Removed: from __future__ import annotations - breaks FastMCP+pydantic Literal resolution

import contextlib
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

    @mcp.tool
    async def semantic_diff(
        ctx: Context,
        base: str = Field("HEAD", description="Base ref (commit, branch, tag) or epoch:N"),
        target: str | None = Field(None, description="Target ref (None = working tree)"),
        paths: list[str] | None = Field(None, description="Limit to specific paths"),
        verbosity: Literal["full", "standard", "minimal"] = Field(
            "full",
            description=(
                "Output detail level: full=everything, standard=omit change_preview, "
                "minimal=just path/kind/name/change"
            ),
        ),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
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

        from codeplane.mcp.delivery import wrap_existing_response

        result_dict = _result_to_text(result)

        # Track scope usage
        scope_usage = None
        if scope_id:
            from codeplane.mcp.tools.files import _scope_manager

            budget = _scope_manager.get_or_create(scope_id)
            scope_usage = budget.to_usage_dict()

        return wrap_existing_response(
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


def _build_agentic_hint(result: SemanticDiffResult) -> str:
    """Build compact action hint for the agent.

    Returns counts-only summary. Full details are in structural_changes
    and breaking_summary - no need to duplicate here.
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

    # Build compact hint
    parts: list[str] = []
    if sig_changes:
        parts.append(f"{sig_changes} signature changes")
    if removals:
        parts.append(f"{removals} removals")
    if body_changes:
        risk_note = f" ({high_risk} high-risk)" if high_risk else ""
        parts.append(f"{len(body_changes)} body changes{risk_note}")
    if additions:
        parts.append(f"{additions} additions")

    if not parts:
        return "No actionable changes."

    hint = ", ".join(parts) + "."
    if all_test_files:
        hint += f" Run {len(all_test_files)} affected test files."

    return hint


def _result_to_text(result: SemanticDiffResult) -> dict[str, Any]:
    """Convert SemanticDiffResult to compact text format.

    Same information as _result_to_dict, but structural_changes rendered as
    flat text lines instead of nested JSON objects.
    Format per change: {change} {kind} {name}  {path}:{start}-{end}  Δ{lines}  risk:{risk}  refs:{N}  tests:{list}
    """
    from codeplane.mcp.tools.index import _change_to_text

    agentic_hint = _build_agentic_hint(result)

    structural_lines: list[str] = []
    for c in result.structural_changes:
        structural_lines.extend(_change_to_text(c))

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
