"""Testing MCP tools - testing.* handlers."""

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


class TestDiscoverParams(BaseParams):
    """Parameters for testing.discover."""

    paths: list[str] | None = None


class TestRunParams(BaseParams):
    """Parameters for testing.run."""

    targets: list[str] | None = None
    pattern: str | None = None
    tags: list[str] | None = None
    failed_only: bool = False
    parallelism: int | None = None
    timeout_sec: int | None = None
    fail_fast: bool = False
    coverage: bool = False


class TestStatusParams(BaseParams):
    """Parameters for testing.status."""

    run_id: str


class TestCancelParams(BaseParams):
    """Parameters for testing.cancel."""

    run_id: str


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_discover(count: int) -> str:
    """Generate summary for testing.discover."""
    if count == 0:
        return "no test targets found"
    return f"{count} test targets discovered"


def _summarize_run(result: TestResult) -> str:
    """Generate summary for testing.run/status."""
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


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register(
    "testing.discover", "Discover test targets in the repository", TestDiscoverParams
)
async def testing_discover(ctx: AppContext, params: TestDiscoverParams) -> dict[str, Any]:
    """Discover tests."""
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
                # Legacy compatibility
                "path": t.path,
                "runner": t.runner,
            }
            for t in targets
        ],
        "summary": _summarize_discover(len(targets)),
    }

    # Include agentic hint if present (for edge cases like no runners detected)
    if result.agentic_hint:
        output["agentic_hint"] = result.agentic_hint

    return output


@registry.register("testing.run", "Run tests", TestRunParams)
async def testing_run(ctx: AppContext, params: TestRunParams) -> dict[str, Any]:
    """Run tests."""
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
    return _serialize_test_result(result)


@registry.register("testing.status", "Get status of a test run", TestStatusParams)
async def testing_status(ctx: AppContext, params: TestStatusParams) -> dict[str, Any]:
    """Get test run status."""
    result = await ctx.test_ops.status(params.run_id)
    return _serialize_test_result(result)


@registry.register("testing.cancel", "Cancel a running test", TestCancelParams)
async def testing_cancel(ctx: AppContext, params: TestCancelParams) -> dict[str, Any]:
    """Cancel test run."""
    result = await ctx.test_ops.cancel(params.run_id)
    return _serialize_test_result(result)


def _serialize_test_result(result: TestResult) -> dict[str, Any]:
    """Convert TestResult to dict."""
    output: dict[str, Any] = {
        "action": result.action,
        "summary": _summarize_run(result),
    }

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
                # New structured progress
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
                # Legacy flat fields for compatibility
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

    return output
