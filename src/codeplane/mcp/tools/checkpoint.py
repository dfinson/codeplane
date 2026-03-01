"""Checkpoint MCP tool — lint, test, commit in one call.

Chains:  lint (auto-fix) → affected tests → stage → hooks → commit → push → semantic diff
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

from codeplane.git._internal.hooks import run_hook
from codeplane.git.errors import EmptyCommitMessageError, PathsNotFoundError

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext
    from codeplane.testing.models import TestResult

log = structlog.get_logger(__name__)


# =============================================================================
# Test Debt Detection
# =============================================================================


def _detect_test_debt(
    changed_files: list[str],
    repo_root: Path,
) -> dict[str, Any] | None:
    """Detect source files changed without corresponding test updates.

    Scans ``changed_files`` for source files, uses convention-based
    pairing to find their expected test counterparts, and reports any
    that exist on disk but were NOT included in ``changed_files``.

    Returns a dict with ``source_files``, ``missing_test_updates``, and
    ``hint`` if debt is found, otherwise ``None``.
    """
    from codeplane.core.languages import find_test_pairs, is_test_file

    changed_set = set(changed_files)

    # Separate source files from test files
    source_files = [f for f in changed_files if not is_test_file(f)]
    test_files_changed = [f for f in changed_files if is_test_file(f)]

    if not source_files:
        return None

    missing: list[dict[str, str]] = []
    for src in source_files:
        pairs = find_test_pairs(src)
        # Find test files that exist on disk but weren't in changed_files
        for tp in pairs:
            if tp in changed_set:
                break  # At least one test counterpart was updated — no debt
        else:
            # No test counterpart was in changed_files — check if any exist
            existing_tests = [tp for tp in pairs if (repo_root / tp).exists()]
            if existing_tests:
                missing.append(
                    {
                        "source": src,
                        "test_file": existing_tests[0],
                    }
                )

    if not missing:
        return None

    sources_str = ", ".join(m["source"].split("/")[-1] for m in missing[:5])
    tests_str = ", ".join(m["test_file"] for m in missing[:5])
    truncated = " (and more)" if len(missing) > 5 else ""

    hint = (
        f"TEST DEBT: {len(missing)} source file(s) changed without test updates"
        f"{truncated}. Source: {sources_str}. "
        f"Test counterpart(s): {tests_str}. "
        "Consider updating tests to cover your changes."
    )

    return {
        "source_files_changed": len(source_files),
        "test_files_changed": len(test_files_changed),
        "missing_test_updates": [
            {"source": m["source"], "test_file": m["test_file"]} for m in missing
        ],
        "hint": hint,
    }


# =============================================================================
# Commit Helpers
# =============================================================================


def _validate_commit_message(message: str) -> None:
    """Validate commit message is not empty or whitespace-only."""
    if not message or not message.strip():
        raise EmptyCommitMessageError()


def _validate_paths_exist(
    repo_path: Path, paths: list[str], *, tracked_files: set[str] | None = None
) -> None:
    """Validate paths are known to the repository.

    A path is valid if it exists on disk OR is tracked by git (i.e. a deletion).
    Only truly unknown paths (typos, never-existed) raise PathsNotFoundError.
    """
    if not paths:
        return

    tracked = tracked_files or set()
    unknown: list[str] = []
    for p in paths:
        if (repo_path / p).exists():
            continue  # exists on disk — valid
        if p in tracked:
            continue  # tracked deletion — valid
        unknown.append(p)

    if unknown:
        raise PathsNotFoundError(unknown)


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


def _build_coverage_text(
    coverage_artifacts: list[dict[str, str]],
    filter_paths: set[str] | None = None,
) -> tuple[str, str | None]:
    """Build compact coverage summary + cache detailed per-file report.

    Returns:
        (inline_summary, coverage_hint) where:
        - inline_summary: compact one-liner (e.g. ``coverage: 85% (170/200 lines)``)
        - coverage_hint: agentic hint with jq extraction commands for the cached
          detail file, or None if no detail was produced.
    """
    from codeplane.testing.coverage import (
        CoverageParseError,
        CoverageReport,
        build_coverage_detail,
        merge,
        parse_artifact,
    )

    if not coverage_artifacts:
        return "coverage: no data", None

    # Dedupe by path
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for c in coverage_artifacts:
        p = c.get("path", "")
        if p and p not in seen:
            seen.add(p)
            deduped.append(c)

    # Parse all
    reports: list[CoverageReport] = []
    for cov in deduped:
        path_str = cov.get("path", "")
        fmt = cov.get("format")
        if not path_str:
            continue
        try:
            report = parse_artifact(
                Path(path_str),
                format_id=fmt if fmt and fmt != "unknown" else None,
            )
            reports.append(report)
        except CoverageParseError:
            pass
        except Exception as e:
            log.debug("Coverage parse failed", path=path_str, error=str(e))

    if not reports:
        return "coverage: parse failed", None

    merged = merge(*reports) if len(reports) > 1 else reports[0]
    inline, detail = build_coverage_detail(merged, filter_paths=filter_paths)

    if detail is None:
        return inline, None

    # Cache the detailed report in sidecar cache
    import json

    from codeplane.mcp.sidecar_cache import cache_put

    byte_size = len(json.dumps(detail, indent=2, default=str).encode("utf-8"))
    cache_id = cache_put("coverage", "coverage", detail)

    n_files = len(detail.get("files", []))
    files_with_gaps = sum(1 for f in detail.get("files", []) if "uncovered_ranges" in f)

    from codeplane.mcp.delivery import _cpl_cmd

    hint_parts = [
        f"Detailed coverage ({byte_size:,} bytes, {n_files} files) cached as {cache_id}",
        "Retrieve via terminal:",
        f"  {_cpl_cmd(cache_id, 'summary')}",
        f"  {_cpl_cmd(cache_id, 'files')}",
    ]
    if files_with_gaps > 5:
        hint_parts.append(f"  {_cpl_cmd(cache_id, 'files')}  # filter for files with low coverage")
    hint_parts.append(
        f"  {_cpl_cmd(cache_id, 'files.0')}  # replace 0 with the file index you need"
    )

    coverage_hint = "\n".join(hint_parts)
    return inline, coverage_hint


def _serialize_test_result(
    result: "TestResult",
    coverage_filter_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Convert TestResult to compact serializable dict.

    Outer structure is flat JSON for parseability.  Inner details
    (failures, diagnostics) are compressed text strings to minimise
    token count.

    Args:
        result: TestResult to serialize.
        coverage_filter_paths: If provided, only include coverage for these
            source files (excluding test files).
    """
    output: dict[str, Any] = {
        "summary": _summarize_run(result),
    }

    if result.run_status:
        status = result.run_status
        output["status"] = status.status
        output["duration"] = round(status.duration_seconds, 2)

        if status.progress:
            p = status.progress
            output["passed"] = p.cases.passed
            output["failed"] = p.cases.failed
            output["skipped"] = p.cases.skipped
            output["targets"] = p.targets.total

        if status.failures:
            lines: list[str] = []
            for f in status.failures:
                loc = f"{f.path}:{f.line}" if f.line else f.path
                lines.append(f"{loc} {f.name}: {f.message}")
                if f.traceback:
                    # First 3 meaningful lines of traceback
                    tb_lines = [ln for ln in f.traceback.strip().splitlines() if ln.strip()][:3]
                    lines.extend(f"  {ln.strip()}" for ln in tb_lines)
            output["failures"] = "\n".join(lines)

        if status.diagnostics:
            diag_lines: list[str] = []
            for d in status.diagnostics:
                exit_info = f" (exit {d.exit_code})" if d.exit_code is not None else ""
                detail = d.error_detail or "no detail"
                diag_lines.append(f"{d.target_id} [{d.error_type}]: {detail}{exit_info}")
                if d.suggested_action:
                    diag_lines.append(f"  → {d.suggested_action}")
            output["diagnostics"] = "\n".join(diag_lines)

        if status.coverage:
            inline_cov, cov_hint = _build_coverage_text(
                status.coverage,
                filter_paths=coverage_filter_paths,
            )
            output["coverage"] = inline_cov
            if cov_hint:
                output["coverage_hint"] = cov_hint

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


# Default: run only direct tests (hop 0) for fast iteration.
# When committing, escalate to _COMMIT_MAX_TEST_HOPS for thorough validation.
_DEFAULT_MAX_TEST_HOPS = 0
_COMMIT_MAX_TEST_HOPS = 1


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
    coverage_filter_paths: set[str] | None,
    max_test_hops: int,
    phase: int,
    total_phases: int,
) -> dict[str, Any]:
    """Execute tests in hop tiers: direct tests first, then transitive.

    If direct tests (hop 0) fail, transitive tests (hop 1+) are skipped
    on the assumption that direct-import failures will cascade.

    Within each tier, low-cost targets are batched into fewer subprocess
    invocations to reduce startup overhead.

    Args:
        coverage_filter_paths: Source files to include in coverage report
            (changed files excluding tests).
        max_test_hops: Maximum hop tier to execute.  0 = direct only,
            1 = direct + first transitive, etc.  Use a large number
            (e.g. 99) for unlimited.

    Returns a dict with keys: serialized, status, passed, failed, tier_log.
    """
    hop_targets = _assign_target_hops(filtered_targets, graph_result, repo_root)
    sorted_hops = sorted(hop_targets.keys())
    max_hop = sorted_hops[-1] if sorted_hops else 0

    # Limit hops to the requested depth
    executable_hops = [h for h in sorted_hops if h <= max_test_hops]
    skipped_hops = [h for h in sorted_hops if h > max_test_hops]

    # Accumulate results across tiers
    total_passed = 0
    total_failed = 0
    all_test_results: list[Any] = []
    all_batch_results: list[Any] = []
    all_batch_coverage: list[Any] = []  # Coverage artifacts from batched runs
    all_failure_lines: list[str] = []  # Failure details across all hops
    tier_log: list[dict[str, Any]] = []
    final_status = "completed"
    stopped_at_hop: int | None = None

    for hop in executable_hops:
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
        batched_into = len(batch_groups)

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

        hop_batch_results: list[tuple[Any, Any]] = []
        if batch_groups:
            # coverage_dir is passed through to batch execution
            cov_dir_path = Path(coverage_dir) if coverage_dir else None

            async def run_batch(
                group: list[Any], cov_path: Path | None = cov_dir_path
            ) -> tuple[Any, Any]:
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
                    coverage_dir=cov_path,
                )

            batch_tasks = [asyncio.create_task(run_batch(g)) for g in batch_groups]
            hop_batch_results = await asyncio.gather(*batch_tasks)
            # Separate results and coverage artifacts
            for result, cov_artifact in hop_batch_results:
                all_batch_results.append(result)
                if cov_artifact:
                    all_batch_coverage.append(cov_artifact)

        # Collect per-hop batch results for tier counting (avoid cross-hop bleed)
        hop_batch_parsed: list[Any] = (
            [result for result, _ in hop_batch_results] if batch_groups else []
        )

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

            # Collect failure details from solo result
            if rs.failures:
                for f in rs.failures:
                    loc = f"{f.path}:{f.line}" if f.line else f.path
                    all_failure_lines.append(f"{loc} {f.name}: {f.message}")
                    if f.traceback:
                        tb_lines = [ln for ln in f.traceback.strip().splitlines() if ln.strip()][:3]
                        all_failure_lines.extend(f"  {ln.strip()}" for ln in tb_lines)

        for br in hop_batch_parsed:
            tier_passed += br.passed
            tier_failed += br.failed
            tier_total += br.total
            tier_duration += br.duration_seconds

            # Collect failure details from batch results
            if br.tests:
                for tc in br.tests:
                    if tc.status in ("failed", "error"):
                        loc = (
                            f"{tc.file_path}:{tc.line_number}"
                            if tc.file_path and tc.line_number
                            else tc.file_path or tc.classname or "unknown"
                        )
                        msg = tc.message or "no message"
                        all_failure_lines.append(f"{loc} {tc.name}: {msg}")
                        if tc.traceback:
                            tb_lines = [
                                ln for ln in tc.traceback.strip().splitlines() if ln.strip()
                            ][:3]
                            all_failure_lines.extend(f"  {ln.strip()}" for ln in tb_lines)

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
            remaining_hops = [h for h in executable_hops if h > hop] + skipped_hops
            stopped_at_hop = hop

            skipped_info = ", ".join(
                f"hop {h} ({len(hop_targets[h])} targets)" for h in remaining_hops
            )
            tier_entry["stopped_reason"] = (
                f"Failures in {tier_label} — skipped transitive tiers: {skipped_info}"
            )
            break

    # Build combined serialized result
    combined: dict[str, Any] = {}
    if all_test_results and all_test_results[0].run_status:
        # Merge batch coverage artifacts into the solo result's coverage
        if all_batch_coverage:
            existing_cov = all_test_results[0].run_status.coverage or []
            for cov in all_batch_coverage:
                existing_cov.append(
                    {"format": cov.format, "path": str(cov.path), "pack_id": cov.pack_id}
                )
            all_test_results[0].run_status.coverage = existing_cov

        combined = _serialize_test_result(
            all_test_results[0],
            coverage_filter_paths=coverage_filter_paths,
        )

        # Overlay batch results into flat counters
        for br in all_batch_results:
            combined["passed"] = combined.get("passed", 0) + br.passed
            combined["failed"] = combined.get("failed", 0) + br.failed
            combined["skipped"] = combined.get("skipped", 0) + br.skipped
    elif all_batch_results:
        # Only batched targets, no solo - build coverage from batch artifacts
        combined = {
            "status": "completed",
            "passed": sum(br.passed for br in all_batch_results),
            "failed": sum(br.failed for br in all_batch_results),
            "skipped": sum(br.skipped for br in all_batch_results),
        }
        # Add coverage from batch runs
        if all_batch_coverage:
            batch_cov_dicts = [
                {"format": c.format, "path": str(c.path), "pack_id": c.pack_id}
                for c in all_batch_coverage
            ]
            inline_cov, cov_hint = _build_coverage_text(
                batch_cov_dicts,
                filter_paths=coverage_filter_paths,
            )
            combined["coverage"] = inline_cov
            if cov_hint:
                combined["coverage_hint"] = cov_hint

    # Overlay accumulated failure details from ALL hops and batch results
    if all_failure_lines:
        combined["failures"] = "\n".join(all_failure_lines)

    # Compact tier execution as text string
    tier_parts: list[str] = []
    for entry in tier_log:
        label = entry["label"]
        t_count = entry["targets"]
        p = entry["passed"]
        f = entry["failed"]
        dur = entry["duration_seconds"]
        tier_parts.append(f"{label}: {t_count}t {p}p/{f}f {dur}s")
        if entry.get("stopped_reason"):
            # Append skip info
            tier_parts.append("→ STOPPED")
    combined["tiers"] = " | ".join(tier_parts)

    # Log skipped hops due to hop limit
    if skipped_hops and stopped_at_hop is None:
        skipped_targets = sum(len(hop_targets[h]) for h in skipped_hops)
        skipped_info = ", ".join(f"hop {h} ({len(hop_targets[h])} targets)" for h in skipped_hops)
        tier_log.append(
            {
                "hop": skipped_hops[0],
                "label": "skipped (hop limit)",
                "targets": skipped_targets,
                "batched": 0,
                "batch_groups": 0,
                "passed": 0,
                "failed": 0,
                "total": 0,
                "duration_seconds": 0.0,
                "stopped_reason": f"max_test_hops={max_test_hops} \u2014 skipped: {skipped_info}",
            }
        )

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
        hop_note = f", {len(executable_hops)} tier(s)" if executable_hops else ""
        skip_note = f", {len(skipped_hops)} skipped" if skipped_hops else ""
        combined["summary"] = (
            f"{total_passed} passed"
            + (f", {total_failed} failed" if total_failed > 0 else "")
            + f" ({total_duration:.1f}s{hop_note}{skip_note})"
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
        annotations={
            "title": "Checkpoint: lint, test, commit, push",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
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
        max_test_hops: int | None = Field(
            None,
            description="Max import-graph hop depth for test selection. "
            "0 = direct tests only, 1 = direct + 1 transitive, etc. "
            "Default: 0 (direct only) for fast iteration; auto-escalates "
            "to 2 hops when commit_message is set.",
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
        gate_token: str | None = Field(
            None,
            description="Gate confirmation token from a previous gate block.",
        ),
        gate_reason: str | None = Field(
            None,
            description="Justification for passing the gate (min chars per gate spec).",
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
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        # ── Read-only checkpoint: clean-tree verification only ──
        if getattr(session, "read_only", None) is True:
            try:
                wt_status = app_ctx.git_ops.status()
                dirty_files = [p for p, flags in wt_status.items() if flags != 0]
            except Exception:  # noqa: BLE001
                dirty_files = []
            clean = len(dirty_files) == 0
            ro_result: dict[str, Any] = {
                "action": "checkpoint",
                "read_only": True,
                "clean_tree": clean,
                "passed": clean,
            }
            if not clean:
                ro_result["dirty_files"] = dirty_files[:20]
                ro_result["agentic_hint"] = (
                    f"Read-only checkpoint found {len(dirty_files)} "
                    "uncommitted file(s). This is unexpected for a "
                    "read-only session. Investigate or call "
                    "recon(read_only=False) to switch to a read-write session."
                )
            else:
                ro_result["agentic_hint"] = (
                    "Read-only session complete — working tree is clean. No mutations were made."
                )
            from codeplane.mcp.delivery import wrap_response

            return wrap_response(
                ro_result,
                resource_kind="checkpoint",
            )

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

            # Build compact lint result: structured outer keys, text for issues
            issue_lines: list[str] = []
            for t in lint_result.tools_run:
                for d in t.diagnostics:
                    # Strip repo root prefix for brevity
                    rel_path = d.path
                    if "/" in rel_path:
                        # Keep just the relative-looking portion
                        for prefix in (str(app_ctx.repo_root) + "/",):
                            if rel_path.startswith(prefix):
                                rel_path = rel_path[len(prefix) :]
                                break
                    sev = d.severity.value[0].upper()  # W/E/I
                    issue_lines.append(f"{rel_path}:{d.line}:{d.column} {sev} {d.code} {d.message}")

            result["lint"] = {
                "status": lint_result.status,
                "diagnostics": lint_result.total_diagnostics,
                "fixed_files": lint_result.total_files_modified,
                "duration": round(lint_result.duration_seconds, 2),
            }
            if issue_lines:
                result["lint"]["issues"] = issue_lines

            if lint_result.agentic_hint:
                result["lint"]["agentic_hint"] = lint_result.agentic_hint

            # Fail-fast: skip tests if lint has issues
            if lint_diagnostics > 0 and lint_status != "clean":
                test_status = "skipped"
                result["tests"] = {
                    "status": "skipped",
                    "reason": "lint failed — fix lint issues first",
                }
                result["summary"] = _summarize_verify(
                    lint_status, lint_diagnostics, test_passed, test_failed, test_status
                )
                result["passed"] = False
                result["agentic_hint"] = (
                    f"STOP! Lint found {lint_diagnostics} issues. Tests were skipped. "
                    "Fix ALL lint issues before proceeding. "
                    "These failures are almost certainly YOUR fault — you passed changed_files "
                    "so lint ran ONLY on code affected by YOUR changes."
                )
                return result

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
                    # Auto-derive coverage_dir - coverage is always enabled
                    import uuid

                    from codeplane.core.languages import is_test_file

                    coverage_dir = str(
                        app_ctx.repo_root
                        / ".codeplane"
                        / "artifacts"
                        / "coverage"
                        / uuid.uuid4().hex[:8]
                    )
                    Path(coverage_dir).mkdir(parents=True, exist_ok=True)

                    # Filter changed_files to source files only (exclude tests)
                    coverage_filter_paths = {f for f in changed_files if not is_test_file(f)}

                    # Resolve effective max_test_hops:
                    # - Explicit override from agent wins
                    # - Auto-escalate to _COMMIT_MAX_TEST_HOPS when committing
                    # - Default: _DEFAULT_MAX_TEST_HOPS (0) for fast iteration
                    if max_test_hops is not None:
                        effective_hops = max_test_hops
                    elif commit_message:
                        effective_hops = _COMMIT_MAX_TEST_HOPS
                    else:
                        effective_hops = _DEFAULT_MAX_TEST_HOPS

                    # --- Tiered execution: run direct tests first, then transitive ---
                    tiered_result = await _run_tiered_tests(
                        app_ctx=app_ctx,
                        ctx=ctx,
                        graph_result=graph_result,
                        filtered_targets=filtered,
                        repo_root=app_ctx.repo_root,
                        test_filter=test_filter,
                        coverage=True,
                        coverage_dir=coverage_dir,
                        # Always pass filter_paths - empty set = no source files changed
                        coverage_filter_paths=coverage_filter_paths,
                        max_test_hops=effective_hops,
                        phase=phase,
                        total_phases=total_phases,
                    )
                    result["tests"] = tiered_result["serialized"]
                    test_status = tiered_result["status"]
                    test_passed = tiered_result["passed"]
                    test_failed = tiered_result["failed"]

                    # Hoist coverage_hint to top-level for agent visibility
                    serialized = tiered_result["serialized"]
                    if isinstance(serialized, dict) and serialized.get("coverage_hint"):
                        result["coverage_hint"] = serialized.pop("coverage_hint")

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
            tiers_text = (
                result.get("tests", {}).get("tiers", "")
                if isinstance(result.get("tests"), dict)
                else ""
            )
            if tiers_text:
                hints.append(f"Tiers: {tiers_text}")

            hints.append(
                "STOP! You passed changed_files — lint and tests ran ONLY on "
                "code affected by YOUR changes. These failures are almost "
                "certainly YOUR fault. Do NOT dismiss them as 'pre-existing' "
                "or 'not related to my changes'. Errors in OTHER files are "
                "often transitive — caused by your changes breaking downstream "
                "dependents. These are still YOUR responsibility. "
                "Fix ALL issues before proceeding."
            )

            # ── Gap 5: Regenerate resolve cache on failure ──
            # Re-read changed files from disk so the agent has current
            # content + sha256 to make corrections without extra resolves.
            try:
                import hashlib

                from codeplane.mcp.sidecar_cache import cache_put

                chk_session = app_ctx.session_manager.get_or_create(ctx.session_id)
                repo_root = app_ctx.coordinator.repo_root
                refreshed: list[dict[str, Any]] = []
                for cf in changed_files:
                    fp = repo_root / cf
                    if not fp.exists():
                        continue
                    try:
                        raw = fp.read_bytes()
                        if b"\x00" in raw[:512]:
                            continue  # skip binary
                        content_str = raw.decode("utf-8", errors="replace")
                        sha = hashlib.sha256(raw).hexdigest()
                        refreshed.append(
                            {
                                "path": cf,
                                "content": content_str,
                                "line_count": content_str.count("\n") + 1,
                                "file_sha256": sha,
                            }
                        )
                    except Exception:  # noqa: BLE001
                        continue

                if refreshed:
                    cache_payload = {
                        "resolved": refreshed,
                        "agentic_hint": (
                            "Checkpoint FAILED. Resolve cache refreshed "
                            f"with {len(refreshed)} file(s) at current "
                            "disk state. Your plan has been cleared.\n\n"
                            "RECOVERY STEPS:\n"
                            "1. Review lint/test errors above\n"
                            "2. Call refactor_plan(edit_targets=[...], "
                            'description="fix: ...") to declare corrections\n'
                            "3. Call refactor_edit(plan_id=..., edits=[...]) "
                            "to apply fixes\n"
                            "4. Call checkpoint(changed_files=[...], "
                            'commit_message="...") again\n\n'
                            "Use the cache commands below to access "
                            "current file content without re-resolving."
                        ),
                    }
                    cache_id = cache_put(ctx.session_id, "resolve_refresh", cache_payload)
                    result["resolve_cache_id"] = cache_id

                    # Update resolved_files in session with refreshed sha256s
                    if "resolved_files" not in chk_session.counters:
                        chk_session.counters["resolved_files"] = {}  # type: ignore[assignment]
                    resolved_files: dict[str, str] = chk_session.counters["resolved_files"]  # type: ignore[assignment]
                    for r in refreshed:
                        resolved_files[r["path"]] = r["file_sha256"]

                    # Build cplcache hint for the refreshed cache
                    from codeplane.mcp.delivery import _cpl_cmd

                    hints.append(
                        f"\n\nREFRESHED RESOLVE CACHE (cache_id={cache_id}):\n"
                        f"  {_cpl_cmd(cache_id, 'resolved')}\n"
                        "Use this to read current file content for corrections."
                    )
            except Exception:  # noqa: BLE001
                log.debug("checkpoint_failure_cache_failed", exc_info=True)
            finally:
                # ALWAYS clear mutation state on checkpoint — prevents deadlock
                # where plan budget is exhausted but lint fails, leaving
                # no way to create a new plan or fix the issue.
                try:
                    chk_session = app_ctx.session_manager.get_or_create(ctx.session_id)
                    chk_session.mutation_ctx.clear()
                    app_ctx.refactor_ops.clear_pending()
                except Exception:  # noqa: BLE001
                    pass

            result["agentic_hint"] = " ".join(hints)
        else:
            result["passed"] = True
            await ctx.report_progress(total_phases, total_phases, "Checks passed")

            # ── Reset mutation state and batch counter ──
            try:
                chk_session = app_ctx.session_manager.get_or_create(ctx.session_id)
                chk_session.mutation_ctx.clear()
                chk_session.resolve_batch_count = 0
                app_ctx.refactor_ops.clear_pending()
            except Exception:  # noqa: BLE001
                pass

            # --- Optional: Auto-commit ---
            if commit_message:
                _validate_commit_message(commit_message)
                repo_path = Path(app_ctx.git_ops.repo.workdir)

                await ctx.report_progress(total_phases, total_phases + 2, "Staging changes")
                if changed_files:
                    tracked = set(app_ctx.git_ops.tracked_files())
                    _validate_paths_exist(repo_path, changed_files, tracked_files=tracked)
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
                        "summary": _summarize_commit(sha, commit_message),
                    }
                    if _hook_result and not _hook_result.success:
                        fixed = _hook_result.modified_files or []
                        commit_result["hook_warning"] = f"HOOK_AUTO_FIXED: {', '.join(fixed)}"
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

                    # --- Lean semantic diff as compact text ---
                    try:
                        from codeplane.mcp.tools.diff import (
                            _result_to_dict,
                            _run_git_diff,
                        )

                        diff_result = _run_git_diff(
                            app_ctx, base="HEAD~1", target="HEAD", paths=None
                        )
                        minimal = _result_to_dict(diff_result, verbosity="minimal")
                        diff_summary = minimal.get("summary", "")
                        changes = minimal.get("structural_changes", [])
                        if changes:
                            change_lines = [
                                f"{c.get('change', '?')} {c.get('kind', '?')} "
                                f"{c.get('name', '?')} ({c.get('path', '?').split('/')[-1]})"
                                for c in changes[:15]
                            ]
                            commit_result["diff"] = f"{diff_summary}: " + ", ".join(change_lines)
                        elif diff_summary:
                            commit_result["diff"] = diff_summary
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

            # ── Test debt detection ──
            # After checks pass, detect source files changed without
            # corresponding test file updates.  This is a soft signal —
            # it doesn't block the commit but nudges the agent toward
            # test co-evolution.
            if changed_files:
                try:
                    debt = _detect_test_debt(changed_files, app_ctx.repo_root)
                    if debt:
                        result["test_debt"] = debt
                        # Append to agentic_hint so agents always see it
                        existing = result.get("agentic_hint", "")
                        result["agentic_hint"] = f"{existing}\n\n{debt['hint']}"
                except Exception:  # noqa: BLE001
                    log.debug("test_debt_detection_failed", exc_info=True)

        # --- Wrap with delivery envelope ---
        # Checkpoint results can be large (all lint diagnostics + test output).
        # Use the same envelope pattern as semantic_diff/map_repo so large
        # results go to disk with a compact inline summary.
        from codeplane.mcp.delivery import wrap_response

        return wrap_response(
            result,
            resource_kind="checkpoint",
        )
