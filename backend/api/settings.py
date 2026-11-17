"""Settings management endpoints."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import (
    DEFAULT_SELF_REVIEW_PROMPT,
    DEFAULT_VERIFY_PROMPT,
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
    RepoCostSummary,
    RepoDetailResponse,
    RepoJobSummary,
    RepoListResponse,
    RepoSummaryResponse,
    SDKInfoResponse,
    SDKListResponse,
    SettingsResponse,
    UpdateSettingsRequest,
)
from backend.models.db import JobRow
from backend.models.domain import ACTIVE_STATES
from backend.persistence.project_repo import ProjectRepository
from backend.services.adapters.platform_adapter import PlatformRegistry, detect_platform
from backend.services.git.git_service import GitError, GitService
from backend.services.project.repo_membership import list_managed_repo_paths

router = APIRouter(tags=["settings"], route_class=DishkaRoute)


async def _is_project_member(repo_path: str, project_repo: ProjectRepository) -> bool:
    resolved = str(Path(repo_path).expanduser().resolve())
    return any(resolved in project.repo_paths for project in await project_repo.list())


def _config_to_response(config: CPLConfig) -> SettingsResponse:
    return SettingsResponse(
        max_concurrent_jobs=config.runtime.max_concurrent_jobs,
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
        cli_sidecars=config.runtime.cli_sidecars,
    )


@router.get("/settings", response_model=SettingsResponse)
def get_settings(
    config: FromDishka[CPLConfig],
) -> SettingsResponse:
    """Get current settings as structured data."""
    return _config_to_response(config)


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: UpdateSettingsRequest,
    config: FromDishka[CPLConfig],
) -> SettingsResponse:
    """Update settings. Only provided fields are changed.

    Mutates the app-scoped ``CPLConfig`` singleton in place (rather than a
    freshly ``load_config()``-ed copy) so the change is visible immediately
    to ``GET /settings`` and to any running services holding a reference to
    this same config object — not just after the next server restart.
    """
    updates = body.model_dump(exclude_none=True)

    # Declarative mapping: request field → (config section, config attribute)
    _FIELD_MAP: dict[str, tuple[str, str]] = {  # noqa: N806
        "max_concurrent_jobs": ("runtime", "max_concurrent_jobs"),
        "auto_push": ("completion", "auto_push"),
        "cleanup_worktree": ("completion", "cleanup_worktree"),
        "delete_branch_after_merge": ("completion", "delete_branch_after_merge"),
        "artifact_retention_days": ("retention", "artifact_retention_days"),
        "max_artifact_size_mb": ("retention", "max_artifact_size_mb"),
        "auto_archive_days": ("retention", "auto_archive_days"),
        "verify": ("verification", "verify"),
        "self_review": ("verification", "self_review"),
        "max_turns": ("verification", "max_turns"),
        "verify_prompt": ("verification", "verify_prompt"),
        "self_review_prompt": ("verification", "self_review_prompt"),
        "cli_sidecars": ("runtime", "cli_sidecars"),
    }

    for field, (section, attr) in _FIELD_MAP.items():
        if field in updates:
            setattr(getattr(config, section), attr, updates[field])

    save_config(config)

    return _config_to_response(config)


@router.get("/settings/repos", response_model=RepoListResponse)
async def list_repos(
    config: FromDishka[CPLConfig],
    project_repo: FromDishka[ProjectRepository],
) -> RepoListResponse:
    """List every Project-owned or legacy-registered repository path."""
    return RepoListResponse(items=await list_managed_repo_paths(config, project_repo))


@router.get("/settings/repos/{repo_path:path}/summary", response_model=RepoSummaryResponse)
async def get_repo_summary(
    repo_path: str,
    config: FromDishka[CPLConfig],
    project_repo: FromDishka[ProjectRepository],
    git_service: FromDishka[GitService],
    sf: FromDishka[async_sessionmaker[AsyncSession]],
) -> RepoSummaryResponse:
    """Aggregated dashboard overview for a single repository."""
    log = structlog.get_logger()

    resolved = str(Path(repo_path).expanduser().resolve())
    if resolved not in config.repos and not await _is_project_member(resolved, project_repo):
        raise HTTPException(status_code=404, detail=f"Repository '{repo_path}' is not registered.")

    # --- Git info ---
    origin_url: str | None = None
    base_branch: str | None = None
    current_branch: str | None = None
    with contextlib.suppress(GitError):
        raw_url = await git_service.get_origin_url(resolved)
        if raw_url:
            origin_url = GitService.strip_url_credentials(raw_url)
    with contextlib.suppress(GitError):
        base_branch = await git_service.get_default_branch(resolved)
    with contextlib.suppress(GitError):
        current_branch = await git_service.get_current_branch(cwd=resolved)

    platform = detect_platform(origin_url)

    # --- Recent jobs (from DB) ---
    recent_jobs: list[RepoJobSummary] = []
    active_job_count = 0
    cost_summary = RepoCostSummary()
    try:
        async with sf() as session:
            rows = (
                (
                    await session.execute(
                        select(JobRow).where(JobRow.repo == resolved).order_by(JobRow.created_at.desc()).limit(5)
                    )
                )
                .scalars()
                .all()
            )
            for r in rows:
                recent_jobs.append(
                    RepoJobSummary(
                        id=r.id,
                        title=r.title,
                        state=r.state,
                        created_at=r.created_at,
                        completed_at=r.completed_at,
                        total_cost_usd=None,  # cost lives in spans, not job row
                        model=r.model,
                    )
                )
            active_count_result = await session.execute(
                select(JobRow.id)
                .where(JobRow.repo == resolved)
                .where(JobRow.state.in_(("running", "preparing", "paused")))
            )
            active_job_count = len(active_count_result.scalars().all())

            # Aggregate cost from telemetry summary table (last 7 days)
            from sqlalchemy import text as sa_text

            cost_row = (
                (
                    await session.execute(
                        sa_text(
                            "SELECT"
                            " COALESCE(SUM(total_cost_usd), 0) AS total_cost_usd,"
                            " COUNT(*) AS total_jobs,"
                            " COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens"
                            " + cache_write_tokens), 0) AS total_tokens"
                            " FROM job_telemetry_summary"
                            " WHERE repo = :repo AND session_kind = 'job'"
                            " AND created_at >= datetime('now', '-7 days')"
                        ),
                        {"repo": resolved},
                    )
                )
                .mappings()
                .first()
            )
            if cost_row:
                cost_summary = RepoCostSummary(
                    total_cost_usd=float(cost_row["total_cost_usd"]),
                    total_jobs=int(cost_row["total_jobs"]),
                    total_tokens=int(cost_row["total_tokens"]),
                )
    except Exception:
        log.warning("repo_summary.jobs_query_failed", repo=resolved, exc_info=True)

    return RepoSummaryResponse(
        path=resolved,
        origin_url=origin_url,
        base_branch=base_branch,
        current_branch=current_branch,
        platform=platform,
        recent_jobs=recent_jobs,
        active_job_count=active_job_count,
        cost=cost_summary,
    )


# NOTE: This catch-all route MUST be registered AFTER the more specific
# /summary route, because {repo_path:path} is greedy
# and would otherwise consume the suffix as part of repo_path.
@router.get("/settings/repos/{repo_path:path}", response_model=RepoDetailResponse)
async def get_repo_detail(
    repo_path: str,
    config: FromDishka[CPLConfig],
    project_repo: FromDishka[ProjectRepository],
    git_service: FromDishka[GitService],
    sf: FromDishka[async_sessionmaker[AsyncSession]],
) -> RepoDetailResponse:
    """Get detailed config for a single registered repository."""
    resolved = str(Path(repo_path).expanduser().resolve())
    if resolved not in config.repos and not await _is_project_member(resolved, project_repo):
        raise HTTPException(status_code=404, detail=f"Repository '{repo_path}' is not registered.")

    origin_url: str | None = None
    base_branch: str | None = None
    current_branch: str | None = None
    with contextlib.suppress(GitError):
        raw_url = await git_service.get_origin_url(resolved)
        if raw_url:
            origin_url = GitService.strip_url_credentials(raw_url)
    with contextlib.suppress(GitError):
        base_branch = await git_service.get_default_branch(resolved)
    with contextlib.suppress(GitError):
        current_branch = await git_service.get_current_branch(cwd=resolved)

    async with sf() as session:
        active_job_count = int(
            await session.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(
                    JobRow.repo == resolved,
                    JobRow.state.in_(tuple(state.value for state in ACTIVE_STATES)),
                )
            )
            or 0
        )

    return RepoDetailResponse(
        path=resolved,
        origin_url=origin_url,
        base_branch=base_branch,
        current_branch=current_branch,
        active_job_count=active_job_count,
        platform=detect_platform(origin_url),
    )


@router.post("/settings/repos", response_model=RegisterRepoResponse, status_code=201)
async def register_repo_endpoint(
    body: RegisterRepoRequest,
    config: FromDishka[CPLConfig],
    git_service: FromDishka[GitService],
) -> RegisterRepoResponse:
    """Register a repository (local path or remote URL)."""
    source = body.source
    inferred_clone = GitService.is_remote_url(source)
    mode = body.mode or ("clone" if inferred_clone else "register")

    if mode == "clone":
        if not inferred_clone:
            raise HTTPException(status_code=400, detail="Clone mode requires a remote repository URL")
        if not body.clone_to:
            raise HTTPException(
                status_code=400,
                detail="clone_to path is required when cloning a remote URL",
            )
        clone_dir = str(Path(body.clone_to).expanduser().resolve())
        if Path(clone_dir).exists():
            raise HTTPException(
                status_code=409,
                detail=f"Clone directory already exists: {clone_dir}",
            )
        try:
            cloned_path = await git_service.clone_repo(source, clone_dir)
        except GitError as exc:
            structlog.get_logger().warning("clone_failed", source=source, exc_info=exc)
            raise HTTPException(status_code=400, detail="Clone failed") from exc
        register_repo(config, cloned_path)
        return RegisterRepoResponse(path=cloned_path, source=source, cloned=True, registered=True)

    if inferred_clone:
        raise HTTPException(status_code=400, detail="Register mode requires a local repository path")

    resolved = str(Path(source).expanduser().resolve())
    is_valid = await git_service.validate_repo(resolved)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Not a valid git repository: {source}",
        )
    newly_registered = resolved not in config.repos
    register_repo(config, resolved)
    return RegisterRepoResponse(
        path=resolved,
        source=source,
        cloned=False,
        registered=newly_registered,
    )


@router.post("/settings/repos/create", response_model=CreateRepoResponse, status_code=201)
async def create_repo_endpoint(
    body: CreateRepoRequest,
    config: FromDishka[CPLConfig],
    git_service: FromDishka[GitService],
) -> CreateRepoResponse:
    """Create a new git repository and register it."""
    parent = Path(body.path).expanduser().resolve()
    resolved = parent
    if body.name:
        name = body.name.strip()
        if name in ("", ".", "..") or "/" in name or "\\" in name:
            raise HTTPException(status_code=400, detail="Repository name must be a single directory name")
        resolved = parent / name
        if resolved.parent != parent:
            raise HTTPException(status_code=400, detail="Repository name must be a single directory name")

    if (resolved / ".git").is_dir():
        raise HTTPException(status_code=409, detail=f"A git repository already exists at {resolved}")

    try:
        repo_path = await git_service.init_repo(str(resolved))
    except GitError as exc:
        structlog.get_logger().warning("repo_create_failed", path=str(resolved), exc_info=exc)
        raise HTTPException(status_code=400, detail="Failed to create repository") from exc

    register_repo(config, repo_path)
    return CreateRepoResponse(path=repo_path, name=resolved.name)


@router.delete("/settings/repos/{repo_path:path}", status_code=204)
def unregister_repo_endpoint(
    repo_path: str,
    config: FromDishka[CPLConfig],
) -> None:
    """Remove a repository from the allowlist."""
    try:
        unregister_repo(config, repo_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Repository not found in allowlist") from exc


@router.post("/settings/cleanup-worktrees", response_model=CleanupWorktreesResponse)
async def cleanup_worktrees(
    config: FromDishka[CPLConfig],
    project_repo: FromDishka[ProjectRepository],
    git_service: FromDishka[GitService],
) -> CleanupWorktreesResponse:
    """Clean up completed job worktrees for all managed repositories."""
    total = 0
    for repo in await list_managed_repo_paths(config, project_repo):
        try:
            count = await git_service.cleanup_worktrees(repo)
            total += count
        except GitError:
            structlog.get_logger().warning("cleanup_worktrees_failed", repo=repo)
    return CleanupWorktreesResponse(removed=total)


@router.get("/settings/browse", response_model=BrowseDirectoryResponse)
def browse_directories(
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
    if not base.is_relative_to(home):
        raise HTTPException(status_code=403, detail="Access denied")

    entries: list[BrowseEntry] = []
    try:
        # Use os.scandir for performance: DirEntry.is_dir() uses cached
        # d_type from readdir instead of issuing a separate stat() per entry.
        with os.scandir(base) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                is_git = os.path.isdir(os.path.join(entry.path, ".git"))
                entries.append(
                    BrowseEntry(
                        name=entry.name,
                        path=entry.path,
                        is_git_repo=is_git,
                    )
                )
        entries.sort(key=lambda e: e.name.lower())
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

    from backend.models.domain import AgentSDK
    from backend.services.setup.checks import check_agent_auth, check_agent_cli

    config = load_config()
    from backend.services.adapters.integration_policy import IntegrationPolicy

    policy = IntegrationPolicy(config)
    default_sdk = policy.default_sdk

    items: list[SDKInfoResponse] = []
    for sdk in AgentSDK:
        if not policy.enabled(sdk):
            items.append(
                SDKInfoResponse(
                    id=sdk.value,
                    name=_SDK_DISPLAY_NAMES.get(sdk.value, sdk.value),
                    enabled=False,
                    status="disabled_by_policy",
                    authenticated=None,
                    hint="Disabled by runtime.copilot_only. Existing history is preserved.",
                )
            )
            continue
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
        auth = await asyncio.to_thread(check_agent_auth, sdk.value)

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
