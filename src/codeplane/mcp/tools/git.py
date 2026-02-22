"""Git MCP tools - commit with pre-commit hook recovery.

All other git operations (status, log, diff, push, pull, branch, checkout,
merge, reset, stash, rebase, etc.) are better served by the agent running
git commands directly in the terminal. The only git operation that benefits
from MCP wrapping is commit, because it orchestrates:
  stage → lint → pre-commit hooks → auto-fix → re-stage → commit
which an agent cannot reliably chain in a terminal session.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from codeplane.git._internal.hooks import run_hook
from codeplane.git.errors import EmptyCommitMessageError, PathsNotFoundError

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# =============================================================================
# Validation Helpers
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


# =============================================================================
# Hook Helpers
# =============================================================================


def _run_hook_with_retry(
    repo_path: Path,
    paths_to_restage: list[str],
    stage_fn: Any,
) -> tuple[Any, dict[str, Any] | None]:
    """Run pre-commit hooks with auto-fix retry logic.

    Args:
        repo_path: Repository root path
        paths_to_restage: Original paths that should be included in restaging
        stage_fn: Function to call for staging files

    Returns:
        Tuple of (hook_result, failure_response).
        If failure_response is None, hooks passed and commit can proceed.
        If failure_response is not None, return it from the tool.
    """
    hook_result = run_hook(repo_path, "pre-commit")

    if hook_result.success:
        return hook_result, None

    auto_fixed = hook_result.modified_files or []

    if not auto_fixed:
        # Hook failed with no auto-fixes — manual intervention needed
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
            "agentic_hint": "Hook failed with errors that require manual fixing. Review the output above and fix the reported issues, then retry.",
        }

    # Hook auto-fixed files — re-stage and retry
    restage_paths = list(set(auto_fixed + paths_to_restage))
    stage_fn(restage_paths)

    retry_result = run_hook(repo_path, "pre-commit")

    if not retry_result.success:
        # Second attempt also failed — return combined output
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
            "agentic_hint": "Hook auto-fixed files on the first attempt but still failed on retry. This requires manual fixing. Review the output from both attempts above.",
        }

    # Retry succeeded
    return hook_result, None


def _summarize_commit(sha: str, message: str) -> str:
    from codeplane.core.formatting import truncate_at_word

    short_sha = sha[:7]
    first_line = message.split("\n")[0]
    truncated = truncate_at_word(first_line, 45)
    return f'{short_sha} "{truncated}"'


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register git tools with FastMCP server."""

    @mcp.tool(
        title="Commit: stage → hooks → commit → push",
        annotations=ToolAnnotations(
            title="Commit: stage → hooks → commit → push",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def commit(
        ctx: Context,
        message: str = Field(..., description="Commit message"),
        paths: list[str] | None = Field(
            None,
            description="Paths to stage before commit. If omitted, commits whatever is already staged.",
        ),
        all: bool = Field(  # noqa: A002
            False,
            description="Stage all modified/deleted tracked files before commit (like git commit -a).",
        ),
        push: bool = Field(
            False,
            description="Push to origin after successful commit.",
        ),
        allow_empty: bool = Field(False, description="Allow empty commits"),
    ) -> dict[str, Any]:
        """Stage, lint, run pre-commit hooks, commit, and optionally push.

        Orchestrates the full commit pipeline:
        1. Stage files (if paths or all provided)
        2. Run pre-commit hooks
        3. If hooks auto-fix files (formatters, linters), re-stage and retry
        4. Commit
        5. Push to origin (if push=True)

        This is the only git operation that benefits from MCP wrapping.
        For all other git operations (status, log, diff, branch, checkout,
        merge, reset, stash, rebase, push, pull), use terminal commands directly.
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        # Validate inputs
        _validate_commit_message(message)
        repo_path = Path(app_ctx.git_ops.repo.workdir)

        total_steps = 2 + int(push)  # stage + hooks + commit (+ push)
        step = 0

        # Stage files
        staged_paths: list[str] = []
        if all:
            await ctx.report_progress(step, total_steps, "Staging all tracked changes")
            staged_paths = app_ctx.git_ops.stage_all()
        elif paths:
            await ctx.report_progress(step, total_steps, f"Staging {len(paths)} path(s)")
            _validate_paths_exist(repo_path, paths)
            app_ctx.git_ops.stage(paths)
            staged_paths = paths
        step += 1

        # Run pre-commit hooks with auto-fix retry
        await ctx.report_progress(step, total_steps, "Running pre-commit hooks")
        hook_result, failure = _run_hook_with_retry(repo_path, staged_paths, app_ctx.git_ops.stage)
        if failure:
            await ctx.warning("Pre-commit hooks failed")
            return failure
        step += 1

        # Commit
        await ctx.report_progress(step, total_steps, "Committing")
        sha = app_ctx.git_ops.commit(message, allow_empty=allow_empty)
        result: dict[str, Any] = {
            "oid": sha,
            "short_oid": sha[:7],
            "summary": _summarize_commit(sha, message),
        }

        # If we got here via a retry, include a warning about what was auto-fixed
        if not hook_result.success:
            auto_fixed = hook_result.modified_files or []
            result["hook_warning"] = {
                "code": "HOOK_AUTO_FIXED",
                "message": "Pre-commit hooks auto-fixed files. Changes were re-staged and commit succeeded.",
                "auto_fixed_files": auto_fixed,
                "hook_stdout": hook_result.stdout,
            }

        # Optional push
        if push:
            await ctx.report_progress(step, total_steps, "Pushing to origin")
            app_ctx.git_ops.push(remote="origin", force=False)
            result["pushed"] = "origin"
            result["summary"] += " → pushed to origin"
            step += 1

        await ctx.report_progress(total_steps, total_steps, f"Committed {sha[:7]}")
        return result
