"""Resolve recipe workspaces without overwriting or implicitly merging code."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from backend.models.domain import StateConflictError
from backend.models.schemas.recipes import RecipeCodeReference, RecipeResolvedWorkspace

if TYPE_CHECKING:
    from backend.models.domain import Job
    from backend.models.schemas.recipes import RecipeInputReceipt, RecipeRunResponse, RecipeStepInstanceResponse
    from backend.services.git.git_service import GitService
    from backend.services.runtime.execution_control import ExecutionControl


async def prepare_workspace(
    git: GitService,
    job: Job,
    run: RecipeRunResponse,
    instance: RecipeStepInstanceResponse,
    inputs: dict[str, RecipeInputReceipt],
    *,
    preparation_control: ExecutionControl,
    parent_workspace: RecipeResolvedWorkspace | None = None,
) -> RecipeResolvedWorkspace:
    if run.code_ref is None:
        raise StateConflictError("The run has no pinned starting code.")
    policy = instance.definition.workspace
    source = (
        RecipeCodeReference(workspace=parent_workspace.path, commit=parent_workspace.code_ref)
        if parent_workspace
        else run.code_ref
    )
    root_path = parent_workspace.path if parent_workspace else job.execution_workspace
    if policy.source_input is not None:
        receipt = inputs.get(policy.source_input)
        if receipt is None or not receipt.present:
            raise StateConflictError("The required workspace code input is unavailable.")
        source = RecipeCodeReference.model_validate(receipt.value)
    if policy.mode == "none":
        return RecipeResolvedWorkspace(path=str(Path(root_path).resolve()), code_ref=source.commit, policy=policy)
    if policy.mode == "mutable" and policy.key == "job":
        path = str(Path(root_path).resolve())
        current = await git.capture_worktree_commit(
            cwd=path,
            snapshot_ref=f"refs/codeplane/recipe-inputs/{instance.id}-{uuid4().hex}",
        )
        if policy.source_input is not None and (
            await git.rev_parse(f"{current}^{{tree}}", cwd=path)
            != await git.rev_parse(f"{source.commit}^{{tree}}", cwd=path)
        ):
            raise StateConflictError(
                "The mutable workspace differs from its exact code input. Reconcile it explicitly."
            )
        return RecipeResolvedWorkspace(path=path, code_ref=current, policy=policy)
    # A receipt's workspace is provenance, not permission to execute in that path.
    # Resolve its commit through the owning repository into an app-owned worktree.
    name = f"recipe-{instance.id}-v{instance.version}"
    if policy.mode == "snapshot":
        key = hashlib.sha256(f"{policy.key}:{source.commit}".encode()).hexdigest()[:16]
        name = f"recipe-{run.id}-{key}"
    path = git.recipe_worktree_path(job.execution_repo, name)

    async def prepare() -> RecipeResolvedWorkspace:
        if Path(path).exists():
            if (
                policy.mode != "snapshot"
                or await git.get_current_branch(cwd=path) != f"codeplane/recipe/{name}"
                or await git.rev_parse("HEAD", cwd=path) != source.commit
                or await git.run_git("status", "--porcelain", cwd=path)
            ):
                raise StateConflictError("The prepared recipe worktree differs from its pinned snapshot.")
        else:
            await git.create_worktree(job.execution_repo, name, source.commit, f"codeplane/recipe/{name}")
        return RecipeResolvedWorkspace(path=str(Path(path).resolve()), code_ref=source.commit, policy=policy)

    if policy.mode == "snapshot":
        async with preparation_control.hold(f"recipe-snapshot:{Path(path).resolve()}"):
            return await prepare()
    return await prepare()
