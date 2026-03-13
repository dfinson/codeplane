"""MCP server exposing Tower functionality as MCP tools.

Each tool handler is thin: validate input, delegate to the existing service
layer, and return the result — same principle as the REST route handlers.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from mcp.server.fastmcp import FastMCP

from backend.config import (
    load_config,
    register_repo,
    unregister_repo,
)
from backend.models.api_schemas import (
    ApprovalResponse,
    ArtifactResponse,
    CreateJobResponse,
    GlobalConfigResponse,
    HealthResponse,
    HealthStatus,
    JobListResponse,
    JobResponse,
    RegisterRepoResponse,
    RepoDetailResponse,
    RepoListResponse,
    SendMessageResponse,
    WorkspaceEntry,
    WorkspaceEntryType,
    WorkspaceListResponse,
)
from backend.persistence.artifact_repo import ArtifactRepository
from backend.persistence.job_repo import JobRepository
from backend.services.artifact_service import ArtifactService
from backend.services.git_service import GitError, GitService
from backend.services.job_service import (
    JobNotFoundError,
    JobService,
    RepoNotAllowedError,
    StateConflictError,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.services.approval_service import ApprovalService
    from backend.services.runtime_service import RuntimeService

log = structlog.get_logger()

_start_time = time.monotonic()

# Module-level references to shared services, set during create_mcp_server()
_session_factory: async_sessionmaker[AsyncSession] | None = None
_runtime_service: RuntimeService | None = None
_approval_service: ApprovalService | None = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    assert _session_factory is not None, "MCP server not initialized"  # noqa: S101
    return _session_factory


def _get_runtime() -> RuntimeService:
    assert _runtime_service is not None, "MCP server not initialized"  # noqa: S101
    return _runtime_service


def _get_approval() -> ApprovalService:
    assert _approval_service is not None, "MCP server not initialized"  # noqa: S101
    return _approval_service


def _job_to_response(job: Any) -> dict[str, Any]:
    """Convert a domain Job to a serializable dict via JobResponse."""
    resp = JobResponse(
        id=job.id,
        repo=job.repo,
        prompt=job.prompt,
        state=job.state,
        strategy=job.strategy,
        base_ref=job.base_ref,
        worktree_path=job.worktree_path,
        branch=job.branch,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        pr_url=job.pr_url,
    )
    return resp.model_dump(mode="json")


def _strip_url_credentials(url: str) -> str:
    """Remove embedded credentials from a git remote URL."""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        cleaned = parsed._replace(netloc=host)
        return urlunparse(cleaned)
    return url


def create_mcp_server(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    runtime_service: RuntimeService,
    approval_service: ApprovalService,
) -> FastMCP:
    """Create and configure the MCP server with all Tower tools."""
    global _session_factory, _runtime_service, _approval_service  # noqa: PLW0603
    _session_factory = session_factory
    _runtime_service = runtime_service
    _approval_service = approval_service

    mcp = FastMCP(
        "Tower",
        instructions="Tower — control tower for running and supervising coding agents.",
        stateless_http=True,
        streamable_http_path="/",
    )

    _register_job_tools(mcp)
    _register_approval_tools(mcp)
    _register_workspace_tools(mcp)
    _register_artifact_tools(mcp)
    _register_config_tools(mcp)
    _register_observability_tools(mcp)

    return mcp


# ---------------------------------------------------------------------------
# Job Management Tools
# ---------------------------------------------------------------------------


def _register_job_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="tower_job_create", description="Create a new coding job (repo, prompt, strategy, options)")
    async def tower_job_create(
        repo: str,
        prompt: str,
        base_ref: str | None = None,
        branch: str | None = None,
        strategy: str = "single_agent",
    ) -> dict[str, Any]:
        sf = _get_session_factory()
        config = load_config()
        async with sf() as session:
            svc = JobService(
                job_repo=JobRepository(session),
                git_service=GitService(config),
                config=config,
            )
            try:
                job = await svc.create_job(
                    repo=repo,
                    prompt=prompt,
                    base_ref=base_ref,
                    branch=branch,
                    strategy=strategy,
                )
            except RepoNotAllowedError as exc:
                return {"error": str(exc)}

            await session.commit()

            runtime = _get_runtime()
            await runtime.start_or_enqueue(job)

            job = await svc.get_job(job.id)

        resp = CreateJobResponse(
            id=job.id,
            state=job.state,
            branch=job.branch,
            worktree_path=job.worktree_path,
            created_at=job.created_at,
        )
        return resp.model_dump(mode="json")

    @mcp.tool(name="tower_job_list", description="List jobs with optional status filter and pagination")
    async def tower_job_list(
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        sf = _get_session_factory()
        config = load_config()
        async with sf() as session:
            svc = JobService(
                job_repo=JobRepository(session),
                git_service=GitService(config),
                config=config,
            )
            jobs, next_cursor, has_more = await svc.list_jobs(
                state=state,
                limit=min(max(limit, 1), 100),
                cursor=cursor,
            )

        resp = JobListResponse(
            items=[
                JobResponse(
                    id=j.id,
                    repo=j.repo,
                    prompt=j.prompt,
                    state=j.state,
                    strategy=j.strategy,
                    base_ref=j.base_ref,
                    worktree_path=j.worktree_path,
                    branch=j.branch,
                    created_at=j.created_at,
                    updated_at=j.updated_at,
                    completed_at=j.completed_at,
                    pr_url=j.pr_url,
                )
                for j in jobs
            ],
            cursor=next_cursor,
            has_more=has_more,
        )
        return resp.model_dump(mode="json")

    @mcp.tool(name="tower_job_get", description="Get full detail for a single job")
    async def tower_job_get(job_id: str) -> dict[str, Any]:
        sf = _get_session_factory()
        config = load_config()
        async with sf() as session:
            svc = JobService(
                job_repo=JobRepository(session),
                git_service=GitService(config),
                config=config,
            )
            try:
                job = await svc.get_job(job_id)
            except JobNotFoundError as exc:
                return {"error": str(exc)}
        return _job_to_response(job)

    @mcp.tool(name="tower_job_cancel", description="Cancel a running or queued job")
    async def tower_job_cancel(job_id: str) -> dict[str, Any]:
        sf = _get_session_factory()
        config = load_config()
        async with sf() as session:
            svc = JobService(
                job_repo=JobRepository(session),
                git_service=GitService(config),
                config=config,
            )
            try:
                job = await svc.cancel_job(job_id)
            except JobNotFoundError as exc:
                return {"error": str(exc)}
            except StateConflictError as exc:
                return {"error": str(exc)}

        runtime = _get_runtime()
        await runtime.cancel(job_id)
        return _job_to_response(job)

    @mcp.tool(name="tower_job_rerun", description="Rerun a job with the same configuration")
    async def tower_job_rerun(job_id: str) -> dict[str, Any]:
        sf = _get_session_factory()
        config = load_config()
        async with sf() as session:
            svc = JobService(
                job_repo=JobRepository(session),
                git_service=GitService(config),
                config=config,
            )
            try:
                job = await svc.rerun_job(job_id)
            except (JobNotFoundError, RepoNotAllowedError) as exc:
                return {"error": str(exc)}
            await session.commit()

        resp = CreateJobResponse(
            id=job.id,
            state=job.state,
            branch=job.branch,
            worktree_path=job.worktree_path,
            created_at=job.created_at,
        )
        return resp.model_dump(mode="json")

    @mcp.tool(name="tower_job_message", description="Send an operator message to a running job")
    async def tower_job_message(job_id: str, content: str) -> dict[str, Any]:
        if not content or len(content) > 10000:
            return {"error": "Content must be between 1 and 10,000 characters"}

        runtime = _get_runtime()
        sent = await runtime.send_message(job_id, content)
        if not sent:
            return {"error": "Job is not currently running"}

        from datetime import UTC, datetime

        resp = SendMessageResponse(seq=0, timestamp=datetime.now(UTC))
        return resp.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Approval Tools
# ---------------------------------------------------------------------------


def _register_approval_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="tower_approval_list", description="List pending/resolved approvals for a job")
    async def tower_approval_list(job_id: str) -> list[dict[str, Any]]:
        svc = _get_approval()
        approvals = await svc.list_for_job(job_id)
        return [
            ApprovalResponse(
                id=a.id,
                job_id=a.job_id,
                description=a.description,
                proposed_action=a.proposed_action,
                requested_at=a.requested_at,
                resolved_at=a.resolved_at,
                resolution=a.resolution,
            ).model_dump(mode="json")
            for a in approvals
        ]

    @mcp.tool(name="tower_approval_resolve", description="Approve or reject a pending approval request")
    async def tower_approval_resolve(approval_id: str, resolution: str) -> dict[str, Any]:
        if resolution not in ("approved", "rejected"):
            return {"error": "Resolution must be 'approved' or 'rejected'"}

        from backend.services.approval_service import (
            ApprovalAlreadyResolvedError,
            ApprovalNotFoundError,
        )

        svc = _get_approval()
        try:
            a = await svc.resolve(approval_id, resolution)
        except ApprovalNotFoundError as exc:
            return {"error": str(exc)}
        except ApprovalAlreadyResolvedError as exc:
            return {"error": str(exc)}

        return ApprovalResponse(
            id=a.id,
            job_id=a.job_id,
            description=a.description,
            proposed_action=a.proposed_action,
            requested_at=a.requested_at,
            resolved_at=a.resolved_at,
            resolution=a.resolution,
        ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Workspace & Artifact Tools
# ---------------------------------------------------------------------------


def _register_workspace_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="tower_workspace_list", description="List files in a job's worktree")
    async def tower_workspace_list(
        job_id: str,
        path: str = "",
        cursor: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        sf = _get_session_factory()
        config = load_config()
        async with sf() as session:
            svc = JobService(
                job_repo=JobRepository(session),
                git_service=None,  # type: ignore[arg-type]
                config=config,
            )
            try:
                job = await svc.get_job(job_id)
            except JobNotFoundError as exc:
                return {"error": str(exc)}

        worktree = Path(job.worktree_path or job.repo).resolve()
        if not worktree.is_dir():
            return {"error": "Worktree not found"}

        target = (worktree / path).resolve()
        if not target.is_relative_to(worktree):
            return {"error": "Invalid path"}
        if not target.is_dir():
            return {"error": "Directory not found"}

        entries: list[WorkspaceEntry] = []
        try:
            sorted_items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            sorted_items = []

        limit = min(max(limit, 1), 200)
        past_cursor = cursor is None
        for item in sorted_items:
            if item.name.startswith("."):
                continue
            # Resolve symlinks and ensure they stay within worktree
            try:
                resolved_item = item.resolve()
            except OSError:
                continue
            if not resolved_item.is_relative_to(worktree):
                continue
            rel = str(item.relative_to(worktree))
            if not past_cursor:
                if rel == cursor:
                    past_cursor = True
                continue
            entry_type = WorkspaceEntryType.directory if item.is_dir() else WorkspaceEntryType.file
            size = item.stat().st_size if item.is_file() else None
            entries.append(WorkspaceEntry(path=rel, type=entry_type, size_bytes=size))
            if len(entries) >= limit:
                break

        has_more = len(entries) == limit
        next_cursor = entries[-1].path if has_more else None
        resp = WorkspaceListResponse(items=entries, cursor=next_cursor, has_more=has_more)
        return resp.model_dump(mode="json")

    @mcp.tool(name="tower_workspace_read", description="Read a file from a job's worktree")
    async def tower_workspace_read(job_id: str, path: str) -> dict[str, Any]:
        sf = _get_session_factory()
        config = load_config()
        async with sf() as session:
            svc = JobService(
                job_repo=JobRepository(session),
                git_service=None,  # type: ignore[arg-type]
                config=config,
            )
            try:
                job = await svc.get_job(job_id)
            except JobNotFoundError as exc:
                return {"error": str(exc)}

        worktree = Path(job.worktree_path or job.repo).resolve()
        file_path = (worktree / path).resolve()

        if not file_path.is_relative_to(worktree):
            return {"error": "Invalid path"}
        if not file_path.is_file():
            return {"error": "File not found"}

        max_file_size = 5 * 1024 * 1024
        if file_path.stat().st_size > max_file_size:
            return {"error": "File too large to preview (>5 MB)"}

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            return {"error": "Cannot read file"}

        return {"path": path, "content": content}


def _register_artifact_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="tower_artifact_list", description="List artifacts produced by a job")
    async def tower_artifact_list(job_id: str) -> dict[str, Any]:
        sf = _get_session_factory()
        async with sf() as session:
            svc = ArtifactService(ArtifactRepository(session))
            artifacts = await svc.list_for_job(job_id)
            await session.commit()

        items = [
            ArtifactResponse(
                id=a.id,
                job_id=a.job_id,
                name=a.name,
                type=a.type,
                mime_type=a.mime_type,
                size_bytes=a.size_bytes,
                phase=a.phase,
                created_at=a.created_at,
            ).model_dump(mode="json")
            for a in artifacts
        ]
        return {"items": items}

    @mcp.tool(name="tower_artifact_get", description="Get artifact metadata by ID")
    async def tower_artifact_get(artifact_id: str) -> dict[str, Any]:
        sf = _get_session_factory()
        async with sf() as session:
            svc = ArtifactService(ArtifactRepository(session))
            artifact = await svc.get(artifact_id)
            await session.commit()

        if artifact is None:
            return {"error": "Artifact not found"}

        return ArtifactResponse(
            id=artifact.id,
            job_id=artifact.job_id,
            name=artifact.name,
            type=artifact.type,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size_bytes,
            phase=artifact.phase,
            created_at=artifact.created_at,
        ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Configuration Tools
# ---------------------------------------------------------------------------


def _register_config_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="tower_settings_get", description="Get global configuration as YAML")
    async def tower_settings_get() -> dict[str, Any]:
        from backend.config import DEFAULT_CONFIG_PATH

        if DEFAULT_CONFIG_PATH.exists():
            return GlobalConfigResponse(config_yaml=DEFAULT_CONFIG_PATH.read_text()).model_dump(mode="json")
        return GlobalConfigResponse(config_yaml="").model_dump(mode="json")

    @mcp.tool(name="tower_settings_update", description="Update global configuration from YAML string")
    async def tower_settings_update(config_yaml: str) -> dict[str, Any]:
        import yaml

        from backend.config import DEFAULT_CONFIG_PATH

        try:
            yaml.safe_load(config_yaml)
        except yaml.YAMLError as exc:
            return {"error": f"Invalid YAML: {exc}"}

        DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_CONFIG_PATH.write_text(config_yaml)
        return GlobalConfigResponse(config_yaml=config_yaml).model_dump(mode="json")

    @mcp.tool(name="tower_repo_list", description="List registered repositories")
    async def tower_repo_list() -> dict[str, Any]:
        config = load_config()
        return RepoListResponse(items=config.repos).model_dump(mode="json")

    @mcp.tool(name="tower_repo_get", description="Get repo config with resolved details")
    async def tower_repo_get(repo_path: str) -> dict[str, Any]:
        config = load_config()
        resolved = str(Path(repo_path).expanduser().resolve())
        if resolved not in config.repos:
            return {"error": f"Repository '{repo_path}' is not registered."}

        git = GitService(config)
        origin_url: str | None = None
        base_branch: str | None = None
        with contextlib.suppress(GitError):
            raw_url = await git.get_origin_url(resolved)
            if raw_url:
                origin_url = _strip_url_credentials(raw_url)
        with contextlib.suppress(GitError):
            base_branch = await git.get_default_branch(resolved)

        return RepoDetailResponse(
            path=resolved,
            origin_url=origin_url,
            base_branch=base_branch,
        ).model_dump(mode="json")

    @mcp.tool(name="tower_repo_register", description="Register a repository (path or URL)")
    async def tower_repo_register(source: str) -> dict[str, Any]:
        config = load_config()
        git = GitService(config)

        if GitService.is_remote_url(source):
            clone_dir = GitService.derive_clone_dir(source, config.repos_base_dir)
            if Path(clone_dir).exists():
                return {"error": f"Clone directory already exists: {clone_dir}"}
            try:
                cloned_path = await git.clone_repo(source, clone_dir)
            except GitError as exc:
                return {"error": f"Clone failed: {exc}"}
            register_repo(config, cloned_path)
            return RegisterRepoResponse(path=cloned_path, source=source, cloned=True).model_dump(mode="json")

        resolved = str(Path(source).expanduser().resolve())
        is_valid = await git.validate_repo(resolved)
        if not is_valid:
            return {"error": f"Not a valid git repository: {source}"}
        register_repo(config, resolved)
        return RegisterRepoResponse(path=resolved, source=source, cloned=False).model_dump(mode="json")

    @mcp.tool(name="tower_repo_remove", description="Remove a repository from the allowlist")
    async def tower_repo_remove(repo_path: str) -> dict[str, Any]:
        config = load_config()
        try:
            unregister_repo(config, repo_path)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"status": "removed", "path": repo_path}


# ---------------------------------------------------------------------------
# Observability Tools
# ---------------------------------------------------------------------------


def _register_observability_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="tower_health", description="Service health check")
    async def tower_health() -> dict[str, Any]:
        sf = _get_session_factory()
        config = load_config()
        async with sf() as session:
            svc = JobService(
                job_repo=JobRepository(session),
                git_service=GitService(config),
                config=config,
            )
            active = await svc.count_active_jobs()
            queued = await svc.count_queued_jobs()

        return HealthResponse(
            status=HealthStatus.healthy,
            version="0.1.0",
            uptime_seconds=round(time.monotonic() - _start_time, 1),
            active_jobs=active,
            queued_jobs=queued,
        ).model_dump(mode="json")

    @mcp.tool(name="tower_cleanup_worktrees", description="Clean up worktrees for completed jobs")
    async def tower_cleanup_worktrees() -> dict[str, Any]:
        config = load_config()
        git = GitService(config)
        total = 0
        for repo in config.repos:
            try:
                count = await git.cleanup_worktrees(repo)
                total += count
            except GitError:
                log.warning("cleanup_worktrees_failed", repo=repo)
        return {"removed": total}
