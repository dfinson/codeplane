"""Git MCP tools - consolidated git_* handlers."""

from __future__ import annotations

import contextlib
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from codeplane.config.constants import GIT_BLAME_MAX, GIT_LOG_MAX
from codeplane.git._internal.hooks import run_hook
from codeplane.mcp.errors import HookFailedError
from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models - Core (explicit tools)
# =============================================================================


class GitStatusParams(BaseParams):
    """Parameters for git_status."""

    paths: list[str] | None = None


class GitDiffParams(BaseParams):
    """Parameters for git_diff."""

    base: str | None = Field(None, description="Base ref for comparison")
    target: str | None = Field(None, description="Target ref for comparison")
    staged: bool = Field(False, description="Show staged changes only")


class GitCommitParams(BaseParams):
    """Parameters for git_commit."""

    message: str
    paths: list[str] | None = None
    allow_empty: bool = False


class GitLogParams(BaseParams):
    """Parameters for git_log."""

    ref: str = "HEAD"
    limit: int = Field(default=50, le=GIT_LOG_MAX)
    cursor: str | None = None
    since: str | None = None
    until: str | None = None
    paths: list[str] | None = None


class GitPushParams(BaseParams):
    """Parameters for git_push."""

    remote: str = "origin"
    force: bool = False


class GitPullParams(BaseParams):
    """Parameters for git_pull."""

    remote: str = "origin"


class GitCheckoutParams(BaseParams):
    """Parameters for git_checkout."""

    ref: str
    create: bool = False


class GitMergeParams(BaseParams):
    """Parameters for git_merge."""

    ref: str


class GitResetParams(BaseParams):
    """Parameters for git_reset."""

    ref: str
    mode: Literal["soft", "mixed", "hard"] = "mixed"


# =============================================================================
# Parameter Models - Collapsed (action-based tools)
# =============================================================================


class GitStageParams(BaseParams):
    """Parameters for git_stage."""

    action: Literal["add", "remove", "all", "discard"]
    paths: list[str] | None = Field(None, description="Required for add/remove/discard")


class GitBranchParams(BaseParams):
    """Parameters for git_branch."""

    action: Literal["list", "create", "delete"]
    name: str | None = Field(None, description="Branch name (required for create/delete)")
    ref: str = Field("HEAD", description="Base ref for create")
    force: bool = Field(False, description="Force delete")


class GitRemoteParams(BaseParams):
    """Parameters for git_remote."""

    action: Literal["list", "fetch", "tags"]
    remote: str = "origin"


class GitStashParams(BaseParams):
    """Parameters for git_stash."""

    action: Literal["push", "pop", "list"]
    message: str | None = Field(None, description="Stash message (for push)")
    include_untracked: bool = Field(False, description="Include untracked files (for push)")
    index: int = Field(0, description="Stash index (for pop)")


class GitRebaseParams(BaseParams):
    """Parameters for git_rebase."""

    action: Literal["plan", "continue", "abort", "skip"]
    upstream: str | None = Field(None, description="Upstream ref (required for plan)")
    onto: str | None = Field(None, description="Onto ref (optional for plan)")


class GitInspectParams(BaseParams):
    """Parameters for git_inspect."""

    action: Literal["show", "blame"]
    ref: str = Field("HEAD", description="Commit ref (for show)")
    path: str | None = Field(None, description="File path (required for blame)")
    start_line: int | None = Field(None, description="Start line (for blame)")
    end_line: int | None = Field(None, description="End line (for blame)")
    cursor: str | None = None
    limit: int = Field(default=100, le=GIT_BLAME_MAX)


class GitHistoryParams(BaseParams):
    """Parameters for git_history."""

    action: Literal["amend", "cherrypick", "revert"]
    commit: str | None = Field(None, description="Commit ref (required for cherrypick/revert)")
    message: str | None = Field(None, description="New message (for amend)")


class GitSubmoduleParams(BaseParams):
    """Parameters for git_submodule."""

    action: Literal["list", "add", "update", "init", "remove"]
    path: str | None = Field(None, description="Submodule path")
    url: str | None = Field(None, description="Repository URL (for add)")
    branch: str | None = Field(None, description="Branch to track (for add)")
    paths: list[str] | None = Field(None, description="Paths to update/init")
    recursive: bool = Field(False, description="Recursive update")
    init: bool = Field(True, description="Initialize before update")


class GitWorktreeParams(BaseParams):
    """Parameters for git_worktree."""

    action: Literal["list", "add", "remove", "lock", "unlock", "prune"]
    path: str | None = Field(None, description="Worktree path (for add)")
    ref: str | None = Field(None, description="Branch/commit ref (for add)")
    name: str | None = Field(None, description="Worktree name (for remove/lock/unlock)")
    reason: str | None = Field(None, description="Lock reason")
    force: bool = False


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


def _summarize_diff(files_changed: int, additions: int, deletions: int, staged: bool) -> str:
    if files_changed == 0:
        return "no changes" if not staged else "no staged changes"
    prefix = "staged: " if staged else ""
    return f"{prefix}{files_changed} files changed (+{additions}/-{deletions})"


def _summarize_commit(sha: str, message: str) -> str:
    short_sha = sha[:7]
    first_line = message.split("\n")[0][:50]
    if len(message.split("\n")[0]) > 50:
        first_line += "..."
    return f'{short_sha} "{first_line}"'


def _summarize_log(count: int, has_more: bool) -> str:
    more = " (more available)" if has_more else ""
    return f"{count} commits{more}"


def _summarize_branches(count: int, current: str | None) -> str:
    if current:
        return f"{count} branches, current: {current}"
    return f"{count} branches"


def _summarize_paths(action: str, paths: list[str]) -> str:
    if len(paths) == 1:
        return f"{action} {paths[0]}"
    if len(paths) <= 3:
        return f"{action} {len(paths)} files ({', '.join(paths)})"
    return f"{action} {len(paths)} files ({paths[0]}, {paths[1]}, +{len(paths) - 2} more)"


# =============================================================================
# Core Tools (explicit, high-frequency)
# =============================================================================


@registry.register("git_status", "Get repository status", GitStatusParams)
async def git_status(ctx: AppContext, _params: GitStatusParams) -> dict[str, Any]:
    """Get repository status."""
    status = ctx.git_ops.status()
    head = ctx.git_ops.head()
    state = ctx.git_ops.state()
    branch = ctx.git_ops.current_branch()

    return {
        "branch": branch,
        "head_commit": head.target_sha,
        "is_clean": len(status) == 0,
        "is_detached": head.is_detached,
        "state": state,
        "files": status,
        "summary": _summarize_status(branch, status, len(status) == 0, state),
    }


@registry.register("git_diff", "Get diff between refs or working tree", GitDiffParams)
async def git_diff(ctx: AppContext, params: GitDiffParams) -> dict[str, Any]:
    """Get diff."""
    diff = ctx.git_ops.diff(
        base=params.base,
        target=params.target,
        staged=params.staged,
        include_patch=True,
    )
    result = asdict(diff)
    result["summary"] = _summarize_diff(
        diff.files_changed, diff.total_additions, diff.total_deletions, params.staged
    )
    return result


@registry.register("git_commit", "Create a commit", GitCommitParams)
async def git_commit(ctx: AppContext, params: GitCommitParams) -> dict[str, Any]:
    """Create commit with pre-commit hook execution."""
    if params.paths:
        ctx.git_ops.stage(params.paths)

    repo_path = Path(ctx.git_ops.repo.workdir)
    hook_result = run_hook(repo_path, "pre-commit")

    if not hook_result.success:
        raise HookFailedError(
            hook_type="pre-commit",
            exit_code=hook_result.exit_code,
            stdout=hook_result.stdout,
            stderr=hook_result.stderr,
            modified_files=hook_result.modified_files,
        )

    sha = ctx.git_ops.commit(params.message, allow_empty=params.allow_empty)
    return {
        "oid": sha,
        "short_oid": sha[:7],
        "summary": _summarize_commit(sha, params.message),
    }


@registry.register("git_log", "Get commit history", GitLogParams)
async def git_log(ctx: AppContext, params: GitLogParams) -> dict[str, Any]:
    """Get commit log."""
    commits = ctx.git_ops.log(
        ref=params.ref,
        limit=params.limit + 1,
        since=params.since,
        until=params.until,
        paths=params.paths,
    )

    has_more = len(commits) > params.limit
    if has_more:
        commits = commits[: params.limit]

    pagination: dict[str, Any] = {}
    if has_more and commits:
        pagination["next_cursor"] = commits[-1].sha

    return {
        "results": [asdict(c) for c in commits],
        "pagination": pagination,
        "summary": _summarize_log(len(commits), has_more),
    }


@registry.register("git_push", "Push to remote", GitPushParams)
async def git_push(ctx: AppContext, params: GitPushParams) -> dict[str, Any]:
    """Push to remote."""
    ctx.git_ops.push(remote=params.remote, force=params.force)
    force_str = " (force)" if params.force else ""
    return {
        "pushed": params.remote,
        "summary": f"pushed to {params.remote}{force_str}",
    }


@registry.register("git_pull", "Pull from remote", GitPullParams)
async def git_pull(ctx: AppContext, params: GitPullParams) -> dict[str, Any]:
    """Pull from remote."""
    result = ctx.git_ops.pull(remote=params.remote)
    res = asdict(result)
    res["summary"] = f"pulled from {params.remote}"
    return res


@registry.register("git_checkout", "Checkout a ref", GitCheckoutParams)
async def git_checkout(ctx: AppContext, params: GitCheckoutParams) -> dict[str, Any]:
    """Checkout ref."""
    ctx.git_ops.checkout(params.ref, create=params.create)
    action = "created and checked out" if params.create else "checked out"
    return {
        "checked_out": params.ref,
        "summary": f"{action} {params.ref}",
    }


@registry.register("git_merge", "Merge a branch", GitMergeParams)
async def git_merge(ctx: AppContext, params: GitMergeParams) -> dict[str, Any]:
    """Merge branch."""
    result = ctx.git_ops.merge(params.ref)
    res = asdict(result)
    if result.conflict_paths:
        res["summary"] = f"merge {params.ref}: {len(result.conflict_paths)} conflicts"
    else:
        res["summary"] = f"merged {params.ref}"
    return res


@registry.register("git_reset", "Reset HEAD to a ref", GitResetParams)
async def git_reset(ctx: AppContext, params: GitResetParams) -> dict[str, Any]:
    """Reset HEAD."""
    ctx.git_ops.reset(params.ref, mode=params.mode)
    ref_display = params.ref[:12] if len(params.ref) > 12 else params.ref
    return {
        "reset_to": params.ref,
        "mode": params.mode,
        "summary": f"reset ({params.mode}) to {ref_display}",
    }


# =============================================================================
# Collapsed Tools (action-based, lower frequency)
# =============================================================================


@registry.register("git_stage", "Stage or unstage files", GitStageParams)
async def git_stage(ctx: AppContext, params: GitStageParams) -> dict[str, Any]:
    """Stage/unstage files based on action."""
    if params.action == "add":
        if not params.paths:
            raise ValueError("paths required for action='add'")
        ctx.git_ops.stage(params.paths)
        return {"staged": params.paths, "summary": _summarize_paths("staged", params.paths)}

    elif params.action == "remove":
        if not params.paths:
            raise ValueError("paths required for action='remove'")
        ctx.git_ops.unstage(params.paths)
        return {"unstaged": params.paths, "summary": _summarize_paths("unstaged", params.paths)}

    elif params.action == "all":
        staged = ctx.git_ops.stage_all()
        return {
            "staged": staged,
            "summary": _summarize_paths("staged", staged) if staged else "nothing to stage",
        }

    elif params.action == "discard":
        if not params.paths:
            raise ValueError("paths required for action='discard'")
        ctx.git_ops.discard(params.paths)
        return {"discarded": params.paths, "summary": _summarize_paths("discarded", params.paths)}

    raise ValueError(f"Unknown action: {params.action}")


@registry.register("git_branch", "Manage branches", GitBranchParams)
async def git_branch(ctx: AppContext, params: GitBranchParams) -> dict[str, Any]:
    """List, create, or delete branches."""
    if params.action == "list":
        branches = ctx.git_ops.branches(include_remote=True)
        current = ctx.git_ops.current_branch()
        return {
            "branches": [asdict(b) for b in branches],
            "summary": _summarize_branches(len(branches), current),
        }

    elif params.action == "create":
        if not params.name:
            raise ValueError("name required for action='create'")
        branch = ctx.git_ops.create_branch(params.name, ref=params.ref)
        result = asdict(branch)
        result["summary"] = f"created branch {params.name}"
        return result

    elif params.action == "delete":
        if not params.name:
            raise ValueError("name required for action='delete'")
        ctx.git_ops.delete_branch(params.name, force=params.force)
        return {"deleted": params.name, "summary": f"deleted branch {params.name}"}

    raise ValueError(f"Unknown action: {params.action}")


@registry.register("git_remote", "Manage remotes", GitRemoteParams)
async def git_remote(ctx: AppContext, params: GitRemoteParams) -> dict[str, Any]:
    """List remotes, fetch, or list tags."""
    if params.action == "list":
        remotes = ctx.git_ops.remotes()
        return {"remotes": [asdict(r) for r in remotes], "summary": f"{len(remotes)} remotes"}

    elif params.action == "fetch":
        ctx.git_ops.fetch(remote=params.remote)
        return {"fetched": params.remote, "summary": f"fetched from {params.remote}"}

    elif params.action == "tags":
        tags = ctx.git_ops.tags()
        return {"tags": [asdict(t) for t in tags], "summary": f"{len(tags)} tags"}

    raise ValueError(f"Unknown action: {params.action}")


@registry.register("git_stash", "Manage stash", GitStashParams)
async def git_stash(ctx: AppContext, params: GitStashParams) -> dict[str, Any]:
    """Push, pop, or list stash entries."""
    if params.action == "push":
        sha = ctx.git_ops.stash_push(
            message=params.message,
            include_untracked=params.include_untracked,
        )
        msg = f': "{params.message}"' if params.message else ""
        return {"stash_commit": sha, "summary": f"stashed{msg}"}

    elif params.action == "pop":
        ctx.git_ops.stash_pop(index=params.index)
        return {"popped": params.index, "summary": f"popped stash@{{{params.index}}}"}

    elif params.action == "list":
        entries = ctx.git_ops.stash_list()
        return {"entries": [asdict(e) for e in entries], "summary": f"{len(entries)} stash entries"}

    raise ValueError(f"Unknown action: {params.action}")


@registry.register("git_rebase", "Manage rebase", GitRebaseParams)
async def git_rebase(ctx: AppContext, params: GitRebaseParams) -> dict[str, Any]:
    """Plan, continue, abort, or skip rebase."""
    if params.action == "plan":
        if not params.upstream:
            raise ValueError("upstream required for action='plan'")
        plan = ctx.git_ops.rebase_plan(params.upstream, onto=params.onto)
        result = asdict(plan)
        onto_str = f" onto {params.onto}" if params.onto else ""
        result["summary"] = f"rebasing {len(plan.steps)} commits{onto_str}"
        return result

    elif params.action == "continue":
        rebase_result = ctx.git_ops.rebase_continue()
        res = asdict(rebase_result)
        res["summary"] = "rebase continued"
        return res

    elif params.action == "abort":
        ctx.git_ops.rebase_abort()
        return {"aborted": True, "summary": "rebase aborted"}

    elif params.action == "skip":
        skip_result = ctx.git_ops.rebase_skip()
        res = asdict(skip_result)
        res["summary"] = "skipped commit"
        return res

    raise ValueError(f"Unknown action: {params.action}")


@registry.register("git_inspect", "Inspect commits or blame", GitInspectParams)
async def git_inspect(ctx: AppContext, params: GitInspectParams) -> dict[str, Any]:
    """Show commit details or file blame."""
    if params.action == "show":
        commit = ctx.git_ops.show(ref=params.ref)
        result = asdict(commit)
        result["summary"] = f"{commit.sha[:7]}: {commit.message.split(chr(10))[0][:50]}"
        return result

    elif params.action == "blame":
        if not params.path:
            raise ValueError("path required for action='blame'")
        blame = ctx.git_ops.blame(
            params.path,
            min_line=params.start_line,
            max_line=params.end_line,
        )
        blame_dict = asdict(blame)
        lines = blame_dict.pop("lines", [])

        start_idx = 0
        if params.cursor:
            with contextlib.suppress(ValueError):
                start_idx = int(params.cursor)

        end_idx = start_idx + params.limit
        page = lines[start_idx:end_idx]
        has_more = end_idx < len(lines)

        pagination: dict[str, Any] = {}
        if has_more:
            pagination["next_cursor"] = str(end_idx)
            pagination["total_estimate"] = len(lines)

        return {
            "results": page,
            "pagination": pagination,
            **blame_dict,
            "summary": f"{len(page)} lines from {params.path}",
        }

    raise ValueError(f"Unknown action: {params.action}")


@registry.register("git_history", "Amend, cherry-pick, or revert commits", GitHistoryParams)
async def git_history(ctx: AppContext, params: GitHistoryParams) -> dict[str, Any]:
    """Modify commit history."""
    if params.action == "amend":
        sha = ctx.git_ops.amend(message=params.message)
        return {"oid": sha, "short_oid": sha[:7], "summary": f"amended to {sha[:7]}"}

    elif params.action == "cherrypick":
        if not params.commit:
            raise ValueError("commit required for action='cherrypick'")
        result = ctx.git_ops.cherrypick(params.commit)
        res = asdict(result)
        res["summary"] = f"cherry-picked {params.commit[:7]}"
        return res

    elif params.action == "revert":
        if not params.commit:
            raise ValueError("commit required for action='revert'")
        result = ctx.git_ops.revert(params.commit)
        res = asdict(result)
        res["summary"] = f"reverted {params.commit[:7]}"
        return res

    raise ValueError(f"Unknown action: {params.action}")


@registry.register("git_submodule", "Manage submodules", GitSubmoduleParams)
async def git_submodule(ctx: AppContext, params: GitSubmoduleParams) -> dict[str, Any]:
    """List, add, update, init, or remove submodules."""
    if params.action == "list":
        submodules = ctx.git_ops.submodules()
        return {
            "submodules": [asdict(s) for s in submodules],
            "summary": f"{len(submodules)} submodules",
        }

    elif params.action == "add":
        if not params.url or not params.path:
            raise ValueError("url and path required for action='add'")
        sm = ctx.git_ops.submodule_add(params.url, params.path, params.branch)
        result = asdict(sm)
        result["summary"] = f"added submodule at {params.path}"
        return result

    elif params.action == "update":
        upd_result = ctx.git_ops.submodule_update(params.paths, params.recursive, params.init)
        res = asdict(upd_result)
        res["summary"] = "submodules updated"
        return res

    elif params.action == "init":
        paths = ctx.git_ops.submodule_init(params.paths)
        return {"initialized": paths, "summary": f"initialized {len(paths)} submodules"}

    elif params.action == "remove":
        if not params.path:
            raise ValueError("path required for action='remove'")
        ctx.git_ops.submodule_remove(params.path)
        return {"removed": params.path, "summary": f"removed submodule {params.path}"}

    raise ValueError(f"Unknown action: {params.action}")


@registry.register("git_worktree", "Manage worktrees", GitWorktreeParams)
async def git_worktree(ctx: AppContext, params: GitWorktreeParams) -> dict[str, Any]:
    """List, add, remove, lock, unlock, or prune worktrees."""
    if params.action == "list":
        worktrees = ctx.git_ops.worktrees()
        return {
            "worktrees": [asdict(w) for w in worktrees],
            "summary": f"{len(worktrees)} worktrees",
        }

    elif params.action == "add":
        if not params.path or not params.ref:
            raise ValueError("path and ref required for action='add'")
        ctx.git_ops.worktree_add(Path(params.path), params.ref)
        return {
            "created": params.path,
            "ref": params.ref,
            "summary": f"added worktree at {params.path}",
        }

    elif params.action == "remove":
        if not params.name:
            raise ValueError("name required for action='remove'")
        ctx.git_ops.worktree_remove(params.name, params.force)
        return {"removed": params.name, "summary": f"removed worktree {params.name}"}

    elif params.action == "lock":
        if not params.name:
            raise ValueError("name required for action='lock'")
        ctx.git_ops.worktree_lock(params.name, params.reason)
        return {"locked": params.name, "summary": f"locked worktree {params.name}"}

    elif params.action == "unlock":
        if not params.name:
            raise ValueError("name required for action='unlock'")
        ctx.git_ops.worktree_unlock(params.name)
        return {"unlocked": params.name, "summary": f"unlocked worktree {params.name}"}

    elif params.action == "prune":
        pruned = ctx.git_ops.worktree_prune()
        return {
            "pruned": pruned,
            "summary": f"pruned {len(pruned)} worktrees" if pruned else "nothing to prune",
        }

    raise ValueError(f"Unknown action: {params.action}")
