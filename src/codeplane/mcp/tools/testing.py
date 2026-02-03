"""Testing MCP tools - test discovery and execution.

Split into verb-first tools:
- discover_test_targets: Find test targets
- run_test_targets: Execute tests
- get_test_run_status: Check run progress
- cancel_test_run: Abort a run
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext
    from codeplane.testing.models import TestResult


# =============================================================================
# Parameter Models
# =============================================================================


class DiscoverTestTargetsParams(BaseParams):
    """Parameters for discover_test_targets."""

    paths: list[str] | None = None


class RunTestTargetsParams(BaseParams):
    """Parameters for run_test_targets."""

    targets: list[str] | None = None
    pattern: str | None = None
    tags: list[str] | None = None
    failed_only: bool = False
    parallelism: int | None = None
    timeout_sec: int | None = None
    fail_fast: bool = False
    coverage: bool = False


class GetTestRunStatusParams(BaseParams):
    """Parameters for get_test_run_status."""

    run_id: str


class CancelTestRunParams(BaseParams):
    """Parameters for cancel_test_run."""

    run_id: str


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_discover(count: int) -> str:
    if count == 0:
        return "no test targets found"
    return f"{count} test targets discovered"


def _display_discover(count: int, targets: list[Any]) -> str:
    """Human-friendly message for discover action."""
    if count == 0:
        return "No test targets found in this repository."
    # Group by language
    by_lang: dict[str, int] = {}
    for t in targets:
        lang = t.language if hasattr(t, "language") else "unknown"
        by_lang[lang] = by_lang.get(lang, 0) + 1
    lang_parts = [f"{v} {k}" for k, v in sorted(by_lang.items(), key=lambda x: -x[1])]
    return f"Found {count} test targets: {', '.join(lang_parts)}."


def _display_run_start(result: TestResult) -> str:
    """Human-friendly message for run action."""
    if not result.run_status:
        return "Test run initiated."
    status = result.run_status
    total = status.progress.targets.total if status.progress else 0
    return f"Test run started: {total} targets. Run ID: {status.run_id}"


def _display_run_status(result: TestResult) -> str | None:
    """Human-friendly message for status - only on completion or failure."""
    if not result.run_status:
        return None
    status = result.run_status
    if status.status == "completed":
        p = status.progress
        if p and p.cases.failed > 0:
            return f"Tests completed: {p.cases.passed} passed, {p.cases.failed} FAILED in {status.duration_seconds:.1f}s."
        elif p:
            return f"Tests completed: {p.cases.passed} passed in {status.duration_seconds:.1f}s."
        return "Tests completed."
    elif status.status == "cancelled":
        return "Test run was cancelled."
    elif status.status == "failed":
        return "Test run failed to start."
    # Running - no display needed (avoid noise on polling)
    return None


def _summarize_run(result: TestResult) -> str:
    if not result.run_status:
        return "no run status"

    status = result.run_status
    if status.progress:
        p = status.progress
        parts: list[str] = [status.status]
        if p.cases.total > 0:
            parts.append(f"{p.cases.passed}/{p.cases.total} passed")
            if p.cases.failed:
                parts.append(f"{p.cases.failed} failed")
            if p.cases.skipped:
                parts.append(f"{p.cases.skipped} skipped")
        return ", ".join(parts)

    return status.status


def _serialize_test_result(result: TestResult, is_action: bool = False) -> dict[str, Any]:
    """Convert TestResult to dict.

    Args:
        result: The test result to serialize
        is_action: If True, include display_to_user for run start
    """
    output: dict[str, Any] = {
        "action": result.action,
        "summary": _summarize_run(result),
    }

    # Add display_to_user for actions and terminal states
    if is_action:
        output["display_to_user"] = _display_run_start(result)
    else:
        display = _display_run_status(result)
        if display:
            output["display_to_user"] = display

    if result.run_status:
        status = result.run_status
        # Compute poll hint based on current progress
        poll_hint = status.compute_poll_hint()

        output["run_status"] = {
            "run_id": status.run_id,
            "status": status.status,
            "duration_seconds": status.duration_seconds,
            "artifact_dir": status.artifact_dir,
            "poll_after_seconds": poll_hint,
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

    if result.agentic_hint:
        output["agentic_hint"] = result.agentic_hint

    return output


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register(
    "discover_test_targets",
    "Find test targets in the repository. Returns testable files/directories with runner info.",
    DiscoverTestTargetsParams,
)
async def discover_test_targets(
    ctx: AppContext, params: DiscoverTestTargetsParams
) -> dict[str, Any]:
    """Discover test targets in the repository."""
    result = await ctx.test_ops.discover(paths=params.paths)
    targets = result.targets or []
    output: dict[str, Any] = {
        "action": result.action,
        "targets": [
            {
                "target_id": t.target_id,
                "selector": t.selector,
                "kind": t.kind,
                "language": t.language,
                "runner_pack_id": t.runner_pack_id,
                "workspace_root": t.workspace_root,
                "estimated_cost": t.estimated_cost,
                "test_count": t.test_count,
                "path": t.path,
                "runner": t.runner,
            }
            for t in targets
        ],
        "summary": _summarize_discover(len(targets)),
        "display_to_user": _display_discover(len(targets), targets),
    }
    if result.agentic_hint:
        output["agentic_hint"] = result.agentic_hint
    return output


@registry.register(
    "run_test_targets",
    "Execute tests. Pass target_ids from discover, or use pattern/tags to filter.",
    RunTestTargetsParams,
)
async def run_test_targets(ctx: AppContext, params: RunTestTargetsParams) -> dict[str, Any]:
    """Run tests on specified targets."""
    result = await ctx.test_ops.run(
        targets=params.targets,
        pattern=params.pattern,
        tags=params.tags,
        failed_only=params.failed_only,
        parallelism=params.parallelism,
        timeout_sec=params.timeout_sec,
        fail_fast=params.fail_fast,
        coverage=params.coverage,
    )
    return _serialize_test_result(result, is_action=True)


@registry.register(
    "get_test_run_status",
    "Check progress of a running test. Returns pass/fail counts and any failures.",
    GetTestRunStatusParams,
)
async def get_test_run_status(ctx: AppContext, params: GetTestRunStatusParams) -> dict[str, Any]:
    """Get status of a test run."""
    result = await ctx.test_ops.status(params.run_id)
    return _serialize_test_result(result, is_action=False)


@registry.register(
    "cancel_test_run",
    "Abort a running test execution.",
    CancelTestRunParams,
)
async def cancel_test_run(ctx: AppContext, params: CancelTestRunParams) -> dict[str, Any]:
    """Cancel a running test."""
    result = await ctx.test_ops.cancel(params.run_id)
    return _serialize_test_result(result, is_action=True)
