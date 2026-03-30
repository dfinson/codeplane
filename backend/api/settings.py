"""Settings management endpoints."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Annotated

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, HTTPException

from backend.config import (
    CPLConfig,
    load_config,
    register_repo,
    save_config,
    unregister_repo,
)
from backend.models.api_schemas import (
    BrowseDirectoryResponse,
    BrowseEntry,
    CleanupWorktreesResponse,
    CreateRepoRequest,
    CreateRepoResponse,
    PlatformStatusListResponse,
    PlatformStatusResponse,
    RegisterRepoRequest,
    RegisterRepoResponse,
    RepoDetailResponse,
    RepoListResponse,
    SDKInfoResponse,
    SDKListResponse,
    SettingsResponse,
    UpdateSettingsRequest,
)
from backend.services.git_service import GitError, GitService
from backend.services.platform_adapter import PlatformRegistry, detect_platform
from backend.services.runtime_service import DEFAULT_SELF_REVIEW_PROMPT, DEFAULT_VERIFY_PROMPT

router = APIRouter(tags=["settings"], route_class=DishkaRoute)


def _get_config() -> CPLConfig:
    return load_config()


def _get_git_service(config: Annotated[CPLConfig, Depends(_get_config)]) -> GitService:
    return GitService(config)


def _config_to_response(config: CPLConfig) -> SettingsResponse:
    return SettingsResponse(
        max_concurrent_jobs=config.runtime.max_concurrent_jobs,
        permission_mode=config.runtime.permission_mode,
        auto_push=config.completion.auto_push,
        cleanup_worktree=config.completion.cleanup_worktree,
        delete_branch_after_merge=config.completion.delete_branch_after_merge,
        artifact_retention_days=config.retention.artifact_retention_days,
        max_artifact_size_mb=config.retention.max_artifact_size_mb,
        auto_archive_days=config.retention.auto_archive_days,
        verify=config.verification.verify,
        self_review=config.verification.self_review,
        max_turns=config.verification.max_turns,
        verify_prompt=config.verification.verify_prompt or DEFAULT_VERIFY_PROMPT,
        self_review_prompt=config.verification.self_review_prompt or DEFAULT_SELF_REVIEW_PROMPT,
    )


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(
    config: Annotated[CPLConfig, Depends(_get_config)],
) -> SettingsResponse:
    """Get current settings as structured data."""
    return _config_to_response(config)


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: UpdateSettingsRequest,
) -> SettingsResponse:
    """Update settings. Only provided fields are changed."""
    config = load_config()
    updates = body.model_dump(exclude_none=True)
    if "max_concurrent_jobs" in updates:
        config.runtime.max_concurrent_jobs = updates["max_concurrent_jobs"]
    if "permission_mode" in updates:
        config.runtime.permission_mode = str(updates["permission_mode"])
    if "auto_push" in updates:
        config.completion.auto_push = updates["auto_push"]
    if "cleanup_worktree" in updates:
        config.completion.cleanup_worktree = updates["cleanup_worktree"]
    if "delete_branch_after_merge" in updates:
        config.completion.delete_branch_after_merge = updates["delete_branch_after_merge"]
    if "artifact_retention_days" in updates:
        config.retention.artifact_retention_days = updates["artifact_retention_days"]
    if "max_artifact_size_mb" in updates:
        config.retention.max_artifact_size_mb = updates["max_artifact_size_mb"]
    if "auto_archive_days" in updates:
        config.retention.auto_archive_days = updates["auto_archive_days"]
    if "verify" in updates:
        config.verification.verify = updates["verify"]
    if "self_review" in updates:
        config.verification.self_review = updates["self_review"]
    if "max_turns" in updates:
        config.verification.max_turns = updates["max_turns"]
    if "verify_prompt" in updates:
        config.verification.verify_prompt = updates["verify_prompt"]
    if "self_review_prompt" in updates:
        config.verification.self_review_prompt = updates["self_review_prompt"]
    save_config(config)
    return _config_to_response(config)


@router.get("/settings/repos", response_model=RepoListResponse)
async def list_repos(
    config: Annotated[CPLConfig, Depends(_get_config)],
) -> RepoListResponse:
    """List registered repository paths."""
    return RepoListResponse(items=config.repos)


@router.get("/settings/repos/{repo_path:path}", response_model=RepoDetailResponse)
async def get_repo_detail(
    repo_path: str,
    config: Annotated[CPLConfig, Depends(_get_config)],
    git: Annotated[GitService, Depends(_get_git_service)],
) -> RepoDetailResponse:
    """Get detailed config for a single registered repository."""
    resolved = str(Path(repo_path).expanduser().resolve())
    if resolved not in config.repos:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_path}' is not registered.")

    origin_url: str | None = None
    base_branch: str | None = None
    current_branch: str | None = None
    with contextlib.suppress(GitError):
        raw_url = await git.get_origin_url(resolved)
        if raw_url:
            origin_url = GitService.strip_url_credentials(raw_url)
    with contextlib.suppress(GitError):
        base_branch = await git.get_default_branch(resolved)
    with contextlib.suppress(GitError):
        current_branch = await git.get_current_branch(cwd=resolved)

    return RepoDetailResponse(
        path=resolved,
        origin_url=origin_url,
        base_branch=base_branch,
        current_branch=current_branch,
        platform=detect_platform(origin_url),
    )


@router.post("/settings/repos", response_model=RegisterRepoResponse, status_code=201)
async def register_repo_endpoint(
    body: RegisterRepoRequest,
    config: Annotated[CPLConfig, Depends(_get_config)],
    git: Annotated[GitService, Depends(_get_git_service)],
) -> RegisterRepoResponse:
    """Register a repository (local path or remote URL)."""
    source = body.source

    if GitService.is_remote_url(source):
        if not body.clone_to:
            raise HTTPException(
                status_code=400,
                detail="clone_to path is required when registering a remote URL",
            )
        clone_dir = str(Path(body.clone_to).expanduser().resolve())
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


@router.post("/settings/repos/create", response_model=CreateRepoResponse, status_code=201)
async def create_repo_endpoint(
    body: CreateRepoRequest,
    config: Annotated[CPLConfig, Depends(_get_config)],
    git: Annotated[GitService, Depends(_get_git_service)],
) -> CreateRepoResponse:
    """Create a new git repository and register it."""
    resolved = Path(body.path).expanduser().resolve()
    if body.name:
        resolved = resolved / body.name

    if (resolved / ".git").is_dir():
        raise HTTPException(status_code=409, detail=f"A git repository already exists at {resolved}")

    try:
        repo_path = await git.init_repo(str(resolved))
    except GitError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to create repository: {exc}") from exc

    register_repo(config, repo_path)
    return CreateRepoResponse(path=repo_path, name=resolved.name)


@router.delete("/settings/repos/{repo_path:path}", status_code=204)
async def unregister_repo_endpoint(
    repo_path: str,
    config: Annotated[CPLConfig, Depends(_get_config)],
) -> None:
    """Remove a repository from the allowlist."""
    try:
        unregister_repo(config, repo_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/settings/cleanup-worktrees", response_model=CleanupWorktreesResponse)
async def cleanup_worktrees(
    config: Annotated[CPLConfig, Depends(_get_config)],
    git: Annotated[GitService, Depends(_get_git_service)],
) -> CleanupWorktreesResponse:
    """Clean up completed job worktrees for all registered repos."""
    total = 0
    for repo in config.repos:
        try:
            count = await git.cleanup_worktrees(repo)
            total += count
        except GitError:
            structlog.get_logger().warning("cleanup_worktrees_failed", repo=repo)
    return CleanupWorktreesResponse(removed=total)


@router.get("/settings/browse", response_model=BrowseDirectoryResponse)
async def browse_directories(
    path: str = "~",
) -> BrowseDirectoryResponse:
    """List directories at a given path for the repo browser.

    Returns subdirectories and indicates which are git repos.
    """
    try:
        base = Path(path).expanduser().resolve()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc

    if not base.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    # Security: don't traverse above user's home
    home = Path.home().resolve()
    if not str(base).startswith(str(home)) and base != home:
        raise HTTPException(status_code=403, detail="Access denied")

    entries: list[BrowseEntry] = []
    try:
        for item in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if item.name.startswith(".") or not item.is_dir():
                continue
            is_git = (item / ".git").is_dir()
            entries.append(
                BrowseEntry(
                    name=item.name,
                    path=str(item),
                    is_git_repo=is_git,
                )
            )
    except PermissionError:
        structlog.get_logger(__name__).warning(
            "browse_directory_permission_denied",
            path=str(base),
            exc_info=True,
        )

    return BrowseDirectoryResponse(
        current=str(base),
        parent=str(base.parent) if base != home else None,
        items=entries,
    )


# --- Platform status ---


@router.get("/platforms/status", response_model=PlatformStatusListResponse)
async def get_platform_status(
    platform_registry: FromDishka[PlatformRegistry],
) -> PlatformStatusListResponse:
    """Check auth status for all detected git hosting platforms."""
    statuses = await platform_registry.check_all()
    return PlatformStatusListResponse(
        items=[
            PlatformStatusResponse(
                platform=s.platform,
                authenticated=s.authenticated,
                user=s.user,
                error=s.error,
            )
            for s in statuses
        ]
    )


# --- SDK status ---


_SDK_DISPLAY_NAMES: dict[str, str] = {
    "copilot": "GitHub Copilot",
    "claude": "Claude Code",
}


@router.get("/sdks", response_model=SDKListResponse)
async def list_sdks() -> SDKListResponse:
    """List available agent SDKs, installation status, and auth status."""
    import asyncio

    from backend.services.agent_adapter import AgentSDK
    from backend.services.setup_service import _check_agent_auth, check_agent_cli

    config = _get_config()
    default_sdk = config.runtime.default_sdk

    items: list[SDKInfoResponse] = []
    for sdk in AgentSDK:
        cli = check_agent_cli(sdk.value)
        if not cli.ready:
            items.append(
                SDKInfoResponse(
                    id=sdk.value,
                    name=_SDK_DISPLAY_NAMES.get(sdk.value, sdk.value),
                    enabled=False,
                    status="not_installed",
                    authenticated=None,
                    hint=cli.hint,
                )
            )
            continue

        # Run auth check in a thread to avoid blocking the event loop on subprocess.
        auth = await asyncio.to_thread(_check_agent_auth, sdk.value)

        if auth.authenticated is True:
            status = "ready"
            enabled = True
            hint = ""
        elif auth.authenticated is False:
            status = "not_configured"
            enabled = False
            hint = auth.hint
        else:
            # Unknown auth — allow selection but surface a hint
            status = "ready"
            enabled = True
            hint = auth.hint or "Auth status could not be verified"

        items.append(
            SDKInfoResponse(
                id=sdk.value,
                name=_SDK_DISPLAY_NAMES.get(sdk.value, sdk.value),
                enabled=enabled,
                status=status,
                authenticated=auth.authenticated,
                hint=hint,
            )
        )

    return SDKListResponse(default=default_sdk, sdks=items)
