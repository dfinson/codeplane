"""Verify MCP tool — single "did I break anything?" endpoint.

Chains:  lint (auto-fix by default) → affected tests → combined report
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext
    from codeplane.testing.models import TestResult


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
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register verify tool with FastMCP server."""

    @mcp.tool(
        title="Verify: lint + affected tests",
        annotations=ToolAnnotations(
            title="Verify: lint + affected tests",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def verify(
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
    ) -> dict[str, Any]:
        """Run lint + affected tests in one call. The "did I break anything?" check.

        Chains:
        1. lint (full repo, auto-fix by default) — reports and fixes issues
        2. discover + run tests affected by changed_files (via import graph)

        Returns combined results with pass/fail verdict.
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        # Compute total phases for progress reporting
        total_phases = int(lint) + int(tests) * 3  # tests = discover + filter + run
        phase = 0

        result: dict[str, Any] = {"action": "verify", "changed_files": changed_files}
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
                paths=None,  # full repo
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
                effective_targets = [t.target_id for t in filtered]
                phase += 1

                if not effective_targets:
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
                        await ctx.report_progress(
                            phase,
                            total_phases,
                            f"Running {len(effective_targets)} affected test target(s)",
                        )
                        test_result = await app_ctx.test_ops.run(
                            targets=effective_targets,
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

                        serialized = _serialize_test_result(test_result)
                        result["tests"] = serialized

                        if test_result.run_status:
                            test_status = test_result.run_status.status
                            if test_result.run_status.progress:
                                test_passed = test_result.run_status.progress.cases.passed
                                test_failed = test_result.run_status.progress.cases.failed

                        if test_failed > 0:
                            await ctx.warning(f"Tests: {test_passed} passed, {test_failed} FAILED")
                        elif test_passed > 0:
                            await ctx.info(f"Tests: {test_passed} passed")

                        # Track scoped test for pattern detection
                        session = app_ctx.session_manager.get_or_create(ctx.session_id)
                        session.pattern_detector.record(
                            tool_name="verify",
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
            await ctx.report_progress(total_phases, total_phases, "Verify FAILED")
            hints: list[str] = []
            if has_lint_issues:
                hints.append(f"Fix {lint_diagnostics} lint issues.")
            if has_test_failures:
                hints.append(f"Fix {test_failed} failing test(s).")
            if has_test_error:
                hints.append("Test phase errored — check tests section for details.")
            result["agentic_hint"] = " ".join(hints)
        else:
            result["passed"] = True
            await ctx.report_progress(total_phases, total_phases, "Verify passed")

        return result
