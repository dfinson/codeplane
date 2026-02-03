"""Git MCP tools - git.* handlers."""

from __future__ import annotations

import contextlib
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from codeplane.config.constants import GIT_BLAME_MAX, GIT_LOG_MAX
from codeplane.git._internal.hooks import run_hook
from codeplane.mcp.errors import HookFailedError
from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class GitStatusParams(BaseParams):
    """Parameters for git.status."""

    paths: list[str] | None = None


class GitDiffParams(BaseParams):
    """Parameters for git.diff."""

    base_ref: str | None = None
    target_ref: str | None = None
    staged: bool = False


class GitCommitParams(BaseParams):
    """Parameters for git.commit."""

    message: str
    paths: list[str] | None = None
    allow_empty: bool = False


class GitLogParams(BaseParams):
    """Parameters for git.log."""

    ref: str = "HEAD"
    limit: int = Field(default=50, le=GIT_LOG_MAX)
    cursor: str | None = None
    since: str | None = None
    until: str | None = None
    paths: list[str] | None = None


class GitBranchCreateParams(BaseParams):
    """Parameters for git.branch_create."""

    branch_name: str
    ref: str = "HEAD"


class GitCheckoutParams(BaseParams):
    """Parameters for git.checkout."""

    ref: str
    create: bool = False


class GitDeleteBranchParams(BaseParams):
    """Parameters for git.branch_delete."""

    branch_name: str
    force: bool = False


class GitResetParams(BaseParams):
    """Parameters for git.reset."""

    ref: str
    mode: str = "mixed"


class GitMergeParams(BaseParams):
    """Parameters for git.merge."""

    ref: str


class GitStageParams(BaseParams):
    """Parameters for git.stage."""

    paths: list[str]


class GitUnstageParams(BaseParams):
    """Parameters for git.unstage."""

    paths: list[str]


class GitDiscardParams(BaseParams):
    """Parameters for git.discard."""

    paths: list[str]


class GitAmendParams(BaseParams):
    """Parameters for git.amend."""

    message: str | None = None


class GitBlameParams(BaseParams):
    """Parameters for git.blame."""

    path: str
    start_line: int | None = None
    end_line: int | None = None
    cursor: str | None = None
    limit: int = Field(default=100, le=GIT_BLAME_MAX)


class GitShowParams(BaseParams):
    """Parameters for git.show."""

    ref: str = "HEAD"


class EmptyParams(BaseParams):
    """Empty params for tools with no arguments."""

    pass


class GitStashPushParams(BaseParams):
    """Parameters for git.stash_push."""

    message: str | None = None
    include_untracked: bool = False


class GitStashPopParams(BaseParams):
    """Parameters for git.stash_pop."""

    index: int = 0


class GitRebasePlanParams(BaseParams):
    """Parameters for git.rebase_plan."""

    upstream: str
    onto: str | None = None


class GitCherrypickParams(BaseParams):
    """Parameters for git.cherrypick."""

    commit: str


class GitRevertParams(BaseParams):
    """Parameters for git.revert."""

    commit: str


class GitFetchParams(BaseParams):
    """Parameters for git.fetch."""

    remote: str = "origin"


class GitPushParams(BaseParams):
    """Parameters for git.push."""

    remote: str = "origin"
    force: bool = False


class GitPullParams(BaseParams):
    """Parameters for git.pull."""

    remote: str = "origin"


class GitSubmoduleAddParams(BaseParams):
    """Parameters for git.submodule_add."""

    url: str
    path: str
    branch: str | None = None


class GitSubmoduleUpdateParams(BaseParams):
    """Parameters for git.submodule_update."""

    paths: list[str] | None = None
    recursive: bool = False
    init: bool = True


class GitSubmoduleInitParams(BaseParams):
    """Parameters for git.submodule_init."""

    paths: list[str] | None = None


class GitSubmoduleRemoveParams(BaseParams):
    """Parameters for git.submodule_remove."""

    path: str


class GitWorktreeAddParams(BaseParams):
    """Parameters for git.worktree_add."""

    path: str
    ref: str


class GitWorktreeRemoveParams(BaseParams):
    """Parameters for git.worktree_remove."""

    worktree_name: str
    force: bool = False


class GitWorktreeLockParams(BaseParams):
    """Parameters for git.worktree_lock."""

    worktree_name: str
    reason: str | None = None


class GitWorktreeUnlockParams(BaseParams):
    """Parameters for git.worktree_unlock."""

    worktree_name: str


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_status(branch: str | None, files: dict[str, int], is_clean: bool, state: int) -> str:
    """Generate summary for git.status."""
    if is_clean:
        return f"clean, branch: {branch or 'detached'}"

    # Count by status
    modified = sum(1 for s in files.values() if s in (256, 512))  # WT_MODIFIED, WT_NEW
    staged = sum(1 for s in files.values() if s in (1, 2, 4))  # INDEX_NEW, INDEX_MODIFIED, etc
    conflicted = sum(1 for s in files.values() if s >= 4096)  # CONFLICTED

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
    """Generate summary for git.diff."""
    if files_changed == 0:
        return "no changes" if not staged else "no staged changes"
    prefix = "staged: " if staged else ""
    return f"{prefix}{files_changed} files changed (+{additions}/-{deletions})"


def _summarize_commit(sha: str, message: str, stats: dict[str, int] | None = None) -> str:
    """Generate summary for git.commit."""
    short_sha = sha[:7]
    # Truncate message to first line, max 50 chars
    first_line = message.split("\n")[0][:50]
    if len(message.split("\n")[0]) > 50:
        first_line += "..."

    if stats:
        return f'{short_sha} "{first_line}" (+{stats.get("insertions", 0)}/-{stats.get("deletions", 0)})'
    return f'{short_sha} "{first_line}"'


def _summarize_log(count: int, has_more: bool) -> str:
    """Generate summary for git.log."""
    more = " (more available)" if has_more else ""
    return f"{count} commits{more}"


def _summarize_branches(count: int, current: str | None) -> str:
    """Generate summary for git.branches."""
    if current:
        return f"{count} branches, current: {current}"
    return f"{count} branches"


def _summarize_paths(action: str, paths: list[str]) -> str:
    """Generate summary for path-based operations."""
    if len(paths) == 1:
        return f"{action} {paths[0]}"
    if len(paths) <= 3:
        return f"{action} {len(paths)} files ({', '.join(paths)})"
    return f"{action} {len(paths)} files ({paths[0]}, {paths[1]}, +{len(paths) - 2} more)"


# =============================================================================
# Tool Handlers
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
        base=params.base_ref,
        target=params.target_ref,
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

    # Run pre-commit hook before committing
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
        limit=params.limit + 1,  # Fetch one extra to detect if more exist
        since=params.since,
        until=params.until,
        paths=params.paths,
    )

    # Check if there are more results
    has_more = len(commits) > params.limit
    if has_more:
        commits = commits[: params.limit]

    pagination: dict[str, Any] = {}
    if has_more and commits:
        # Use last commit SHA as cursor
        pagination["next_cursor"] = commits[-1].sha

    return {
        "results": [asdict(c) for c in commits],
        "pagination": pagination,
        "summary": _summarize_log(len(commits), has_more),
    }


@registry.register("git_branches", "List branches", EmptyParams)
async def git_branches(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List branches."""
    branches = ctx.git_ops.branches(include_remote=True)
    current = ctx.git_ops.current_branch()
    return {
        "branches": [asdict(b) for b in branches],
        "summary": _summarize_branches(len(branches), current),
    }


@registry.register("git_tags", "List tags", EmptyParams)
async def git_tags(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List tags."""
    tags = ctx.git_ops.tags()
    return {
        "tags": [asdict(t) for t in tags],
        "summary": f"{len(tags)} tags",
    }


@registry.register("git_remotes", "List remotes", EmptyParams)
async def git_remotes(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List remotes."""
    remotes = ctx.git_ops.remotes()
    return {
        "remotes": [asdict(r) for r in remotes],
        "summary": f"{len(remotes)} remotes",
    }


@registry.register("git_stage", "Stage files", GitStageParams)
async def git_stage(ctx: AppContext, params: GitStageParams) -> dict[str, Any]:
    """Stage files."""
    ctx.git_ops.stage(params.paths)
    return {
        "staged": params.paths,
        "summary": _summarize_paths("staged", params.paths),
    }


@registry.register("git_stage_all", "Stage all changed files", EmptyParams)
async def git_stage_all(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """Stage all changed files."""
    staged = ctx.git_ops.stage_all()
    return {
        "staged": staged,
        "summary": _summarize_paths("staged", staged) if staged else "nothing to stage",
    }


@registry.register("git_unstage", "Unstage files", GitUnstageParams)
async def git_unstage(ctx: AppContext, params: GitUnstageParams) -> dict[str, Any]:
    """Unstage files."""
    ctx.git_ops.unstage(params.paths)
    return {
        "unstaged": params.paths,
        "summary": _summarize_paths("unstaged", params.paths),
    }


@registry.register("git_discard", "Discard working tree changes", GitDiscardParams)
async def git_discard(ctx: AppContext, params: GitDiscardParams) -> dict[str, Any]:
    """Discard changes."""
    ctx.git_ops.discard(params.paths)
    return {
        "discarded": params.paths,
        "summary": _summarize_paths("discarded", params.paths),
    }


@registry.register("git_amend", "Amend last commit", GitAmendParams)
async def git_amend(ctx: AppContext, params: GitAmendParams) -> dict[str, Any]:
    """Amend commit."""
    sha = ctx.git_ops.amend(message=params.message)
    return {
        "oid": sha,
        "short_oid": sha[:7],
        "summary": f"amended to {sha[:7]}",
    }


@registry.register("git_branch_create", "Create a new branch", GitBranchCreateParams)
async def git_branch_create(ctx: AppContext, params: GitBranchCreateParams) -> dict[str, Any]:
    """Create branch."""
    branch = ctx.git_ops.create_branch(params.branch_name, ref=params.ref)
    result = asdict(branch)
    result["summary"] = f"created branch {params.branch_name}"
    return result


@registry.register("git_checkout", "Checkout a ref", GitCheckoutParams)
async def git_checkout(ctx: AppContext, params: GitCheckoutParams) -> dict[str, Any]:
    """Checkout ref."""
    ctx.git_ops.checkout(params.ref, create=params.create)
    action = "created and checked out" if params.create else "checked out"
    return {
        "checked_out": params.ref,
        "summary": f"{action} {params.ref}",
    }


@registry.register("git_branch_delete", "Delete a branch", GitDeleteBranchParams)
async def git_branch_delete(ctx: AppContext, params: GitDeleteBranchParams) -> dict[str, Any]:
    """Delete branch."""
    ctx.git_ops.delete_branch(params.branch_name, force=params.force)
    return {
        "deleted": params.branch_name,
        "summary": f"deleted branch {params.branch_name}",
    }


@registry.register("git_reset", "Reset HEAD to a ref", GitResetParams)
async def git_reset(ctx: AppContext, params: GitResetParams) -> dict[str, Any]:
    """Reset HEAD."""
    ctx.git_ops.reset(params.ref, mode=params.mode)
    return {
        "reset_to": params.ref,
        "mode": params.mode,
        "summary": f"reset ({params.mode}) to {params.ref[:12] if len(params.ref) > 12 else params.ref}",
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


@registry.register("git_blame", "Get line authorship", GitBlameParams)
async def git_blame(ctx: AppContext, params: GitBlameParams) -> dict[str, Any]:
    """Get blame."""
    blame = ctx.git_ops.blame(
        params.path,
        min_line=params.start_line,
        max_line=params.end_line,
    )
    blame_dict = asdict(blame)
    lines = blame_dict.pop("lines", [])

    # Apply limit and cursor logic
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


@registry.register("git_show", "Show commit details", GitShowParams)
async def git_show(ctx: AppContext, params: GitShowParams) -> dict[str, Any]:
    """Show commit."""
    commit = ctx.git_ops.show(ref=params.ref)
    result = asdict(commit)
    result["summary"] = f"{commit.sha[:7]}: {commit.message.split(chr(10))[0][:50]}"
    return result


@registry.register("git_stash_push", "Stash changes", GitStashPushParams)
async def git_stash_push(ctx: AppContext, params: GitStashPushParams) -> dict[str, Any]:
    """Push to stash."""
    sha = ctx.git_ops.stash_push(
        message=params.message,
        include_untracked=params.include_untracked,
    )
    msg = f': "{params.message}"' if params.message else ""
    return {
        "stash_commit": sha,
        "summary": f"stashed{msg}",
    }


@registry.register("git_stash_pop", "Pop from stash", GitStashPopParams)
async def git_stash_pop(ctx: AppContext, params: GitStashPopParams) -> dict[str, Any]:
    """Pop from stash."""
    ctx.git_ops.stash_pop(index=params.index)
    return {
        "popped": params.index,
        "summary": f"popped stash@{{{params.index}}}",
    }


@registry.register("git_stash_list", "List stash entries", EmptyParams)
async def git_stash_list(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List stash."""
    entries = ctx.git_ops.stash_list()
    return {
        "entries": [asdict(e) for e in entries],
        "summary": f"{len(entries)} stash entries",
    }


@registry.register("git_rebase_plan", "Plan a rebase", GitRebasePlanParams)
async def git_rebase_plan(ctx: AppContext, params: GitRebasePlanParams) -> dict[str, Any]:
    """Plan rebase."""
    plan = ctx.git_ops.rebase_plan(params.upstream, onto=params.onto)
    result = asdict(plan)
    onto_str = f" onto {params.onto}" if params.onto else ""
    result["summary"] = f"rebasing {len(plan.steps)} commits{onto_str}"
    return result


@registry.register("git_rebase_continue", "Continue rebase", EmptyParams)
async def git_rebase_continue(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """Continue rebase."""
    result = ctx.git_ops.rebase_continue()
    res = asdict(result)
    res["summary"] = "rebase continued"
    return res


@registry.register("git_rebase_abort", "Abort rebase", EmptyParams)
async def git_rebase_abort(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """Abort rebase."""
    ctx.git_ops.rebase_abort()
    return {
        "aborted": True,
        "summary": "rebase aborted",
    }


@registry.register("git_rebase_skip", "Skip current rebase commit", EmptyParams)
async def git_rebase_skip(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """Skip rebase commit."""
    result = ctx.git_ops.rebase_skip()
    res = asdict(result)
    res["summary"] = "skipped commit"
    return res


@registry.register("git_cherrypick", "Cherry-pick a commit", GitCherrypickParams)
async def git_cherrypick(ctx: AppContext, params: GitCherrypickParams) -> dict[str, Any]:
    """Cherry-pick commit."""
    result = ctx.git_ops.cherrypick(params.commit)
    res = asdict(result)
    res["summary"] = f"cherry-picked {params.commit[:7]}"
    return res


@registry.register("git_revert", "Revert a commit", GitRevertParams)
async def git_revert(ctx: AppContext, params: GitRevertParams) -> dict[str, Any]:
    """Revert commit."""
    result = ctx.git_ops.revert(params.commit)
    res = asdict(result)
    res["summary"] = f"reverted {params.commit[:7]}"
    return res


@registry.register("git_fetch", "Fetch from remote", GitFetchParams)
async def git_fetch(ctx: AppContext, params: GitFetchParams) -> dict[str, Any]:
    """Fetch from remote."""
    ctx.git_ops.fetch(remote=params.remote)
    return {
        "fetched": params.remote,
        "summary": f"fetched from {params.remote}",
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


@registry.register("git_submodules", "List submodules", EmptyParams)
async def git_submodules(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List submodules."""
    submodules = ctx.git_ops.submodules()
    return {
        "submodules": [asdict(s) for s in submodules],
        "summary": f"{len(submodules)} submodules",
    }


@registry.register("git_submodule_add", "Add a submodule", GitSubmoduleAddParams)
async def git_submodule_add(ctx: AppContext, params: GitSubmoduleAddParams) -> dict[str, Any]:
    """Add submodule."""
    sm = ctx.git_ops.submodule_add(params.url, params.path, params.branch)
    result = asdict(sm)
    result["summary"] = f"added submodule at {params.path}"
    return result


@registry.register("git_submodule_update", "Update submodules", GitSubmoduleUpdateParams)
async def git_submodule_update(ctx: AppContext, params: GitSubmoduleUpdateParams) -> dict[str, Any]:
    """Update submodules."""
    result = ctx.git_ops.submodule_update(params.paths, params.recursive, params.init)
    res = asdict(result)
    res["summary"] = "submodules updated"
    return res


@registry.register("git_submodule_init", "Initialize submodules", GitSubmoduleInitParams)
async def git_submodule_init(ctx: AppContext, params: GitSubmoduleInitParams) -> dict[str, Any]:
    """Init submodules."""
    paths = ctx.git_ops.submodule_init(params.paths)
    return {
        "initialized": paths,
        "summary": f"initialized {len(paths)} submodules",
    }


@registry.register("git_submodule_remove", "Remove a submodule", GitSubmoduleRemoveParams)
async def git_submodule_remove(ctx: AppContext, params: GitSubmoduleRemoveParams) -> dict[str, Any]:
    """Remove submodule."""
    ctx.git_ops.submodule_remove(params.path)
    return {
        "removed": params.path,
        "summary": f"removed submodule {params.path}",
    }


@registry.register("git_worktrees", "List worktrees", EmptyParams)
async def git_worktrees(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List worktrees."""
    worktrees = ctx.git_ops.worktrees()
    return {
        "worktrees": [asdict(w) for w in worktrees],
        "summary": f"{len(worktrees)} worktrees",
    }


@registry.register("git_worktree_add", "Add a worktree", GitWorktreeAddParams)
async def git_worktree_add(ctx: AppContext, params: GitWorktreeAddParams) -> dict[str, Any]:
    """Add worktree."""
    from pathlib import Path

    ctx.git_ops.worktree_add(Path(params.path), params.ref)
    return {
        "created": params.path,
        "ref": params.ref,
        "summary": f"added worktree at {params.path}",
    }


@registry.register("git_worktree_remove", "Remove a worktree", GitWorktreeRemoveParams)
async def git_worktree_remove(ctx: AppContext, params: GitWorktreeRemoveParams) -> dict[str, Any]:
    """Remove worktree."""
    ctx.git_ops.worktree_remove(params.worktree_name, params.force)
    return {
        "removed": params.worktree_name,
        "summary": f"removed worktree {params.worktree_name}",
    }


@registry.register("git_worktree_lock", "Lock a worktree", GitWorktreeLockParams)
async def git_worktree_lock(ctx: AppContext, params: GitWorktreeLockParams) -> dict[str, Any]:
    """Lock worktree."""
    ctx.git_ops.worktree_lock(params.worktree_name, params.reason)
    return {
        "locked": params.worktree_name,
        "summary": f"locked worktree {params.worktree_name}",
    }


@registry.register("git_worktree_unlock", "Unlock a worktree", GitWorktreeUnlockParams)
async def git_worktree_unlock(ctx: AppContext, params: GitWorktreeUnlockParams) -> dict[str, Any]:
    """Unlock worktree."""
    ctx.git_ops.worktree_unlock(params.worktree_name)
    return {
        "unlocked": params.worktree_name,
        "summary": f"unlocked worktree {params.worktree_name}",
    }


@registry.register("git_worktree_prune", "Prune worktrees", EmptyParams)
async def git_worktree_prune(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """Prune worktrees."""
    pruned = ctx.git_ops.worktree_prune()
    return {
        "pruned": pruned,
        "summary": f"pruned {len(pruned)} worktrees" if pruned else "nothing to prune",
    }
