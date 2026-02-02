"""Git MCP tools - git_* handlers."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from codeplane.mcp.registry import registry

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class GitStatusParams(BaseModel):
    """Parameters for git_status."""

    paths: list[str] | None = None


class GitDiffParams(BaseModel):
    """Parameters for git_diff."""

    base: str | None = None
    target: str | None = None
    staged: bool = False


class GitCommitParams(BaseModel):
    """Parameters for git_commit."""

    message: str
    paths: list[str] | None = None
    allow_empty: bool = False


class GitLogParams(BaseModel):
    """Parameters for git_log."""

    ref: str = "HEAD"
    limit: int = Field(default=50, le=100)


class GitBranchCreateParams(BaseModel):
    """Parameters for git_create_branch."""

    name: str
    ref: str = "HEAD"


class GitCheckoutParams(BaseModel):
    """Parameters for git_checkout."""

    ref: str
    create: bool = False


class GitDeleteBranchParams(BaseModel):
    """Parameters for git_delete_branch."""

    name: str
    force: bool = False


class GitResetParams(BaseModel):
    """Parameters for git_reset."""

    ref: str
    mode: str = "mixed"


class GitMergeParams(BaseModel):
    """Parameters for git_merge."""

    ref: str


class GitStageParams(BaseModel):
    """Parameters for git_stage."""

    paths: list[str]


class GitUnstageParams(BaseModel):
    """Parameters for git_unstage."""

    paths: list[str]


class GitDiscardParams(BaseModel):
    """Parameters for git_discard."""

    paths: list[str]


class GitAmendParams(BaseModel):
    """Parameters for git_amend."""

    message: str | None = None


class GitBlameParams(BaseModel):
    """Parameters for git_blame."""

    path: str


class GitShowParams(BaseModel):
    """Parameters for git_show."""

    ref: str = "HEAD"


class EmptyParams(BaseModel):
    """Empty params for tools with no arguments."""

    pass


class GitStashPushParams(BaseModel):
    """Parameters for git_stash_push."""

    message: str | None = None
    include_untracked: bool = False


class GitStashPopParams(BaseModel):
    """Parameters for git_stash_pop."""

    index: int = 0


class GitRebasePlanParams(BaseModel):
    """Parameters for git_rebase_plan."""

    upstream: str
    onto: str | None = None


class GitCherrypickParams(BaseModel):
    """Parameters for git_cherrypick."""

    commit: str


class GitRevertParams(BaseModel):
    """Parameters for git_revert."""

    commit: str


class GitFetchParams(BaseModel):
    """Parameters for git_fetch."""

    remote: str = "origin"


class GitPushParams(BaseModel):
    """Parameters for git_push."""

    remote: str = "origin"
    force: bool = False


class GitPullParams(BaseModel):
    """Parameters for git_pull."""

    remote: str = "origin"


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
    return asdict(diff)


@registry.register("git_commit", "Create a commit", GitCommitParams)
async def git_commit(ctx: AppContext, params: GitCommitParams) -> dict[str, Any]:
    """Create commit."""
    if params.paths:
        ctx.git_ops.stage(params.paths)
    sha = ctx.git_ops.commit(params.message, allow_empty=params.allow_empty)
    return {"oid": sha, "short_oid": sha[:7]}


@registry.register("git_log", "Get commit history", GitLogParams)
async def git_log(ctx: AppContext, params: GitLogParams) -> dict[str, Any]:
    """Get commit log."""
    commits = ctx.git_ops.log(ref=params.ref, limit=params.limit)
    return {"commits": [asdict(c) for c in commits]}


@registry.register("git_branches", "List branches", EmptyParams)
async def git_branches(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List branches."""
    branches = ctx.git_ops.branches(include_remote=True)
    return {"branches": [asdict(b) for b in branches]}


@registry.register("git_tags", "List tags", EmptyParams)
async def git_tags(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List tags."""
    tags = ctx.git_ops.tags()
    return {"tags": [asdict(t) for t in tags]}


@registry.register("git_remotes", "List remotes", EmptyParams)
async def git_remotes(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List remotes."""
    remotes = ctx.git_ops.remotes()
    return {"remotes": [asdict(r) for r in remotes]}


@registry.register("git_stage", "Stage files", GitStageParams)
async def git_stage(ctx: AppContext, params: GitStageParams) -> dict[str, Any]:
    """Stage files."""
    ctx.git_ops.stage(params.paths)
    return {"staged": params.paths}


@registry.register("git_unstage", "Unstage files", GitUnstageParams)
async def git_unstage(ctx: AppContext, params: GitUnstageParams) -> dict[str, Any]:
    """Unstage files."""
    ctx.git_ops.unstage(params.paths)
    return {"unstaged": params.paths}


@registry.register("git_discard", "Discard working tree changes", GitDiscardParams)
async def git_discard(ctx: AppContext, params: GitDiscardParams) -> dict[str, Any]:
    """Discard changes."""
    ctx.git_ops.discard(params.paths)
    return {"discarded": params.paths}


@registry.register("git_amend", "Amend last commit", GitAmendParams)
async def git_amend(ctx: AppContext, params: GitAmendParams) -> dict[str, Any]:
    """Amend commit."""
    sha = ctx.git_ops.amend(message=params.message)
    return {"oid": sha, "short_oid": sha[:7]}


@registry.register("git_create_branch", "Create a new branch", GitBranchCreateParams)
async def git_create_branch(ctx: AppContext, params: GitBranchCreateParams) -> dict[str, Any]:
    """Create branch."""
    branch = ctx.git_ops.create_branch(params.name, ref=params.ref)
    return asdict(branch)


@registry.register("git_checkout", "Checkout a ref", GitCheckoutParams)
async def git_checkout(ctx: AppContext, params: GitCheckoutParams) -> dict[str, Any]:
    """Checkout ref."""
    ctx.git_ops.checkout(params.ref, create=params.create)
    return {"checked_out": params.ref}


@registry.register("git_delete_branch", "Delete a branch", GitDeleteBranchParams)
async def git_delete_branch(ctx: AppContext, params: GitDeleteBranchParams) -> dict[str, Any]:
    """Delete branch."""
    ctx.git_ops.delete_branch(params.name, force=params.force)
    return {"deleted": params.name}


@registry.register("git_reset", "Reset HEAD to a ref", GitResetParams)
async def git_reset(ctx: AppContext, params: GitResetParams) -> dict[str, Any]:
    """Reset HEAD."""
    ctx.git_ops.reset(params.ref, mode=params.mode)
    return {"reset_to": params.ref, "mode": params.mode}


@registry.register("git_merge", "Merge a branch", GitMergeParams)
async def git_merge(ctx: AppContext, params: GitMergeParams) -> dict[str, Any]:
    """Merge branch."""
    result = ctx.git_ops.merge(params.ref)
    return asdict(result)


@registry.register("git_blame", "Get line authorship", GitBlameParams)
async def git_blame(ctx: AppContext, params: GitBlameParams) -> dict[str, Any]:
    """Get blame."""
    blame = ctx.git_ops.blame(params.path)
    return asdict(blame)


@registry.register("git_show", "Show commit details", GitShowParams)
async def git_show(ctx: AppContext, params: GitShowParams) -> dict[str, Any]:
    """Show commit."""
    commit = ctx.git_ops.show(ref=params.ref)
    return asdict(commit)


@registry.register("git_stash_push", "Stash changes", GitStashPushParams)
async def git_stash_push(ctx: AppContext, params: GitStashPushParams) -> dict[str, Any]:
    """Push to stash."""
    sha = ctx.git_ops.stash_push(
        message=params.message,
        include_untracked=params.include_untracked,
    )
    return {"stash_commit": sha}


@registry.register("git_stash_pop", "Pop from stash", GitStashPopParams)
async def git_stash_pop(ctx: AppContext, params: GitStashPopParams) -> dict[str, Any]:
    """Pop from stash."""
    ctx.git_ops.stash_pop(index=params.index)
    return {"popped": params.index}


@registry.register("git_stash_list", "List stash entries", EmptyParams)
async def git_stash_list(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """List stash."""
    entries = ctx.git_ops.stash_list()
    return {"entries": [asdict(e) for e in entries]}


@registry.register("git_rebase_plan", "Plan a rebase", GitRebasePlanParams)
async def git_rebase_plan(ctx: AppContext, params: GitRebasePlanParams) -> dict[str, Any]:
    """Plan rebase."""
    plan = ctx.git_ops.rebase_plan(params.upstream, onto=params.onto)
    return asdict(plan)


@registry.register("git_rebase_continue", "Continue rebase", EmptyParams)
async def git_rebase_continue(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """Continue rebase."""
    result = ctx.git_ops.rebase_continue()
    return asdict(result)


@registry.register("git_rebase_abort", "Abort rebase", EmptyParams)
async def git_rebase_abort(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """Abort rebase."""
    ctx.git_ops.rebase_abort()
    return {"aborted": True}


@registry.register("git_rebase_skip", "Skip current rebase commit", EmptyParams)
async def git_rebase_skip(ctx: AppContext, _params: EmptyParams) -> dict[str, Any]:
    """Skip rebase commit."""
    result = ctx.git_ops.rebase_skip()
    return asdict(result)


@registry.register("git_cherrypick", "Cherry-pick a commit", GitCherrypickParams)
async def git_cherrypick(ctx: AppContext, params: GitCherrypickParams) -> dict[str, Any]:
    """Cherry-pick commit."""
    result = ctx.git_ops.cherrypick(params.commit)
    return asdict(result)


@registry.register("git_revert", "Revert a commit", GitRevertParams)
async def git_revert(ctx: AppContext, params: GitRevertParams) -> dict[str, Any]:
    """Revert commit."""
    result = ctx.git_ops.revert(params.commit)
    return asdict(result)


@registry.register("git_fetch", "Fetch from remote", GitFetchParams)
async def git_fetch(ctx: AppContext, params: GitFetchParams) -> dict[str, Any]:
    """Fetch from remote."""
    ctx.git_ops.fetch(remote=params.remote)
    return {"fetched": params.remote}


@registry.register("git_push", "Push to remote", GitPushParams)
async def git_push(ctx: AppContext, params: GitPushParams) -> dict[str, Any]:
    """Push to remote."""
    ctx.git_ops.push(remote=params.remote, force=params.force)
    return {"pushed": params.remote}


@registry.register("git_pull", "Pull from remote", GitPullParams)
async def git_pull(ctx: AppContext, params: GitPullParams) -> dict[str, Any]:
    """Pull from remote."""
    result = ctx.git_ops.pull(remote=params.remote)
    return asdict(result)
