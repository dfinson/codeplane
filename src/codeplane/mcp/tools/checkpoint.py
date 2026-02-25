"""Checkpoint MCP tool — lint, test, commit in one call.

Chains:  lint (auto-fix) → affected tests → stage → hooks → commit → push → semantic diff
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from codeplane.git._internal.hooks import run_hook
from codeplane.git.errors import EmptyCommitMessageError, PathsNotFoundError

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext
    from codeplane.testing.models import TestResult

log = structlog.get_logger(__name__)


# =============================================================================
# Commit Helpers
# =============================================================================


def _validate_commit_message(message: str) -> None:
    """Validate commit message is not empty or whitespace-only."""
    if not message or not message.strip():
        raise EmptyCommitMessageError()


def _validate_paths_exist(repo_path: Path, paths: list[str]) -> None:
    """Validate all paths exist in the repository or working tree.

    Raises PathsNotFoundError with details about which paths are missing.
    """
    if not paths:
        return

    missing: list[str] = []
    for p in paths:
        full_path = repo_path / p
        if not full_path.exists():
            missing.append(p)

    if missing:
        raise PathsNotFoundError(missing)


def _run_hook_with_retry(
    repo_path: Path,
    paths_to_restage: list[str],
    stage_fn: Any,
) -> tuple[Any, dict[str, Any] | None]:
    """Run pre-commit hooks with auto-fix retry logic.

    Returns:
        Tuple of (hook_result, failure_response).
        If failure_response is None, hooks passed and commit can proceed.
    """
    hook_result = run_hook(repo_path, "pre-commit")

    if hook_result.success:
        return hook_result, None

    auto_fixed = hook_result.modified_files or []

    if not auto_fixed:
        return hook_result, {
            "hook_failure": {
                "code": "HOOK_FAILED",
                "hook_type": "pre-commit",
                "exit_code": hook_result.exit_code,
                "stdout": hook_result.stdout,
                "stderr": hook_result.stderr,
                "modified_files": [],
            },
            "summary": f"pre-commit hook failed (exit {hook_result.exit_code})",
            "agentic_hint": (
                "Hook failed with errors that require manual fixing. "
                "Review the output above and fix the reported issues, then retry."
            ),
        }

    # Hook auto-fixed files — re-stage and retry
    restage_paths = list(set(auto_fixed + paths_to_restage))
    stage_fn(restage_paths)

    retry_result = run_hook(repo_path, "pre-commit")

    if not retry_result.success:
        return hook_result, {
            "hook_failure": {
                "code": "HOOK_FAILED_AFTER_RETRY",
                "hook_type": "pre-commit",
                "exit_code": retry_result.exit_code,
                "attempts": [
                    {
                        "attempt": 1,
                        "exit_code": hook_result.exit_code,
                        "stdout": hook_result.stdout,
                        "stderr": hook_result.stderr,
                        "auto_fixed_files": auto_fixed,
                    },
                    {
                        "attempt": 2,
                        "exit_code": retry_result.exit_code,
                        "stdout": retry_result.stdout,
                        "stderr": retry_result.stderr,
                        "auto_fixed_files": retry_result.modified_files or [],
                    },
                ],
            },
            "summary": "pre-commit hook failed after auto-fix retry",
            "agentic_hint": (
                "Hook auto-fixed files on the first attempt but still failed on retry. "
                "This requires manual fixing."
            ),
        }

    return hook_result, None


def _summarize_commit(sha: str, message: str) -> str:
    from codeplane.core.formatting import truncate_at_word

    short_sha = sha[:7]
    first_line = message.split("\n")[0]
    truncated = truncate_at_word(first_line, 45)
    return f'{short_sha} "{truncated}"'


# =============================================================================
# Target Matching
# =============================================================================


def _normalize_selector(selector: str) -> str:
    """Normalize target selector for path matching.

    Handles Go package selectors (./path), wildcard selectors (./...),
    and project root selectors (.).
    """
    if selector in (".", "./..."):
        return ""
    if selector.startswith("./"):
        return selector[2:]
    return selector


def _target_matches_affected_files(
    target: Any,
    affected_paths: set[str],
    repo_root: Path,
) -> bool:
    """Check if a test target's scope contains any affected test file.

    For 'file' targets (e.g., Python pytest), this is an exact path match.
    For 'package' targets (e.g., Go packages), checks if any affected file
    is within the package directory.
    For 'project' targets (e.g., Maven modules, Gradle), checks if any affected
    file is within the project root scope.
    """
    ws = Path(target.workspace_root)
    sel = _normalize_selector(target.selector)
    scope_abs = ws / sel if sel else ws

    try:
        scope_rel = str(scope_abs.relative_to(repo_root))
    except ValueError:
        # Target workspace outside repo root, fall back to exact selector match
        return target.selector in affected_paths

    if scope_rel == ".":
        # Scope is the entire repo — all files match
        return bool(affected_paths)

    return any(p == scope_rel or p.startswith(scope_rel + "/") for p in affected_paths)


# =============================================================================
# Test Result Helpers
# =============================================================================


def _summarize_run(result: "TestResult") -> str:
    """Generate compact summary for a test run."""
    if not result.run_status:
        return "no run status"

    status = result.run_status
    if status.progress:
        p = status.progress
        if status.status == "completed":
            if p.cases.failed > 0:
                return (
                    f"{p.cases.passed} passed, {p.cases.failed} failed "
                    f"({status.duration_seconds:.1f}s)"
                )
            return f"{p.cases.passed} passed ({status.duration_seconds:.1f}s)"
        elif status.status == "running":
            parts = [f"{p.cases.passed} passed"]
            if p.cases.failed:
                parts.append(f"{p.cases.failed} failed")
            return f"running: {p.targets.completed}/{p.targets.total} targets ({', '.join(parts)})"
        elif status.status == "cancelled":
            return "cancelled"
        elif status.status == "failed":
            return "run failed"
        # Other statuses
        status_parts: list[str] = [status.status]
        if p.cases.total > 0:
            status_parts.append(f"{p.cases.passed}/{p.cases.total} passed")
            if p.cases.failed:
                status_parts.append(f"{p.cases.failed} failed")
        return ", ".join(status_parts)

    return status.status


def _display_run(result: "TestResult") -> str | None:
    """Human-friendly run message — only on completion or failure."""
    if not result.run_status:
        return None
    status = result.run_status
    if status.status == "completed":
        p = status.progress
        if p and p.cases.failed > 0:
            return (
                f"Tests completed: {p.cases.passed} passed, {p.cases.failed} FAILED "
                f"in {status.duration_seconds:.1f}s."
            )
        elif p:
            return f"Tests completed: {p.cases.passed} passed in {status.duration_seconds:.1f}s."
        return "Tests completed."
    elif status.status == "cancelled":
        return "Test run was cancelled."
    elif status.status == "failed":
        return "Test run failed to start."
    return None


def _build_coverage_hint(
    coverage_artifacts: list[dict[str, str]],
    target_selectors: list[str] | None = None,
) -> str:
    """Build guidance for interpreting coverage data."""
    if not coverage_artifacts:
        return "No coverage data available."

    hints: list[str] = []

    if target_selectors:
        hints.append(
            "Executed test targets:\n"
            + "\n".join(f"  - {sel}" for sel in target_selectors[:10])
            + (
                f"\n  ... and {len(target_selectors) - 10} more"
                if len(target_selectors) > 10
                else ""
            )
        )

    # Dedupe coverage artifacts by path
    seen_paths: set[str] = set()
    deduped: list[dict[str, str]] = []
    for cov in coverage_artifacts:
        path = cov.get("path", "")
        if path and path not in seen_paths:
            seen_paths.add(path)
            deduped.append(cov)

    for cov in deduped:
        fmt = cov.get("format", "unknown")
        path = cov.get("path", "")

        if fmt == "lcov":
            hints.append(
                f"Coverage file: {path}\n"
                "  Format: LCOV (line-by-line coverage)\n"
                "  Reading: Look for 'SF:' (source file), 'DA:line,count' (line hits), "
                "'LF:' (lines found), 'LH:' (lines hit)\n"
                "  Note: File includes ALL project sources. Focus on files matching "
                "your test paths."
            )
        elif fmt == "istanbul":
            hints.append(
                f"Coverage directory: {path}\n"
                "  Format: Istanbul/NYC (JSON + LCOV)\n"
                "  Files: coverage-summary.json (overview), lcov.info (line detail)\n"
                "  Note: Focus on source files corresponding to your test targets."
            )
        elif fmt == "gocov":
            hints.append(
                f"Coverage file: {path}\n"
                "  Format: Go coverage profile\n"
                "  Reading: 'mode: set/count/atomic', then 'file:start.col,end.col count'\n"
                "  Note: Go coverage is package-scoped."
            )
        elif fmt == "jacoco":
            hints.append(
                f"Coverage directory: {path}\n"
                "  Format: JaCoCo (XML + HTML)\n"
                "  Files: jacoco.xml (machine-readable), index.html (human-readable)\n"
                "  Note: Coverage tied to modules configured in build file."
            )
        else:
            hints.append(f"Coverage: {path} (format: {fmt})")

    return "\n\n".join(hints)


def _serialize_test_result(result: "TestResult") -> dict[str, Any]:
    """Convert TestResult to serializable dict."""
    output: dict[str, Any] = {
        "summary": _summarize_run(result),
    }

    display = _display_run(result)
    if display:
        output["display_to_user"] = display

    if result.run_status:
        status = result.run_status

        output["run_status"] = {
            "run_id": status.run_id,
            "status": status.status,
            "duration_seconds": status.duration_seconds,
            "artifact_dir": status.artifact_dir,
        }
        if status.progress:
            progress = status.progress
            output["run_status"]["progress"] = {
                "targets": {
                    "total": progress.targets.total,
                    "completed": progress.targets.completed,
                    "running": progress.targets.running,
                    "failed": progress.targets.failed,
                },
                "cases": {
                    "total": progress.cases.total,
                    "passed": progress.cases.passed,
                    "failed": progress.cases.failed,
                    "skipped": progress.cases.skipped,
                    "errors": progress.cases.errors,
                },
                "total": progress.total,
                "completed": progress.completed,
                "passed": progress.passed,
                "failed": progress.failed,
                "skipped": progress.skipped,
            }
        if status.failures:
            output["run_status"]["failures"] = [
                {
                    "name": f.name,
                    "path": f.path,
                    "line": f.line,
                    "message": f.message,
                    "traceback": f.traceback,
                    "classname": f.classname,
                    "duration_seconds": f.duration_seconds,
                }
                for f in status.failures
            ]
        if status.diagnostics:
            output["run_status"]["diagnostics"] = [
                {
                    "target_id": d.target_id,
                    "error_type": d.error_type,
                    "error_detail": d.error_detail,
                    "suggested_action": d.suggested_action,
                    "command": d.command,
                    "working_directory": d.working_directory,
                    "exit_code": d.exit_code,
                }
                for d in status.diagnostics
            ]
        if status.coverage:
            output["run_status"]["coverage"] = status.coverage
            output["run_status"]["coverage_hint"] = _build_coverage_hint(
                status.coverage,
                status.target_selectors,
            )
            from codeplane.testing.coverage import CoverageArtifact, parse_coverage_summary

            coverage_stats: list[dict[str, Any]] = []
            for cov_dict in status.coverage:
                artifact = CoverageArtifact(
                    format=cov_dict.get("format", "unknown"),
                    path=Path(cov_dict.get("path", "")),
                    pack_id=cov_dict.get("pack_id", ""),
                    invocation_id="",
                )
                summary = parse_coverage_summary(artifact)
                if summary and summary.is_valid:
                    coverage_stats.append(summary.to_dict())
            if coverage_stats:
                output["run_status"]["coverage_stats"] = coverage_stats

    if isinstance(result.agentic_hint, str) and result.agentic_hint:
        output["agentic_hint"] = result.agentic_hint

    return output


# =============================================================================
# Verify Summary
# =============================================================================


def _summarize_verify(
    lint_status: str,
    lint_diagnostics: int,
    test_passed: int,
    test_failed: int,
    test_status: str,
) -> str:
    """Generate compact summary for verify result."""
    parts: list[str] = []

    if lint_status == "clean":
        parts.append("lint: clean")
    elif lint_status == "skipped":
        parts.append("lint: skipped")
    elif lint_diagnostics > 0:
        parts.append(f"lint: {lint_diagnostics} issues")
    else:
        parts.append(f"lint: {lint_status}")

    if test_status == "skipped":
        parts.append("tests: skipped")
    elif test_failed > 0:
        parts.append(f"tests: {test_passed} passed, {test_failed} FAILED")
    elif test_passed > 0:
        parts.append(f"tests: {test_passed} passed")
    else:
        parts.append(f"tests: {test_status}")

    return " | ".join(parts)


# =============================================================================
# Tiered Test Execution
# =============================================================================

# Targets with estimated_cost at or below this threshold are batched together
# into a single subprocess call when they share the same runner + workspace.
_BATCH_COST_THRESHOLD = 1.0


def _assign_target_hops(
    targets: list[Any],
    graph_result: Any,
    repo_root: Path,
) -> dict[int, list[Any]]:
    """Map test targets to their import-graph hop distance.

    Returns dict[hop_number, list_of_targets].  Targets that don't match any
    hop in the graph result (e.g., discovered-but-not-in-graph) default to
    hop 0 to ensure they always run.
    """
    from codeplane.index._internal.indexing.import_graph import ImportGraphResult

    assert isinstance(graph_result, ImportGraphResult)
    tests_by_hop = graph_result.tests_by_hop()

    # Build reverse map: test_file -> hop
    file_to_hop: dict[str, int] = {}
    for hop, files in tests_by_hop.items():
        for f in files:
            if f not in file_to_hop:
                file_to_hop[f] = hop

    # Map targets to hops via _target_matches_affected_files logic
    hop_targets: dict[int, list[Any]] = {}
    for target in targets:
        ws = Path(target.workspace_root)
        sel = _normalize_selector(target.selector)
        scope_abs = ws / sel if sel else ws

        try:
            scope_rel = str(scope_abs.relative_to(repo_root))
        except ValueError:
            scope_rel = target.selector

        # Find the hop for this target's file path
        if scope_rel in file_to_hop:
            hop = file_to_hop[scope_rel]
        else:
            # Target didn't match directly — check prefix matching
            hop = 0  # default to hop 0 (always run)
            for fpath, fhop in file_to_hop.items():
                if fpath == scope_rel or fpath.startswith(scope_rel + "/"):
                    hop = fhop
                    break

        hop_targets.setdefault(hop, []).append(target)

    return hop_targets


def _partition_for_batching(
    targets: list[Any],
) -> tuple[list[list[Any]], list[Any]]:
    """Split targets into batchable groups and solo targets.

    Batchable: multiple targets that share the same (runner_pack_id,
    workspace_root) and all have estimated_cost <= _BATCH_COST_THRESHOLD.

    Solo: targets with higher estimated cost, or unique runner/workspace
    combinations.

    Returns (batch_groups, solo_targets).
    """
    from collections import defaultdict

    # Group by (runner_pack_id, workspace_root)
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for t in targets:
        key = (t.runner_pack_id, t.workspace_root)
        groups[key].append(t)

    batch_groups: list[list[Any]] = []
    solo_targets: list[Any] = []

    for _key, group in groups.items():
        # Separate low-cost and high-cost targets
        low_cost = [t for t in group if t.estimated_cost <= _BATCH_COST_THRESHOLD]
        high_cost = [t for t in group if t.estimated_cost > _BATCH_COST_THRESHOLD]

        solo_targets.extend(high_cost)

        if len(low_cost) >= 2:
            # Worth batching: 2+ targets save subprocess overhead
            batch_groups.append(low_cost)
        else:
            # Single target — no point batching
            solo_targets.extend(low_cost)

    return batch_groups, solo_targets


async def _run_tiered_tests(
    *,
    app_ctx: "AppContext",
    ctx: Any,
    graph_result: Any,
    filtered_targets: list[Any],
    repo_root: Path,
    test_filter: str | None,
    coverage: bool,
    coverage_dir: str | None,
    phase: int,
    total_phases: int,
) -> dict[str, Any]:
    """Execute tests in hop tiers: direct tests first, then transitive.

    If direct tests (hop 0) fail, transitive tests (hop 1+) are skipped
    on the assumption that direct-import failures will cascade.

    Within each tier, low-cost targets are batched into fewer subprocess
    invocations to reduce startup overhead.

    Returns a dict with keys: serialized, status, passed, failed, tier_log.
    """
    hop_targets = _assign_target_hops(filtered_targets, graph_result, repo_root)
    sorted_hops = sorted(hop_targets.keys())
    max_hop = sorted_hops[-1] if sorted_hops else 0

    # Accumulate results across tiers
    total_passed = 0
    total_failed = 0
    all_test_results: list[Any] = []
    all_batch_results: list[Any] = []
    tier_log: list[dict[str, Any]] = []
    final_status = "completed"
    stopped_at_hop: int | None = None

    for hop in sorted_hops:
        targets_this_hop = hop_targets[hop]
        target_count = len(targets_this_hop)

        tier_label = "direct" if hop == 0 else f"hop {hop}"
        await ctx.report_progress(
            phase,
            total_phases,
            f"Running {target_count} {tier_label} test target(s)",
        )

        # Partition into batches and solo targets
        batch_groups, solo_targets = _partition_for_batching(targets_this_hop)

        batch_count = sum(len(g) for g in batch_groups)
        solo_count = len(solo_targets)
        batched_into = len(batch_groups)

        if batch_count > 0:
            await ctx.info(
                f"Tier {tier_label}: {solo_count} solo + {batch_count} batched "
                f"into {batched_into} group(s)"
            )

        # Build effective target list: solo targets run individually
        effective_target_ids = [t.target_id for t in solo_targets]

        # Run solo targets via normal test_ops.run
        solo_result = None
        if effective_target_ids:
            solo_result = await app_ctx.test_ops.run(
                targets=effective_target_ids,
                target_filter=None,
                test_filter=test_filter,
                tags=None,
                failed_only=False,
                parallelism=None,
                timeout_sec=None,
                fail_fast=False,
                coverage=coverage,
                coverage_dir=coverage_dir,
            )

        # Run batched targets
        import asyncio
        import uuid

        hop_batch_results: list[Any] = []
        if batch_groups:

            async def run_batch(group: list[Any]) -> Any:
                artifact_dir = (
                    app_ctx.repo_root / ".codeplane" / "artifacts" / "tests" / uuid.uuid4().hex[:8]
                )
                artifact_dir.mkdir(parents=True, exist_ok=True)
                return await app_ctx.test_ops._run_batch_targets(
                    targets=group,
                    artifact_dir=artifact_dir,
                    test_filter=test_filter,
                    tags=None,
                    timeout_sec=300,
                )

            batch_tasks = [asyncio.create_task(run_batch(g)) for g in batch_groups]
            hop_batch_results = await asyncio.gather(*batch_tasks)
            all_batch_results.extend(hop_batch_results)

        # Aggregate results for this tier
        tier_passed = 0
        tier_failed = 0
        tier_total = 0
        tier_duration = 0.0

        if solo_result and solo_result.run_status:
            rs = solo_result.run_status
            if rs.progress:
                tier_passed += rs.progress.cases.passed
                tier_failed += rs.progress.cases.failed
                tier_total += rs.progress.cases.total
            tier_duration += rs.duration_seconds
            all_test_results.append(solo_result)

        for br in hop_batch_results:
            tier_passed += br.passed
            tier_failed += br.failed
            tier_total += br.total
            tier_duration += br.duration_seconds

        total_passed += tier_passed
        total_failed += tier_failed

        tier_entry: dict[str, Any] = {
            "hop": hop,
            "label": tier_label,
            "targets": target_count,
            "batched": batch_count,
            "batch_groups": batched_into,
            "passed": tier_passed,
            "failed": tier_failed,
            "total": tier_total,
            "duration_seconds": round(tier_duration, 2),
        }
        tier_log.append(tier_entry)

        # Tiered fail-fast: if this hop has failures, skip remaining hops
        if tier_failed > 0 and hop < max_hop:
            remaining_hops = [h for h in sorted_hops if h > hop]
            remaining_targets = sum(len(hop_targets[h]) for h in remaining_hops)
            stopped_at_hop = hop

            skipped_info = ", ".join(
                f"hop {h} ({len(hop_targets[h])} targets)" for h in remaining_hops
            )
            tier_entry["stopped_reason"] = (
                f"Failures in {tier_label} — skipped transitive tiers: {skipped_info}"
            )
            await ctx.warning(
                f"Tests: {tier_label} had {tier_failed} failure(s) — "
                f"skipping {remaining_targets} transitive target(s)"
            )
            break

    # Build combined serialized result
    # Use the first solo_result as the base if available
    combined: dict[str, Any] = {}
    if all_test_results and all_test_results[0].run_status:
        combined = _serialize_test_result(all_test_results[0])

        # Overlay batch results into the progress
        for br in all_batch_results:
            if "run_status" in combined:
                prog = combined["run_status"].get("progress", {})
                cases = prog.get("cases", {})
                cases["passed"] = cases.get("passed", 0) + br.passed
                cases["failed"] = cases.get("failed", 0) + br.failed
                cases["skipped"] = cases.get("skipped", 0) + br.skipped
                cases["total"] = cases.get("total", 0) + br.total
    elif all_batch_results:
        # Only batched targets, no solo
        br_total = sum(br.total for br in all_batch_results)
        br_passed = sum(br.passed for br in all_batch_results)
        br_failed = sum(br.failed for br in all_batch_results)
        br_skipped = sum(br.skipped for br in all_batch_results)
        br_duration = sum(br.duration_seconds for br in all_batch_results)
        combined = {
            "summary": (
                f"{br_passed} passed ({br_duration:.1f}s)"
                if br_failed == 0
                else f"{br_passed} passed, {br_failed} failed ({br_duration:.1f}s)"
            ),
            "run_status": {
                "status": "completed",
                "progress": {
                    "cases": {
                        "total": br_total,
                        "passed": br_passed,
                        "failed": br_failed,
                        "skipped": br_skipped,
                    },
                },
            },
        }

    # Add tier execution log for transparency
    combined["tier_execution"] = tier_log

    # Build transparent summary
    tier_log_idx = next(
        (i for i, t in enumerate(tier_log) if t["hop"] == stopped_at_hop),
        None,
    )
    if stopped_at_hop is not None and tier_log_idx is not None:
        combined["summary"] = (
            f"{total_passed} passed, {total_failed} failed "
            f"(stopped at {tier_log[tier_log_idx]['label']}, "
            f"transitive tiers skipped)"
        )
    else:
        total_duration = sum(t["duration_seconds"] for t in tier_log)
        combined["summary"] = (
            f"{total_passed} passed"
            + (f", {total_failed} failed" if total_failed > 0 else "")
            + f" ({total_duration:.1f}s, {len(sorted_hops)} tier(s))"
        )

    return {
        "serialized": combined,
        "status": final_status,
        "passed": total_passed,
        "failed": total_failed,
        "tier_log": tier_log,
    }


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register checkpoint tool with FastMCP server."""

    @mcp.tool(
        title="Checkpoint: lint, test, commit, push",
        annotations=ToolAnnotations(
            title="Checkpoint: lint, test, commit, push",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def checkpoint(
        ctx: Context,
        changed_files: list[str] = Field(
            ...,
            description="Files you changed. Used for impact-aware test selection.",
        ),
        lint: bool = Field(True, description="Run linting"),
        autofix: bool = Field(True, description="Apply lint auto-fixes"),
        tests: bool = Field(True, description="Run affected tests"),
        test_filter: str | None = Field(
            None,
            description="Filter which test names to run within targets "
            "(passed to pytest -k, jest --testNamePattern).",
        ),
        coverage: bool = Field(False, description="Collect coverage data"),
        coverage_dir: str | None = Field(
            None,
            description="Directory for coverage artifacts (required when coverage=True).",
        ),
        commit_message: str | None = Field(
            None,
            description="If set and checks pass, auto-commit with this message. "
            "Skips commit on failure.",
        ),
        push: bool = Field(
            False,
            description="Push to origin after auto-commit (only used with commit_message).",
        ),
    ) -> dict[str, Any]:
        """Lint, test, and optionally commit+push in one call.

        Chains:
        1. lint (full repo, auto-fix by default) — reports and fixes issues
        2. discover + run tests affected by changed_files (via import graph)
        3. (optional) if commit_message is set and all checks pass:
           stage changed_files → pre-commit hooks → commit → push → lean semantic diff

        Returns combined results with pass/fail verdict.
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        # Compute total phases for progress reporting
        total_phases = int(lint) + int(tests) * 3  # tests = discover + filter + run
        phase = 0

        result: dict[str, Any] = {"action": "checkpoint", "changed_files": changed_files}
        lint_status = "skipped"
        lint_diagnostics = 0
        test_passed = 0
        test_failed = 0
        test_status = "skipped"

        # --- Phase 1: Lint ---
        if lint:
            mode = "auto-fix" if autofix else "check-only"
            await ctx.report_progress(phase, total_phases, f"Linting ({mode})")
            lint_result = await app_ctx.lint_ops.check(
                paths=changed_files or None,  # scope to changeset; None = full repo fallback
                tools=None,
                categories=None,
                dry_run=not autofix,
            )
            lint_status = lint_result.status
            lint_diagnostics = lint_result.total_diagnostics
            phase += 1

            if lint_status == "clean":
                await ctx.info("Lint: clean")
            else:
                await ctx.info(
                    f"Lint: {lint_diagnostics} issue(s), "
                    f"{lint_result.total_files_modified} file(s) modified"
                )

            result["lint"] = {
                "status": lint_result.status,
                "total_diagnostics": lint_result.total_diagnostics,
                "total_files_modified": lint_result.total_files_modified,
                "duration_seconds": round(lint_result.duration_seconds, 2),
                "tools_run": [
                    {
                        "tool_id": t.tool_id,
                        "status": t.status,
                        "files_checked": t.files_checked,
                        "files_modified": t.files_modified,
                        "diagnostics": [
                            {
                                "path": d.path,
                                "line": d.line,
                                "column": d.column,
                                "severity": d.severity.value,
                                "code": d.code,
                                "message": d.message,
                            }
                            for d in t.diagnostics
                        ],
                    }
                    for t in lint_result.tools_run
                ],
            }

            if lint_result.agentic_hint:
                result["lint"]["agentic_hint"] = lint_result.agentic_hint

        # --- Phase 2: Tests ---
        if tests:
            await ctx.report_progress(phase, total_phases, "Discovering test targets")
            discover_result = await app_ctx.test_ops.discover(paths=None)
            all_targets = discover_result.targets or []
            phase += 1

            if all_targets and changed_files:
                await ctx.report_progress(
                    phase,
                    total_phases,
                    f"Filtering {len(all_targets)} targets by import graph",
                )
                graph_result = await app_ctx.coordinator.get_affected_test_targets(changed_files)
                affected_paths = set(graph_result.test_files)

                filtered = [
                    t
                    for t in all_targets
                    if _target_matches_affected_files(t, affected_paths, app_ctx.repo_root)
                ]
                phase += 1

                if not filtered:
                    test_status = "skipped"
                    await ctx.info("Tests: no affected targets — skipping")
                    result["tests"] = {
                        "status": "skipped",
                        "reason": "no affected tests found",
                        "confidence": graph_result.confidence.tier,
                    }
                else:
                    # Validate coverage params
                    if coverage and not coverage_dir:
                        test_status = "error"
                        result["tests"] = {
                            "status": "error",
                            "reason": "coverage=True requires coverage_dir",
                        }
                    else:
                        # --- Tiered execution: run direct tests first, then transitive ---
                        tiered_result = await _run_tiered_tests(
                            app_ctx=app_ctx,
                            ctx=ctx,
                            graph_result=graph_result,
                            filtered_targets=filtered,
                            repo_root=app_ctx.repo_root,
                            test_filter=test_filter,
                            coverage=coverage,
                            coverage_dir=coverage_dir,
                            phase=phase,
                            total_phases=total_phases,
                        )

                        result["tests"] = tiered_result["serialized"]
                        test_status = tiered_result["status"]
                        test_passed = tiered_result["passed"]
                        test_failed = tiered_result["failed"]

                        if test_failed > 0:
                            await ctx.warning(f"Tests: {test_passed} passed, {test_failed} FAILED")
                        elif test_passed > 0:
                            await ctx.info(f"Tests: {test_passed} passed")

                        # Track scoped test for pattern detection
                        session = app_ctx.session_manager.get_or_create(ctx.session_id)
                        session.pattern_detector.record(
                            tool_name="checkpoint",
                            category_override="test_scoped",
                        )
            else:
                test_status = "skipped"
                if not all_targets:
                    reason = "no test targets discovered"
                else:
                    reason = "changed_files is empty — nothing to match against"
                await ctx.info(f"Tests: skipped — {reason}")
                result["tests"] = {
                    "status": "skipped",
                    "reason": reason,
                }

        # --- Summary ---
        result["summary"] = _summarize_verify(
            lint_status, lint_diagnostics, test_passed, test_failed, test_status
        )

        has_lint_issues = lint_diagnostics > 0 and lint_status != "clean"
        has_test_failures = test_failed > 0
        has_test_error = test_status == "error"
        if has_lint_issues or has_test_failures or has_test_error:
            result["passed"] = False
            await ctx.report_progress(total_phases, total_phases, "Checkpoint FAILED")
            hints: list[str] = []
            if has_lint_issues:
                hints.append(f"Fix {lint_diagnostics} lint issues.")
            if has_test_failures:
                hints.append(f"Fix {test_failed} failing test(s).")
            if has_test_error:
                hints.append("Test phase errored — check tests section for details.")

            # Add tier execution transparency
            tier_log = (
                result.get("tests", {}).get("tier_execution", [])
                if isinstance(result.get("tests"), dict)
                else []
            )
            if tier_log:
                tier_parts: list[str] = []
                for entry in tier_log:
                    label = entry.get("label", "?")
                    tp = entry.get("passed", 0)
                    tf = entry.get("failed", 0)
                    tt = entry.get("targets", 0)
                    dur = entry.get("duration_seconds", 0)
                    tier_parts.append(
                        f"{label}: {tt} targets, {tp} passed, {tf} failed ({dur:.1f}s)"
                    )
                    if entry.get("stopped_reason"):
                        tier_parts.append(f"  → {entry['stopped_reason']}")
                hints.append(f"Tier execution: {'; '.join(tier_parts)}")

            hints.append(
                "STOP! You passed changed_files — lint and tests ran ONLY on "
                "code affected by YOUR changes. These failures are almost "
                "certainly YOUR fault. Do NOT dismiss them as 'pre-existing' "
                "or 'not related to my changes'. Errors in OTHER files are "
                "often transitive — caused by your changes breaking downstream "
                "dependents. These are still YOUR responsibility. "
                "Fix ALL issues before proceeding."
            )
            result["agentic_hint"] = " ".join(hints)
        else:
            result["passed"] = True
            await ctx.report_progress(total_phases, total_phases, "Checks passed")

            # --- Optional: Auto-commit ---
            if commit_message:
                _validate_commit_message(commit_message)
                repo_path = Path(app_ctx.git_ops.repo.workdir)

                await ctx.report_progress(total_phases, total_phases + 2, "Staging changes")
                if changed_files:
                    _validate_paths_exist(repo_path, changed_files)
                    app_ctx.git_ops.stage(changed_files)
                    staged_paths = list(changed_files)
                else:
                    staged_paths = app_ctx.git_ops.stage_all()

                await ctx.report_progress(
                    total_phases + 1, total_phases + 2, "Running pre-commit hooks"
                )
                _hook_result, failure = _run_hook_with_retry(
                    repo_path, staged_paths, app_ctx.git_ops.stage
                )
                if failure:
                    await ctx.warning("Pre-commit hooks failed — skipping commit")
                    result["commit"] = failure
                else:
                    sha = app_ctx.git_ops.commit(commit_message)
                    commit_result: dict[str, Any] = {
                        "oid": sha,
                        "short_oid": sha[:7],
                        "summary": _summarize_commit(sha, commit_message),
                    }
                    if _hook_result and not _hook_result.success:
                        commit_result["hook_warning"] = {
                            "code": "HOOK_AUTO_FIXED",
                            "auto_fixed_files": _hook_result.modified_files or [],
                        }
                    if push:
                        app_ctx.git_ops.push(remote="origin", force=False)
                        commit_result["pushed"] = "origin"
                        commit_result["summary"] += " → pushed to origin"
                    result["commit"] = commit_result
                    await ctx.report_progress(
                        total_phases + 2,
                        total_phases + 2,
                        f"Committed {sha[:7]}",
                    )

                    # --- Lean semantic diff for the new commit ---
                    try:
                        from codeplane.mcp.tools.diff import (
                            _result_to_dict,
                            _run_git_diff,
                        )

                        diff_result = _run_git_diff(
                            app_ctx, base="HEAD~1", target="HEAD", paths=None
                        )
                        minimal = _result_to_dict(diff_result, verbosity="minimal")
                        commit_result["semantic_diff"] = {
                            "summary": minimal.get("summary"),
                            "structural_changes": minimal.get("structural_changes", []),
                            "non_structural_changes": minimal.get("non_structural_changes", []),
                        }
                    except Exception:
                        log.debug("post-commit semantic diff skipped", exc_info=True)

                result["agentic_hint"] = (
                    "All checks passed and changes committed."
                    if "oid" in result.get("commit", {})
                    else "All checks passed but commit failed — see commit section."
                )
            else:
                result["agentic_hint"] = (
                    'All checks passed. Call checkpoint again with commit_message="..." '
                    "to commit your changes."
                )

        # --- Wrap with delivery envelope ---
        # Checkpoint results can be large (all lint diagnostics + test output).
        # Use the same envelope pattern as semantic_diff/map_repo so large
        # results go to disk with a compact inline summary.
        from codeplane.mcp.delivery import wrap_existing_response

        inline_summary = result.get("summary", "checkpoint complete")

        return wrap_existing_response(
            result,
            resource_kind="checkpoint",
            inline_summary=inline_summary,
        )
