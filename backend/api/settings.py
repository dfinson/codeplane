"""Settings management endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException

from backend.config import (
    TowerConfig,
    load_config,
    register_repo,
    unregister_repo,
)
from backend.models.api_schemas import (
    GlobalConfigResponse,
    RegisterRepoRequest,
    RegisterRepoResponse,
    RepoListResponse,
    UpdateGlobalConfigRequest,
)
from backend.services.git_service import GitError, GitService

router = APIRouter(tags=["settings"])


def _get_config() -> TowerConfig:
    return load_config()


def _get_git_service(config: Annotated[TowerConfig, Depends(_get_config)]) -> GitService:
    return GitService(config)


@router.get("/settings/global", response_model=GlobalConfigResponse)
async def get_global_config() -> GlobalConfigResponse:
    """Get current global config as YAML."""
    from backend.config import DEFAULT_CONFIG_PATH

    if DEFAULT_CONFIG_PATH.exists():
        return GlobalConfigResponse(config_yaml=DEFAULT_CONFIG_PATH.read_text())
    return GlobalConfigResponse(config_yaml="")


@router.put("/settings/global", response_model=GlobalConfigResponse)
async def update_global_config(body: UpdateGlobalConfigRequest) -> GlobalConfigResponse:
    """Update global config from YAML string."""
    import yaml

    from backend.config import DEFAULT_CONFIG_PATH

    # Validate YAML
    try:
        yaml.safe_load(body.config_yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc

    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFIG_PATH.write_text(body.config_yaml)
    return GlobalConfigResponse(config_yaml=body.config_yaml)


@router.get("/settings/repos", response_model=RepoListResponse)
async def list_repos(
    config: Annotated[TowerConfig, Depends(_get_config)],
) -> RepoListResponse:
    """List registered repository paths."""
    return RepoListResponse(items=config.repos)


@router.post("/settings/repos", response_model=RegisterRepoResponse, status_code=201)
async def register_repo_endpoint(
    body: RegisterRepoRequest,
    config: Annotated[TowerConfig, Depends(_get_config)],
    git: Annotated[GitService, Depends(_get_git_service)],
) -> RegisterRepoResponse:
    """Register a repository (local path or remote URL)."""
    source = body.source

    if GitService.is_remote_url(source):
        # Clone remote repo
        clone_dir = GitService.derive_clone_dir(source, config.repos_base_dir)
        if Path(clone_dir).exists():
            raise HTTPException(
                status_code=409,
                detail=f"Clone directory already exists: {clone_dir}",
            )
        try:
            cloned_path = await git.clone_repo(source, clone_dir)
        except GitError as exc:
            raise HTTPException(status_code=400, detail=f"Clone failed: {exc}") from exc
        register_repo(config, cloned_path)
        return RegisterRepoResponse(path=cloned_path, source=source, cloned=True)

    # Local path
    resolved = str(Path(source).expanduser().resolve())
    is_valid = await git.validate_repo(resolved)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Not a valid git repository: {source}",
        )
    register_repo(config, resolved)
    return RegisterRepoResponse(path=resolved, source=source, cloned=False)


@router.delete("/settings/repos/{repo_path:path}", status_code=204)
async def unregister_repo_endpoint(
    repo_path: str,
    config: Annotated[TowerConfig, Depends(_get_config)],
) -> None:
    """Remove a repository from the allowlist."""
    try:
        unregister_repo(config, repo_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/settings/cleanup-worktrees")
async def cleanup_worktrees(
    config: Annotated[TowerConfig, Depends(_get_config)],
    git: Annotated[GitService, Depends(_get_git_service)],
) -> dict[str, int]:
    """Clean up completed job worktrees for all registered repos."""
    total = 0
    for repo in config.repos:
        try:
            count = await git.cleanup_worktrees(repo)
            total += count
        except GitError:
            structlog.get_logger().warning("cleanup_worktrees_failed", repo=repo)
    return {"removed": total}
