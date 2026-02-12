"""Layer 4: MCP tool for semantic diff.

Orchestrates the full pipeline: sources -> engine -> enrichment -> output.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

from codeplane.core.languages import detect_language_family, has_grammar
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

        return _result_to_dict(result)


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

        status_map = {
            1: "added",
            2: "deleted",
            3: "modified",
            5: "renamed",
        }  # pygit2 delta.status codes
        status = status_map.get(patch.delta.status, "modified")  # type: ignore[union-attr]

        lang = detect_language_family(file_path)
        file_has_grammar = bool(lang and has_grammar(lang))

        changed_files.append(ChangedFile(file_path, status, file_has_grammar))

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

    result.base_description = base or "HEAD"
    result.target_description = target or "working tree"

    return result


def _run_epoch_diff(
    app_ctx: AppContext,
    base: str,
    target: str | None,
    paths: list[str] | None,
) -> SemanticDiffResult:
    """Run semantic diff in epoch mode."""
    from codeplane.index.models import DefSnapshotRecord

    base_epoch = int(base.split(":")[1])
    target_epoch = int(target.split(":")[1]) if target and target.startswith("epoch:") else None

    coordinator = app_ctx.coordinator
    db = coordinator.db

    with db.session() as session:
        from sqlmodel import select

        # Find all files that have snapshots in either epoch
        base_files_stmt = (
            select(DefSnapshotRecord.file_path)
            .where(DefSnapshotRecord.epoch_id == base_epoch)
            .distinct()
        )
        base_file_paths = set(session.exec(base_files_stmt).all())

        target_file_paths: set[str] = set()
        if target_epoch is not None:
            target_files_stmt = (
                select(DefSnapshotRecord.file_path)
                .where(DefSnapshotRecord.epoch_id == target_epoch)
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

            if fp in base_file_paths and fp not in target_file_paths:
                status = "deleted"
            elif fp not in base_file_paths and fp in target_file_paths:
                status = "added"
            else:
                status = "modified"

            changed_files.append(ChangedFile(fp, status, file_has_grammar))

            base_facts[fp] = snapshots_from_epoch(session, base_epoch, fp)
            if target_epoch is not None:
                target_facts[fp] = snapshots_from_epoch(session, target_epoch, fp)
            else:
                target_facts[fp] = snapshots_from_index(session, fp)

    # Run engine (no hunks in epoch mode)
    raw = compute_structural_diff(base_facts, target_facts, changed_files, hunks=None)

    # Enrich
    with db.session() as session:
        result = enrich_diff(raw, session, app_ctx.repo_root)

    result.base_description = f"epoch {base_epoch}"
    result.target_description = f"epoch {target_epoch}" if target_epoch else "current index"

    return result


def _build_agentic_hint(result: SemanticDiffResult) -> str:
    """Build priority-ordered action list for the agent."""
    if not result.structural_changes:
        return "No actionable changes detected."

    hints: list[str] = []

    # Priority 1: Signature changes with references
    for c in result.structural_changes:
        if c.change == "signature_changed" and c.impact:
            ref_count = c.impact.reference_count or 0
            name = c.qualified_name or f"{c.name}()"
            hints.append(
                f"Signature of {name} changed — {ref_count} references in "
                f"{len(c.impact.referencing_files or [])} files may need updating."
            )

    # Priority 2: Removed symbols
    for c in result.structural_changes:
        if c.change == "removed":
            hints.append(f"{c.name} was removed — check for broken references.")

    # Priority 3: Body changes summary
    body_changes = [c for c in result.structural_changes if c.change == "body_changed"]
    if body_changes:
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


def _result_to_dict(result: SemanticDiffResult) -> dict[str, Any]:
    """Convert SemanticDiffResult to a serializable dict."""

    def _change_to_dict(c: StructuralChange) -> dict[str, Any]:
        d: dict[str, Any] = {
            "path": c.path,
            "kind": c.kind,
            "name": c.name,
            "change": c.change,
            "severity": c.severity,
        }
        if c.qualified_name:
            d["qualified_name"] = c.qualified_name
        if c.old_sig:
            d["old_signature"] = c.old_sig
        if c.new_sig:
            d["new_signature"] = c.new_sig
        if c.impact:
            d["impact"] = {k: v for k, v in asdict(c.impact).items() if v is not None}
        if c.nested_changes:
            d["nested_changes"] = [_change_to_dict(nc) for nc in c.nested_changes]
        return d

    agentic_hint = _build_agentic_hint(result)

    return {
        "summary": result.summary,
        "breaking_summary": result.breaking_summary,
        "files_analyzed": result.files_analyzed,
        "base": result.base_description,
        "target": result.target_description,
        "structural_changes": [_change_to_dict(c) for c in result.structural_changes],
        "non_structural_changes": result.non_structural_changes,
        "agentic_hint": agentic_hint,
    }
