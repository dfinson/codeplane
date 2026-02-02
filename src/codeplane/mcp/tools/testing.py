"""Testing MCP tools - test_* handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext
    from codeplane.testing.ops import TestResult


# =============================================================================
# Parameter Models
# =============================================================================


class TestDiscoverParams(BaseParams):
    """Parameters for test_discover."""

    paths: list[str] | None = None


class TestRunParams(BaseParams):
    """Parameters for test_run."""

    targets: list[str] | None = None
    pattern: str | None = None
    tags: list[str] | None = None
    failed_only: bool = False
    parallelism: int | None = None
    timeout_sec: int | None = None
    fail_fast: bool = False


class TestStatusParams(BaseParams):
    """Parameters for test_status."""

    run_id: str


class TestCancelParams(BaseParams):
    """Parameters for test_cancel."""

    run_id: str


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register("test_discover", "Discover test targets in the repository", TestDiscoverParams)
async def test_discover(ctx: AppContext, params: TestDiscoverParams) -> dict[str, Any]:
    """Discover tests."""
    result = await ctx.test_ops.discover(paths=params.paths)

    return {
        "action": result.action,
        "targets": [
            {
                "target_id": t.target_id,
                "path": t.path,
                "language": t.language,
                "runner": t.runner,
                "estimated_cost": t.estimated_cost,
                "test_count": t.test_count,
            }
            for t in (result.targets or [])
        ],
    }


# TODO: Implement TestOps.run() subprocess execution before enabling
# @registry.register("test_run", "Run tests", TestRunParams)
# async def test_run(ctx: AppContext, params: TestRunParams) -> dict[str, Any]:
#     """Run tests."""
#     result = await ctx.test_ops.run(
#         targets=params.targets,
#         _pattern=params.pattern,
#         _tags=params.tags,
#         _failed_only=params.failed_only,
#         _parallelism=params.parallelism,
#         _timeout_sec=params.timeout_sec,
#         _fail_fast=params.fail_fast,
#     )
#
#     return _serialize_test_result(result)


# TODO: Implement TestOps.status() run tracking before enabling
# @registry.register("test_status", "Get status of a test run", TestStatusParams)
# async def test_status(ctx: AppContext, params: TestStatusParams) -> dict[str, Any]:
#     """Get test run status."""
#     result = await ctx.test_ops.status(params.run_id)
#     return _serialize_test_result(result)


# TODO: Implement TestOps.cancel() run management before enabling
# @registry.register("test_cancel", "Cancel a running test", TestCancelParams)
# async def test_cancel(ctx: AppContext, params: TestCancelParams) -> dict[str, Any]:
#     """Cancel test run."""
#     result = await ctx.test_ops.cancel(params.run_id)
#     return _serialize_test_result(result)


def _serialize_test_result(result: TestResult) -> dict[str, Any]:
    """Convert TestResult to dict."""
    output: dict[str, Any] = {"action": result.action}

    if result.run_status:
        status = result.run_status
        output["run_status"] = {
            "run_id": status.run_id,
            "status": status.status,
            "duration_seconds": status.duration_seconds,
        }
        if status.progress:
            output["run_status"]["progress"] = {
                "total": status.progress.total,
                "completed": status.progress.completed,
                "passed": status.progress.passed,
                "failed": status.progress.failed,
                "skipped": status.progress.skipped,
            }
        if status.failures:
            output["run_status"]["failures"] = [
                {
                    "name": f.name,
                    "path": f.path,
                    "line": f.line,
                    "message": f.message,
                    "traceback": f.traceback,
                }
                for f in status.failures
            ]

    return output
