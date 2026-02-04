"""Testing MCP tools - test discovery and execution.

Split into verb-first tools:
- discover_test_targets: Find test targets
- run_test_targets: Execute tests
- get_test_run_status: Check run progress
- cancel_test_run: Abort a run
"""

from typing import TYPE_CHECKING, Any

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext
    from codeplane.testing.models import TestResult


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


def _display_run_start(result: "TestResult") -> str:
    """Human-friendly message for run action."""
    if not result.run_status:
        return "Test run initiated."
    status = result.run_status
    total = status.progress.targets.total if status.progress else 0
    return f"Test run started: {total} targets. Run ID: {status.run_id}"


def _display_run_status(result: "TestResult") -> str | None:
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


def _summarize_run(result: "TestResult") -> str:
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


def _serialize_test_result(result: "TestResult", is_action: bool = False) -> dict[str, Any]:
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
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register testing tools with FastMCP server."""

    @mcp.tool
    async def discover_test_targets(
        ctx: Context,
        paths: list[str] | None = Field(None, description="Paths to search for tests"),
    ) -> dict[str, Any]:
        """Find test targets in the repository. Returns testable files/directories with runner info."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.test_ops.discover(paths=paths)
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

    @mcp.tool
    async def run_test_targets(
        ctx: Context,
        targets: list[str] | None = Field(None, description="Target IDs from discover to run"),
        pattern: str | None = Field(None, description="Filter tests by pattern"),
        tags: list[str] | None = Field(None, description="Filter tests by tags"),
        failed_only: bool = Field(False, description="Run only previously failed tests"),
        parallelism: int | None = Field(None, description="Number of parallel workers"),
        timeout_sec: int | None = Field(None, description="Timeout in seconds"),
        fail_fast: bool = Field(False, description="Stop on first failure"),
        coverage: bool = Field(False, description="Collect coverage data"),
    ) -> dict[str, Any]:
        """Execute tests. Pass target_ids from discover, or use pattern/tags to filter."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.test_ops.run(
            targets=targets,
            pattern=pattern,
            tags=tags,
            failed_only=failed_only,
            parallelism=parallelism,
            timeout_sec=timeout_sec,
            fail_fast=fail_fast,
            coverage=coverage,
        )
        return _serialize_test_result(result, is_action=True)

    @mcp.tool
    async def get_test_run_status(
        ctx: Context,
        run_id: str = Field(..., description="ID of the test run to check"),
    ) -> dict[str, Any]:
        """Check progress of a running test. Returns pass/fail counts and any failures."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.test_ops.status(run_id)
        return _serialize_test_result(result, is_action=False)

    @mcp.tool
    async def cancel_test_run(
        ctx: Context,
        run_id: str = Field(..., description="ID of the test run to cancel"),
    ) -> dict[str, Any]:
        """Abort a running test execution."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.test_ops.cancel(run_id)
        return _serialize_test_result(result, is_action=True)

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
