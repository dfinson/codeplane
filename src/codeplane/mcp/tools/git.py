"""Git MCP tools - consolidated git_* handlers."""

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import Field

from codeplane.config.constants import GIT_BLAME_MAX, GIT_LOG_MAX
from codeplane.git._internal.hooks import run_hook
from codeplane.git.errors import EmptyCommitMessageError, PathsNotFoundError
from codeplane.mcp.budget import measure_bytes
from codeplane.mcp.delivery import wrap_existing_response
from codeplane.mcp.gate import DESTRUCTIVE_RESET_GATE

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# =============================================================================
# Constants
# =============================================================================

# Fingerprint key for tracking hard reset confirmation tokens
_HARD_RESET_TOKEN_KEY = "pending_hard_reset_token"


def _serialize_datetimes(obj: Any) -> Any:
    """Recursively convert datetime objects to ISO-8601 strings."""
    if isinstance(obj, dict):
        return {k: _serialize_datetimes(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_datetimes(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_status(branch: str | None, files: dict[str, int], is_clean: bool, state: int) -> str:
    """Generate summary for git_status."""
    if is_clean:
        return f"clean, branch: {branch or 'detached'}"

    modified = sum(1 for s in files.values() if s in (256, 512))
    staged = sum(1 for s in files.values() if s in (1, 2, 4))
    conflicted = sum(1 for s in files.values() if s >= 4096)

    parts = []
    if modified:
        parts.append(f"{modified} modified")
    if staged:
        parts.append(f"{staged} staged")
    if conflicted:
        parts.append(f"{conflicted} conflicts")

    status_str = ", ".join(parts) if parts else f"{len(files)} changes"
    state_str = ""
    if state == 1:
        state_str = ", rebase in progress"
    elif state == 2:
        state_str = ", merge in progress"

    return f"{status_str}, branch: {branch or 'detached'}{state_str}"


def _summarize_diff(
    page_files: int,
    page_additions: int,
    page_deletions: int,
    staged: bool,
    *,
    total_files: int | None = None,
) -> str:
    if page_files == 0:
        return "no changes" if not staged else "no staged changes"
    prefix = "staged: " if staged else ""
    # If paginated, show "page X of Y files"
    if total_files is not None and total_files > page_files:
        return f"{prefix}{page_files}/{total_files} files (+{page_additions}/-{page_deletions})"
    return f"{prefix}{page_files} files changed (+{page_additions}/-{page_deletions})"


def _summarize_commit(sha: str, message: str) -> str:
    from codeplane.core.formatting import truncate_at_word

    short_sha = sha[:7]
    first_line = message.split("\n")[0]
    truncated = truncate_at_word(first_line, 45)
    return f'{short_sha} "{truncated}"'


def _summarize_log(count: int, has_more: bool) -> str:
    more = " (more available)" if has_more else ""
    return f"{count} commits{more}"


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


def _summarize_branches(count: int, current: str | None) -> str:
    if current:
        return f"{count} branches, current: {current}"
    return f"{count} branches"


def _summarize_paths(action: str, paths: list[str]) -> str:
    from codeplane.core.formatting import format_path_list

    if len(paths) == 0:
        return f"nothing to {action}"
    path_str = format_path_list(paths, max_total=45)
    return f"{action} {path_str}"


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register git tools with FastMCP server."""

    # =========================================================================
    # Core Tools (explicit, high-frequency)
    # =========================================================================

    @mcp.tool
    async def git_status(
        ctx: Context,
        paths: list[str] | None = Field(None, description="Paths to check"),
    ) -> dict[str, Any]:
        """Get repository status."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        status = app_ctx.git_ops.status()
        head = app_ctx.git_ops.head()
        state = app_ctx.git_ops.state()
        branch = app_ctx.git_ops.current_branch()

        return {
            "branch": branch,
            "head_commit": head.target_sha,
            "is_clean": len(status) == 0,
            "is_detached": head.is_detached,
            "state": state,
            "files": status,
            "summary": _summarize_status(branch, status, len(status) == 0, state),
        }

    @mcp.tool
    async def git_diff(
        ctx: Context,
        base: str | None = Field(None, description="Base ref for comparison"),
        target: str | None = Field(None, description="Target ref for comparison"),
        staged: bool = Field(False, description="Show staged changes only"),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
    ) -> dict[str, Any]:
        """Get diff between refs or working tree."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        diff = app_ctx.git_ops.diff(
            base=base,
            target=target,
            staged=staged,
            include_patch=True,
        )

        all_files = list(diff.files)

        # Build file list
        files_out: list[dict[str, Any]] = []
        for f in all_files:
            files_out.append(
                {
                    "old_path": f.old_path,
                    "new_path": f.new_path,
                    "status": f.status,
                    "additions": f.additions,
                    "deletions": f.deletions,
                }
            )

        total_additions = sum(f.additions for f in all_files)
        total_deletions = sum(f.deletions for f in all_files)

        result: dict[str, Any] = {
            "files": files_out,
            "total_additions": total_additions,
            "total_deletions": total_deletions,
            "files_changed": len(all_files),
            "patch": diff.patch or "",
            "summary": _summarize_diff(
                len(all_files),
                total_additions,
                total_deletions,
                staged,
                total_files=len(all_files),
            ),
        }

        # Track scope usage
        scope_usage = None
        if scope_id:
            from codeplane.mcp.tools.files import _scope_manager

            budget = _scope_manager.get_or_create(scope_id)
            scope_usage = budget.to_usage_dict()

        return wrap_existing_response(
            result,
            resource_kind="diff",
            scope_id=scope_id,
            scope_usage=scope_usage,
        )

    @mcp.tool
    async def git_commit(
        ctx: Context,
        message: str = Field(..., description="Commit message"),
        paths: list[str] | None = Field(None, description="Paths to stage before commit"),
        allow_empty: bool = Field(False, description="Allow empty commits"),
    ) -> dict[str, Any]:
        """Create a commit.

        Stages paths (if provided), runs pre-commit hooks, and commits.
        If hooks auto-fix files (e.g. formatters), automatically re-stages
        and retries once. On retry success the commit goes through with a
        warning. On retry failure the full hook output from both attempts
        is returned.
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        # Validate inputs
        _validate_commit_message(message)
        repo_path = Path(app_ctx.git_ops.repo.workdir)
        if isinstance(paths, list) and paths:
            _validate_paths_exist(repo_path, paths)
            app_ctx.git_ops.stage(paths)

        original_paths = paths if isinstance(paths, list) else []
        hook_result, failure = _run_hook_with_retry(
            repo_path, original_paths, app_ctx.git_ops.stage
        )
        if failure:
            return failure

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

        return result

    @mcp.tool
    async def git_stage_and_commit(
        ctx: Context,
        message: str = Field(..., description="Commit message"),
        paths: list[str] = Field(..., description="Paths to stage before commit"),
        allow_empty: bool = Field(False, description="Allow empty commits"),
    ) -> dict[str, Any]:
        """Stage files and create a commit in one step.

        Stages the given paths, runs pre-commit hooks, and commits.
        If hooks auto-fix files (e.g. formatters), automatically re-stages
        and retries once.  On retry success the commit goes through with a
        warning.  On retry failure the full hook output from both attempts
        is returned.
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        # Validate inputs
        _validate_commit_message(message)
        repo_path = Path(app_ctx.git_ops.repo.workdir)
        _validate_paths_exist(repo_path, paths)

        app_ctx.git_ops.stage(paths)

        repo_path = Path(app_ctx.git_ops.repo.workdir)
        hook_result, failure = _run_hook_with_retry(repo_path, paths, app_ctx.git_ops.stage)
        if failure:
            return failure

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

        return result

    @mcp.tool
    async def git_log(
        ctx: Context,
        ref: str = Field("HEAD", description="Starting reference"),
        limit: int = Field(default=50, le=GIT_LOG_MAX, description="Maximum commits to return"),
        since: str | None = Field(None, description="Show commits after date"),
        until: str | None = Field(None, description="Show commits before date"),
        paths: list[str] | None = Field(None, description="Filter by paths"),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
    ) -> dict[str, Any]:
        """Get commit history."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        commits = app_ctx.git_ops.log(
            ref=ref,
            limit=limit,
            since=since,
            until=until,
            paths=paths,
        )

        items: list[dict[str, Any]] = []
        for c in commits:
            d = asdict(c)
            # Convert datetime fields to ISO strings for JSON serialization
            for sig_key in ("author", "committer"):
                if sig_key in d and "time" in d[sig_key]:
                    d[sig_key]["time"] = d[sig_key]["time"].isoformat()
            items.append(d)

        result = {
            "results": items,
            "summary": _summarize_log(len(items), False),
        }
        # Track scope usage
        scope_usage = None
        if scope_id:
            from codeplane.mcp.tools.files import _scope_manager

            budget = _scope_manager.get_or_create(scope_id)
            scope_usage = budget.to_usage_dict()

        return wrap_existing_response(
            result,
            resource_kind="log",
            scope_id=scope_id,
            scope_usage=scope_usage,
        )

    @mcp.tool
    async def git_push(
        ctx: Context,
        remote: str = Field("origin", description="Remote name"),
        force: bool = Field(False, description="Force push"),
    ) -> dict[str, Any]:
        """Push to remote."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        app_ctx.git_ops.push(remote=remote, force=force)
        force_str = " (force)" if force else ""
        return {
            "pushed": remote,
            "summary": f"pushed to {remote}{force_str}",
        }

    @mcp.tool
    async def git_pull(
        ctx: Context,
        remote: str = Field("origin", description="Remote name"),
    ) -> dict[str, Any]:
        """Pull from remote."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = app_ctx.git_ops.pull(remote=remote)
        res = asdict(result)
        res["summary"] = f"pulled from {remote}"
        return res

    @mcp.tool
    async def git_checkout(
        ctx: Context,
        ref: str = Field(..., description="Reference to checkout"),
        create: bool = Field(False, description="Create new branch"),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
    ) -> dict[str, Any]:
        """Checkout a ref."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        app_ctx.git_ops.checkout(ref, create=create)

        # Reset scope budget duplicate tracking after mutation
        if scope_id:
            from codeplane.mcp.tools.files import _scope_manager

            _scope_manager.record_mutation(scope_id)

        action = "created and checked out" if create else "checked out"
        return {
            "checked_out": ref,
            "summary": f"{action} {ref}",
        }

    @mcp.tool
    async def git_merge(
        ctx: Context,
        ref: str = Field(..., description="Reference to merge"),
    ) -> dict[str, Any]:
        """Merge a branch."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = app_ctx.git_ops.merge(ref)
        res = asdict(result)
        if result.conflict_paths:
            res["summary"] = f"merge {ref}: {len(result.conflict_paths)} conflicts"
        else:
            res["summary"] = f"merged {ref}"
        return res

    @mcp.tool
    async def git_reset(
        ctx: Context,
        ref: str = Field(..., description="Reference to reset to"),
        mode: Literal["soft", "mixed", "hard"] = Field("mixed", description="Reset mode"),
        confirmation_token: str | None = Field(
            None,
            description="Required for hard reset. Obtain from initial call without token.",
        ),
        gate_reason: str | None = Field(
            None,
            description="Reason for hard reset (min 50 chars). Required with confirmation_token.",
        ),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
    ) -> dict[str, Any]:
        """Reset HEAD to a ref.

        For mode='hard', a two-phase confirmation is required:
        1. First call without confirmation_token returns a warning and token
        2. Second call with the token executes the reset

        This prevents accidental data loss from uncommitted changes.
        """
        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        gm = session.gate_manager

        # For hard reset, enforce two-phase confirmation via unified GateManager
        if mode == "hard":
            # Phase 2: Validate token + reason and execute
            if confirmation_token:
                gate_reason_str = gate_reason if isinstance(gate_reason, str) else ""
                result = gm.validate(confirmation_token, gate_reason_str)
                if not result.ok:
                    return {
                        "error": {
                            "code": "GATE_VALIDATION_FAILED",
                            "message": result.error,
                        },
                        "hint": result.hint,
                        "summary": "gate validation failed",
                    }
                # Gate passed — proceed to execute

            # Phase 1: Issue gate and return warning
            else:
                # Gather information about what would be lost
                status = app_ctx.git_ops.status()
                uncommitted_files = list(status.keys())
                uncommitted_count = len(uncommitted_files)

                # Issue gate via unified GateManager
                gate_block = gm.issue(DESTRUCTIVE_RESET_GATE)

                return {
                    "requires_confirmation": True,
                    "gate": gate_block,
                    "confirmation_token": gate_block["id"],
                    "mode": mode,
                    "target_ref": ref,
                    "uncommitted_files_count": uncommitted_count,
                    "uncommitted_files": uncommitted_files[:20],
                    "warning": DESTRUCTIVE_RESET_GATE.message,
                    "agentic_hint": (
                        "STOP: This operation is irreversible and may destroy work. "
                        "You MUST ask the user for explicit approval before proceeding. "
                        "If approved, call git_reset again with the same parameters "
                        f"plus confirmation_token='{gate_block['id']}' and "
                        f"gate_reason='<reason min {DESTRUCTIVE_RESET_GATE.reason_min_chars} chars: "
                        f"{DESTRUCTIVE_RESET_GATE.reason_prompt}>'."
                    ),
                    "summary": f"BLOCKED: hard reset requires user approval ({uncommitted_count} uncommitted files at risk)",
                }

        # Execute the reset (soft/mixed immediately, hard after confirmation)
        app_ctx.git_ops.reset(ref, mode=mode)

        # Reset scope budget duplicate tracking after mutation
        if scope_id:
            from codeplane.mcp.tools.files import _scope_manager

            _scope_manager.record_mutation(scope_id)

        ref_display = ref[:12] if len(ref) > 12 else ref
        return {
            "reset_to": ref,
            "mode": mode,
            "summary": f"reset ({mode}) to {ref_display}",
        }

    # =========================================================================
    # Collapsed Tools (action-based, lower frequency)
    # =========================================================================

    @mcp.tool
    async def git_stage(
        ctx: Context,
        action: Literal["add", "remove", "all", "discard"] = Field(
            ..., description="Staging action"
        ),
        paths: list[str] | None = Field(None, description="Required for add/remove/discard"),
    ) -> dict[str, Any]:
        """Stage or unstage files."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "add":
            if not paths:
                raise ValueError("paths required for action='add'")
            app_ctx.git_ops.stage(paths)
            return {"staged": paths, "summary": _summarize_paths("staged", paths)}

        elif action == "remove":
            if not paths:
                raise ValueError("paths required for action='remove'")
            app_ctx.git_ops.unstage(paths)
            return {"unstaged": paths, "summary": _summarize_paths("unstaged", paths)}

        elif action == "all":
            staged = app_ctx.git_ops.stage_all()
            return {
                "staged": staged,
                "summary": _summarize_paths("staged", staged) if staged else "nothing to stage",
            }

        elif action == "discard":
            if not paths:
                raise ValueError("paths required for action='discard'")
            app_ctx.git_ops.discard(paths)
            return {"discarded": paths, "summary": _summarize_paths("discarded", paths)}

        raise ValueError(f"Unknown action: {action}")

    @mcp.tool
    async def git_branch(
        ctx: Context,
        action: Literal["list", "create", "delete"] = Field(..., description="Branch action"),
        name: str | None = Field(None, description="Branch name (required for create/delete)"),
        ref: str = Field("HEAD", description="Base ref for create"),
        force: bool = Field(False, description="Force delete"),
    ) -> dict[str, Any]:
        """Manage branches."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "list":
            branches = app_ctx.git_ops.branches(include_remote=True)
            current = app_ctx.git_ops.current_branch()
            return {
                "branches": [asdict(b) for b in branches],
                "summary": _summarize_branches(len(branches), current),
            }

        elif action == "create":
            if not name:
                raise ValueError("name required for action='create'")
            branch = app_ctx.git_ops.create_branch(name, ref=ref)
            result = asdict(branch)
            result["summary"] = f"created branch {name}"
            return result

        elif action == "delete":
            if not name:
                raise ValueError("name required for action='delete'")
            app_ctx.git_ops.delete_branch(name, force=force)
            return {"deleted": name, "summary": f"deleted branch {name}"}

        raise ValueError(f"Unknown action: {action}")

    @mcp.tool
    async def git_remote(
        ctx: Context,
        action: Literal["list", "fetch", "tags"] = Field(..., description="Remote action"),
        remote: str = Field("origin", description="Remote name"),
    ) -> dict[str, Any]:
        """Manage remotes."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "list":
            remotes = app_ctx.git_ops.remotes()
            return {"remotes": [asdict(r) for r in remotes], "summary": f"{len(remotes)} remotes"}

        elif action == "fetch":
            app_ctx.git_ops.fetch(remote=remote)
            return {"fetched": remote, "summary": f"fetched from {remote}"}

        elif action == "tags":
            tags = app_ctx.git_ops.tags()
            return {"tags": [asdict(t) for t in tags], "summary": f"{len(tags)} tags"}

        raise ValueError(f"Unknown action: {action}")

    @mcp.tool
    async def git_stash(
        ctx: Context,
        action: Literal["push", "pop", "list"] = Field(..., description="Stash action"),
        message: str | None = Field(None, description="Stash message (for push)"),
        include_untracked: bool = Field(False, description="Include untracked files (for push)"),
        index: int = Field(0, description="Stash index (for pop)"),
    ) -> dict[str, Any]:
        """Manage stash."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "push":
            sha = app_ctx.git_ops.stash_push(
                message=message,
                include_untracked=include_untracked,
            )
            msg = f': "{message}"' if message else ""
            return {"stash_commit": sha, "summary": f"stashed{msg}"}

        elif action == "pop":
            app_ctx.git_ops.stash_pop(index=index)
            return {"popped": index, "summary": f"popped stash@{{{index}}}"}

        elif action == "list":
            entries = app_ctx.git_ops.stash_list()
            return {
                "entries": [asdict(e) for e in entries],
                "summary": f"{len(entries)} stash entries",
            }

        raise ValueError(f"Unknown action: {action}")

    @mcp.tool
    async def git_rebase(
        ctx: Context,
        action: Literal["plan", "continue", "abort", "skip"] = Field(
            ..., description="Rebase action"
        ),
        upstream: str | None = Field(None, description="Upstream ref (required for plan)"),
        onto: str | None = Field(None, description="Onto ref (optional for plan)"),
    ) -> dict[str, Any]:
        """Manage rebase."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "plan":
            if not upstream:
                raise ValueError("upstream required for action='plan'")
            plan = app_ctx.git_ops.rebase_plan(upstream, onto=onto)
            result = asdict(plan)
            onto_str = f" onto {onto}" if onto else ""
            result["summary"] = f"rebasing {len(plan.steps)} commits{onto_str}"
            return result

        elif action == "continue":
            rebase_result = app_ctx.git_ops.rebase_continue()
            res = asdict(rebase_result)
            res["summary"] = "rebase continued"
            return res

        elif action == "abort":
            app_ctx.git_ops.rebase_abort()
            return {"aborted": True, "summary": "rebase aborted"}

        elif action == "skip":
            skip_result = app_ctx.git_ops.rebase_skip()
            res = asdict(skip_result)
            res["summary"] = "skipped commit"
            return res

        raise ValueError(f"Unknown action: {action}")

    @mcp.tool
    async def git_inspect(
        ctx: Context,
        action: Literal["show", "blame"] = Field(..., description="Inspect action"),
        ref: str = Field("HEAD", description="Commit ref (for show)"),
        path: str | None = Field(None, description="File path (required for blame)"),
        start_line: int | None = Field(None, description="Start line (for blame)"),
        end_line: int | None = Field(None, description="End line (for blame)"),
        limit: int = Field(default=100, le=GIT_BLAME_MAX, description="Maximum lines to return"),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
    ) -> dict[str, Any]:
        """Inspect commits or blame."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "show":
            commit_obj = app_ctx.git_ops.show(ref=ref)
            result = _serialize_datetimes(asdict(commit_obj))
            result["summary"] = f"{commit_obj.sha[:7]}: {commit_obj.message.split(chr(10))[0][:50]}"

            # Track scope usage
            scope_usage = None
            if scope_id:
                from codeplane.mcp.tools.files import _scope_manager

                budget = _scope_manager.get_or_create(scope_id)
                budget.increment_read(measure_bytes(result))
                exceeded = budget.check_budget("read_bytes")
                if exceeded:
                    from codeplane.mcp.errors import BudgetExceededError

                    raise BudgetExceededError(scope_id, "read_bytes", exceeded)
                scope_usage = budget.to_usage_dict()

            return wrap_existing_response(
                result,
                resource_kind="commit",
                scope_id=scope_id,
                scope_usage=scope_usage,
            )

        elif action == "blame":
            if not path:
                raise ValueError("path required for action='blame'")
            blame = app_ctx.git_ops.blame(
                path,
                min_line=start_line,
                max_line=end_line,
            )
            blame_dict = _serialize_datetimes(asdict(blame))
            hunks = blame_dict.pop("hunks", [])
            page = hunks[:limit]
            from codeplane.core.formatting import compress_path

            # Count total lines covered by hunks on this page
            total_lines = sum(h.get("line_count", 0) for h in page)
            result = {
                "results": page,
                **blame_dict,
                "summary": f"{total_lines} lines ({len(page)} hunks) from {compress_path(path, 35)}",
            }

            # Track scope usage
            scope_usage = None
            if scope_id:
                from codeplane.mcp.tools.files import _scope_manager

                budget = _scope_manager.get_or_create(scope_id)
                budget.increment_read(measure_bytes(result))
                exceeded = budget.check_budget("read_bytes")
                if exceeded:
                    from codeplane.mcp.errors import BudgetExceededError

                    raise BudgetExceededError(scope_id, "read_bytes", exceeded)
                scope_usage = budget.to_usage_dict()

            return wrap_existing_response(
                result,
                resource_kind="blame",
                scope_id=scope_id,
                scope_usage=scope_usage,
            )

        raise ValueError(f"Unknown action: {action}")

    @mcp.tool
    async def git_history(
        ctx: Context,
        action: Literal["amend", "cherrypick", "revert"] = Field(..., description="History action"),
        commit: str | None = Field(None, description="Commit ref (required for cherrypick/revert)"),
        message: str | None = Field(None, description="New message (for amend)"),
    ) -> dict[str, Any]:
        """Amend, cherry-pick, or revert commits."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "amend":
            sha = app_ctx.git_ops.amend(message=message)
            return {"oid": sha, "short_oid": sha[:7], "summary": f"amended to {sha[:7]}"}

        elif action == "cherrypick":
            if not commit:
                raise ValueError("commit required for action='cherrypick'")
            result = app_ctx.git_ops.cherrypick(commit)
            res = asdict(result)
            res["summary"] = f"cherry-picked {commit[:7]}"
            return res

        elif action == "revert":
            if not commit:
                raise ValueError("commit required for action='revert'")
            result = app_ctx.git_ops.revert(commit)
            res = asdict(result)
            res["summary"] = f"reverted {commit[:7]}"
            return res

        raise ValueError(f"Unknown action: {action}")

    @mcp.tool
    async def git_submodule(
        ctx: Context,
        action: Literal["list", "add", "update", "init", "remove"] = Field(
            ..., description="Submodule action"
        ),
        path: str | None = Field(None, description="Submodule path"),
        url: str | None = Field(None, description="Repository URL (for add)"),
        branch: str | None = Field(None, description="Branch to track (for add)"),
        paths: list[str] | None = Field(None, description="Paths to update/init"),
        recursive: bool = Field(False, description="Recursive update"),
        init: bool = Field(True, description="Initialize before update"),
    ) -> dict[str, Any]:
        """Manage submodules."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "list":
            submodules = app_ctx.git_ops.submodules()
            return {
                "submodules": [asdict(s) for s in submodules],
                "summary": f"{len(submodules)} submodules",
            }

        elif action == "add":
            if not url or not path:
                raise ValueError("url and path required for action='add'")
            sm = app_ctx.git_ops.submodule_add(url, path, branch)
            result = asdict(sm)
            result["summary"] = f"added submodule at {path}"
            return result

        elif action == "update":
            upd_result = app_ctx.git_ops.submodule_update(paths, recursive, init)
            res = asdict(upd_result)
            res["summary"] = "submodules updated"
            return res

        elif action == "init":
            init_paths = app_ctx.git_ops.submodule_init(paths)
            return {
                "initialized": init_paths,
                "summary": f"initialized {len(init_paths)} submodules",
            }

        elif action == "remove":
            if not path:
                raise ValueError("path required for action='remove'")
            app_ctx.git_ops.submodule_remove(path)
            return {"removed": path, "summary": f"removed submodule {path}"}

        raise ValueError(f"Unknown action: {action}")

    @mcp.tool
    async def git_worktree(
        ctx: Context,
        action: Literal["list", "add", "remove", "lock", "unlock", "prune"] = Field(
            ..., description="Worktree action"
        ),
        path: str | None = Field(None, description="Worktree path (for add)"),
        ref: str | None = Field(None, description="Branch/commit ref (for add)"),
        name: str | None = Field(None, description="Worktree name (for remove/lock/unlock)"),
        reason: str | None = Field(None, description="Lock reason"),
        force: bool = Field(False, description="Force remove"),
    ) -> dict[str, Any]:
        """Manage worktrees."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "list":
            worktrees = app_ctx.git_ops.worktrees()
            return {
                "worktrees": [asdict(w) for w in worktrees],
                "summary": f"{len(worktrees)} worktrees",
            }

        elif action == "add":
            if not path or not ref:
                raise ValueError("path and ref required for action='add'")
            app_ctx.git_ops.worktree_add(Path(path), ref)
            return {
                "created": path,
                "ref": ref,
                "summary": f"added worktree at {path}",
            }

        elif action == "remove":
            if not name:
                raise ValueError("name required for action='remove'")
            app_ctx.git_ops.worktree_remove(name, force)
            return {"removed": name, "summary": f"removed worktree {name}"}

        elif action == "lock":
            if not name:
                raise ValueError("name required for action='lock'")
            app_ctx.git_ops.worktree_lock(name, reason)
            return {"locked": name, "summary": f"locked worktree {name}"}

        elif action == "unlock":
            if not name:
                raise ValueError("name required for action='unlock'")
            app_ctx.git_ops.worktree_unlock(name)
            return {"unlocked": name, "summary": f"unlocked worktree {name}"}

        elif action == "prune":
            pruned = app_ctx.git_ops.worktree_prune()
            return {
                "pruned": pruned,
                "summary": f"pruned {len(pruned)} worktrees" if pruned else "nothing to prune",
            }

        raise ValueError(f"Unknown action: {action}")

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
