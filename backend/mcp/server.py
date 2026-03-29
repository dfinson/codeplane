"""MCP server exposing CodePlane functionality as MCP tools.

Each tool handler is thin: validate input, delegate to the existing service
layer, and return the result — same principle as the REST route handlers.

Tools use an ``action`` parameter to multiplex related operations under a
single tool name, keeping the total tool count low for LLM clients.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from typing_extensions import TypedDict

from backend import __version__
from backend.config import (
    load_config,
    register_repo,
    unregister_repo,
)
from backend.models.api_schemas import (
    ApprovalResponse,
    ArtifactResponse,
    CreateJobResponse,
    HealthResponse,
    HealthStatus,
    JobListResponse,
    JobResponse,
    RegisterRepoResponse,
    RepoDetailResponse,
    RepoListResponse,
    SendMessageResponse,
    SettingsResponse,
    WorkspaceEntry,
    WorkspaceEntryType,
    WorkspaceListResponse,
)
from backend.persistence.artifact_repo import ArtifactRepository
from backend.persistence.job_repo import JobRepository
from backend.services.agent_adapter import SDKModelMismatchError
from backend.services.artifact_service import ArtifactService
from backend.services.git_service import GitError, GitService
from backend.services.job_service import (
    JobNotFoundError,
    JobService,
    RepoNotAllowedError,
    StateConflictError,
)
from backend.services.platform_adapter import detect_platform as _detect_platform

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CPLConfig
    from backend.models.domain import Job
    from backend.services.approval_service import ApprovalService
    from backend.services.runtime_service import RuntimeService
    from backend.services.utility_session import UtilitySessionService

log = structlog.get_logger()

# Intentionally captured at import time — used to compute MCP server uptime.
# This module is imported during app startup so the value is accurate.
_start_time = time.monotonic()


# ---------------------------------------------------------------------------
# MCP tool return-type helpers
# ---------------------------------------------------------------------------


class McpErrorDict(TypedDict):
    """Standard error response returned by MCP tool handlers."""

    error: str


# MCP tool handlers return JSON-serializable dicts produced by Pydantic's
# ``model_dump(mode="json")``.  The broad ``dict[str, Any]`` component
# reflects Pydantic's own return signature; ``McpErrorDict`` captures the
# error path so callers can narrow on the ``"error"`` key.
McpToolResult: TypeAlias = McpErrorDict | dict[str, Any]

# Module-level service references, set once by create_mcp_server().
# These are module-scoped rather than passed per-call because the MCP FastMCP
# tool decorator captures free functions — there is no instance to bind to.
# The assert-based accessors below ensure a clear error if called before init.
_session_factory: async_sessionmaker[AsyncSession] | None = None
_runtime_service: RuntimeService | None = None
_approval_service: ApprovalService | None = None
_utility_session: UtilitySessionService | None = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    assert _session_factory is not None, "MCP server not initialized"  # noqa: S101
    return _session_factory


def _get_runtime() -> RuntimeService:
    assert _runtime_service is not None, "MCP server not initialized"  # noqa: S101
    return _runtime_service


def _get_approval() -> ApprovalService:
    assert _approval_service is not None, "MCP server not initialized"  # noqa: S101
    return _approval_service


# ---------------------------------------------------------------------------
# Service factory helpers — avoid repeating construction across tool handlers
# ---------------------------------------------------------------------------


def _make_job_service(session: AsyncSession, config: CPLConfig, *, git: bool = True) -> JobService:
    from backend.services.naming_service import NamingService

    naming: NamingService | None = None
    if _utility_session is not None:
        naming = NamingService(_utility_session)
    return JobService(
        job_repo=JobRepository(session),
        git_service=GitService(config) if git else None,
        config=config,
        naming_service=naming,
    )


def _make_artifact_service(session: AsyncSession) -> ArtifactService:
    return ArtifactService(ArtifactRepository(session))


def _job_to_response(job: Job) -> McpToolResult:
    """Convert a domain Job to a serializable dict via JobResponse."""
    resp = JobResponse(
        id=job.id,
        repo=job.repo,
        prompt=job.prompt,
        title=job.title,
        state=job.state,
        base_ref=job.base_ref,
        worktree_path=job.worktree_path,
        branch=job.branch,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        pr_url=job.pr_url,
        merge_status=job.merge_status,
        resolution=job.resolution,
        archived_at=job.archived_at,
        failure_reason=job.failure_reason,
        model=job.model,
        sdk=job.sdk,
        worktree_name=job.worktree_name,
        verify=job.verify,
        self_review=job.self_review,
        max_turns=job.max_turns,
        verify_prompt=job.verify_prompt,
        self_review_prompt=job.self_review_prompt,
    )
    return resp.model_dump(mode="json")


def create_mcp_server(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    runtime_service: RuntimeService,
    approval_service: ApprovalService,
    utility_session: UtilitySessionService | None = None,
) -> FastMCP:
    """Create and configure the MCP server with all CodePlane tools."""
    global _session_factory, _runtime_service, _approval_service, _utility_session  # noqa: PLW0603
    _session_factory = session_factory
    _runtime_service = runtime_service
    _approval_service = approval_service
    _utility_session = utility_session

    mcp = FastMCP(
        "CodePlane",
        instructions="CodePlane — control plane for running and supervising coding agents.",
        stateless_http=True,
        streamable_http_path="/",
    )

    _register_job_tool(mcp)
    _register_approval_tool(mcp)
    _register_workspace_tool(mcp)
    _register_artifact_tool(mcp)
    _register_settings_tool(mcp)
    _register_repo_tool(mcp)
    _register_health_tool(mcp)

    return mcp


# ---------------------------------------------------------------------------
# Job Management
# ---------------------------------------------------------------------------


def _register_job_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        name="codeplane_job",
        title="Manage Coding Jobs",
        annotations=ToolAnnotations(title="Manage Coding Jobs", destructiveHint=True, openWorldHint=True),
        description=(
            "Manage coding jobs. Actions: create, list, get, cancel, rerun, message."
            "\n\n"
            "- create: repo (required), prompt (required), base_ref, branch"
            "\n- list: state (filter), limit (default 50), cursor"
            "\n- get: job_id (required)"
            "\n- cancel: job_id (required)"
            "\n- rerun: job_id (required)"
            "\n- message: job_id (required), content (required, max 10000 chars)"
        ),
    )
    async def codeplane_job(
        action: Literal["create", "list", "get", "cancel", "rerun", "message"],
        job_id: str | None = None,
        repo: str | None = None,
        prompt: str | None = None,
        content: str | None = None,
        base_ref: str | None = None,
        branch: str | None = None,
        model: str | None = None,
        sdk: str | None = None,
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> McpToolResult:
        sf = _get_session_factory()
        config = load_config()

        if action == "create":
            if not repo or not prompt:
                return {"error": "repo and prompt are required for create"}
            async with sf() as session:
                svc = _make_job_service(session, config)
                try:
                    job = await svc.create_job(
                        repo=repo,
                        prompt=prompt,
                        base_ref=base_ref,
                        branch=branch,
                        model=model,
                        sdk=sdk,
                    )
                except RepoNotAllowedError as exc:
                    return {"error": str(exc)}
                except SDKModelMismatchError as exc:
                    return {"error": str(exc)}
                await session.commit()
                runtime = _get_runtime()
                await runtime.start_or_enqueue(job)
                job = await svc.get_job(job.id)
            return CreateJobResponse(
                id=job.id,
                state=job.state,
                branch=job.branch,
                worktree_path=job.worktree_path,
                sdk=job.sdk,
                created_at=job.created_at,
            ).model_dump(mode="json")

        if action == "list":
            async with sf() as session:
                svc = _make_job_service(session, config)
                jobs, next_cursor, has_more = await svc.list_jobs(
                    state=state,
                    limit=min(max(limit, 1), 100),
                    cursor=cursor,
                )
            return JobListResponse(
                items=[
                    JobResponse(
                        id=j.id,
                        repo=j.repo,
                        prompt=j.prompt,
                        state=j.state,
                        base_ref=j.base_ref,
                        worktree_path=j.worktree_path,
                        branch=j.branch,
                        created_at=j.created_at,
                        updated_at=j.updated_at,
                        completed_at=j.completed_at,
                        pr_url=j.pr_url,
                        sdk=j.sdk,
                    )
                    for j in jobs
                ],
                cursor=next_cursor,
                has_more=has_more,
            ).model_dump(mode="json")

        if action == "get":
            if not job_id:
                return {"error": "job_id is required for get"}
            async with sf() as session:
                svc = _make_job_service(session, config)
                try:
                    job = await svc.get_job(job_id)
                except JobNotFoundError as exc:
                    return {"error": str(exc)}
            return _job_to_response(job)

        if action == "cancel":
            if not job_id:
                return {"error": "job_id is required for cancel"}
            async with sf() as session:
                svc = _make_job_service(session, config)
                try:
                    job = await svc.cancel_job(job_id)
                except (JobNotFoundError, StateConflictError) as exc:
                    return {"error": str(exc)}
            runtime = _get_runtime()
            await runtime.cancel(job_id)
            return _job_to_response(job)

        if action == "rerun":
            if not job_id:
                return {"error": "job_id is required for rerun"}
            async with sf() as session:
                svc = _make_job_service(session, config)
                try:
                    job = await svc.rerun_job(job_id)
                except (JobNotFoundError, RepoNotAllowedError) as exc:
                    return {"error": str(exc)}
                await session.commit()
            return CreateJobResponse(
                id=job.id,
                state=job.state,
                branch=job.branch,
                worktree_path=job.worktree_path,
                sdk=job.sdk,
                created_at=job.created_at,
            ).model_dump(mode="json")

        if action == "message":
            if not job_id or not content:
                return {"error": "job_id and content are required for message"}
            if len(content) > 10000:
                return {"error": "Content must be at most 10,000 characters"}
            runtime = _get_runtime()
            sent = await runtime.send_message(job_id, content)
            if not sent:
                return {"error": "Job is not currently running"}
            from datetime import UTC, datetime

            return SendMessageResponse(
                seq=0,
                timestamp=datetime.now(UTC),
            ).model_dump(mode="json")

        return {"error": f"Unknown action: {action}. Use: create, list, get, cancel, rerun, message"}


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def _register_approval_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        name="codeplane_approval",
        title="Manage Approvals",
        annotations=ToolAnnotations(title="Manage Approvals", destructiveHint=True),
        description=(
            "Manage approval requests. Actions: list, resolve."
            "\n\n"
            "- list: job_id (required)"
            "\n- resolve: approval_id (required), resolution ('approved' or 'rejected')"
        ),
    )
    async def codeplane_approval(
        action: Literal["list", "resolve"],
        job_id: str | None = None,
        approval_id: str | None = None,
        resolution: str | None = None,
    ) -> McpToolResult | list[dict[str, Any]]:
        svc = _get_approval()

        if action == "list":
            if not job_id:
                return {"error": "job_id is required for list"}
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
                    requires_explicit_approval=a.requires_explicit_approval,
                ).model_dump(mode="json")
                for a in approvals
            ]

        if action == "resolve":
            if not approval_id or not resolution:
                return {"error": "approval_id and resolution are required for resolve"}
            if resolution not in ("approved", "rejected"):
                return {"error": "Resolution must be 'approved' or 'rejected'"}
            from backend.services.approval_service import (
                ApprovalAlreadyResolvedError,
                ApprovalNotFoundError,
            )

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
                requires_explicit_approval=a.requires_explicit_approval,
            ).model_dump(mode="json")

        return {"error": f"Unknown action: {action}. Use: list, resolve"}


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


def _register_workspace_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        name="codeplane_workspace",
        title="Browse Job Worktree",
        annotations=ToolAnnotations(title="Browse Job Worktree", readOnlyHint=True),
        description=(
            "Browse a job's worktree. Actions: list, read."
            "\n\n"
            "- list: job_id (required), path (default ''), cursor, limit (default 200)"
            "\n- read: job_id (required), path (required)"
        ),
    )
    async def codeplane_workspace(
        action: Literal["list", "read"],
        job_id: str | None = None,
        path: str = "",
        cursor: str | None = None,
        limit: int = 200,
    ) -> McpToolResult:
        if not job_id:
            return {"error": "job_id is required"}

        sf = _get_session_factory()
        config = load_config()
        async with sf() as session:
            svc = _make_job_service(session, config, git=False)
            try:
                job = await svc.get_job(job_id)
            except JobNotFoundError as exc:
                return {"error": str(exc)}

        worktree = Path(job.worktree_path or job.repo).resolve()

        if action == "list":
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

            clamped_limit = min(max(limit, 1), 200)
            past_cursor = cursor is None
            for item in sorted_items:
                if item.name.startswith("."):
                    continue
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
                if len(entries) >= clamped_limit:
                    break

            has_more = len(entries) == clamped_limit
            next_cursor = entries[-1].path if has_more else None
            return WorkspaceListResponse(
                items=entries,
                cursor=next_cursor,
                has_more=has_more,
            ).model_dump(mode="json")

        if action == "read":
            if not path:
                return {"error": "path is required for read"}
            file_path = (worktree / path).resolve()
            if not file_path.is_relative_to(worktree):
                return {"error": "Invalid path"}
            if not file_path.is_file():
                return {"error": "File not found"}
            max_file_size = 5 * 1024 * 1024
            if file_path.stat().st_size > max_file_size:
                return {"error": "File too large to preview (>5 MB)"}
            try:
                file_content = file_path.read_text(encoding="utf-8", errors="replace")
            except (PermissionError, OSError):
                return {"error": "Cannot read file"}
            return {"path": path, "content": file_content}

        return {"error": f"Unknown action: {action}. Use: list, read"}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def _register_artifact_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        name="codeplane_artifact",
        title="Access Job Artifacts",
        annotations=ToolAnnotations(title="Access Job Artifacts", readOnlyHint=True),
        description=(
            "Access job artifacts. Actions: list, get.\n\n- list: job_id (required)\n- get: artifact_id (required)"
        ),
    )
    async def codeplane_artifact(
        action: Literal["list", "get"],
        job_id: str | None = None,
        artifact_id: str | None = None,
    ) -> McpToolResult:
        sf = _get_session_factory()

        if action == "list":
            if not job_id:
                return {"error": "job_id is required for list"}
            async with sf() as session:
                svc = _make_artifact_service(session)
                artifacts = await svc.list_for_job(job_id)
                await session.commit()
            return {
                "items": [
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
            }

        if action == "get":
            if not artifact_id:
                return {"error": "artifact_id is required for get"}
            async with sf() as session:
                svc = _make_artifact_service(session)
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

        return {"error": f"Unknown action: {action}. Use: list, get"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _register_settings_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        name="codeplane_settings",
        title="Global Settings",
        annotations=ToolAnnotations(title="Global Settings", idempotentHint=True),
        description=(
            "View or update global settings. Actions: get, update."
            "\n\n"
            "- get: no extra params"
            "\n- update: pass any combination of: max_concurrent_jobs, permission_mode,"
            " auto_push, cleanup_worktree,"
            " delete_branch_after_merge, artifact_retention_days,"
            " max_artifact_size_mb, auto_archive_days,"
            " verify, self_review, max_turns, verify_prompt, self_review_prompt"
        ),
    )
    async def codeplane_settings(
        action: Literal["get", "update"],
        max_concurrent_jobs: int | None = None,
        permission_mode: str | None = None,
        auto_push: bool | None = None,
        cleanup_worktree: bool | None = None,
        delete_branch_after_merge: bool | None = None,
        artifact_retention_days: int | None = None,
        max_artifact_size_mb: int | None = None,
        auto_archive_days: int | None = None,
        verify: bool | None = None,
        self_review: bool | None = None,
        max_turns: int | None = None,
        verify_prompt: str | None = None,
        self_review_prompt: str | None = None,
    ) -> McpToolResult:
        config = load_config()

        if action == "get":
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
                verify_prompt=config.verification.verify_prompt,
                self_review_prompt=config.verification.self_review_prompt,
            ).model_dump(mode="json")

        if action == "update":
            from backend.config import save_config

            field_map: dict[str, tuple[str, str, Any]] = {
                "max_concurrent_jobs": ("runtime", "max_concurrent_jobs", max_concurrent_jobs),
                "permission_mode": ("runtime", "permission_mode", permission_mode),
                "auto_push": ("completion", "auto_push", auto_push),
                "cleanup_worktree": ("completion", "cleanup_worktree", cleanup_worktree),
                "delete_branch_after_merge": ("completion", "delete_branch_after_merge", delete_branch_after_merge),
                "artifact_retention_days": ("retention", "artifact_retention_days", artifact_retention_days),
                "max_artifact_size_mb": ("retention", "max_artifact_size_mb", max_artifact_size_mb),
                "auto_archive_days": ("retention", "auto_archive_days", auto_archive_days),
                "verify": ("verification", "verify", verify),
                "self_review": ("verification", "self_review", self_review),
                "max_turns": ("verification", "max_turns", max_turns),
                "verify_prompt": ("verification", "verify_prompt", verify_prompt),
                "self_review_prompt": ("verification", "self_review_prompt", self_review_prompt),
            }
            for _key, (section_name, attr, value) in field_map.items():
                if value is not None:
                    section = getattr(config, section_name)
                    setattr(section, attr, value)
            save_config(config)
            # Return updated settings
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
                verify_prompt=config.verification.verify_prompt,
                self_review_prompt=config.verification.self_review_prompt,
            ).model_dump(mode="json")

        return {"error": f"Unknown action: {action}. Use: get, update"}


# ---------------------------------------------------------------------------
# Repository Management
# ---------------------------------------------------------------------------


def _register_repo_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        name="codeplane_repo",
        title="Manage Repositories",
        annotations=ToolAnnotations(title="Manage Repositories", destructiveHint=True, openWorldHint=True),
        description=(
            "Manage registered repositories. Actions: list, get, register, remove."
            "\n\n"
            "- list: no extra params"
            "\n- get: repo_path (required)"
            "\n- register: source (required, local path or URL), clone_to (required if URL)"
            "\n- remove: repo_path (required)"
        ),
    )
    async def codeplane_repo(
        action: Literal["list", "get", "register", "remove"],
        repo_path: str | None = None,
        source: str | None = None,
        clone_to: str | None = None,
    ) -> McpToolResult:
        config = load_config()

        if action == "list":
            return RepoListResponse(items=config.repos).model_dump(mode="json")

        if action == "get":
            if not repo_path:
                return {"error": "repo_path is required for get"}
            resolved = str(Path(repo_path).expanduser().resolve())
            if resolved not in config.repos:
                return {"error": f"Repository '{repo_path}' is not registered."}
            git = GitService(config)
            origin_url: str | None = None
            base_branch: str | None = None
            with contextlib.suppress(GitError):
                raw_url = await git.get_origin_url(resolved)
                if raw_url:
                    origin_url = GitService.strip_url_credentials(raw_url)
            with contextlib.suppress(GitError):
                base_branch = await git.get_default_branch(resolved)
            return RepoDetailResponse(
                path=resolved,
                origin_url=origin_url,
                base_branch=base_branch,
                platform=_detect_platform(origin_url),
            ).model_dump(mode="json")

        if action == "register":
            if not source:
                return {"error": "source is required for register"}
            git = GitService(config)
            if GitService.is_remote_url(source):
                if not clone_to:
                    return {"error": "clone_to is required when registering a remote URL"}
                clone_dir = str(Path(clone_to).expanduser().resolve())
                if Path(clone_dir).exists():
                    return {"error": f"Clone directory already exists: {clone_dir}"}
                try:
                    cloned_path = await git.clone_repo(source, clone_dir)
                except GitError as exc:
                    return {"error": f"Clone failed: {exc}"}
                register_repo(config, cloned_path)
                return RegisterRepoResponse(
                    path=cloned_path,
                    source=source,
                    cloned=True,
                ).model_dump(mode="json")
            resolved = str(Path(source).expanduser().resolve())
            is_valid = await git.validate_repo(resolved)
            if not is_valid:
                return {"error": f"Not a valid git repository: {source}"}
            register_repo(config, resolved)
            return RegisterRepoResponse(
                path=resolved,
                source=source,
                cloned=False,
            ).model_dump(mode="json")

        if action == "remove":
            if not repo_path:
                return {"error": "repo_path is required for remove"}
            try:
                unregister_repo(config, repo_path)
            except ValueError as exc:
                return {"error": str(exc)}
            return {"status": "removed", "path": repo_path}

        return {"error": f"Unknown action: {action}. Use: list, get, register, remove"}


# ---------------------------------------------------------------------------
# Health & Observability
# ---------------------------------------------------------------------------


def _register_health_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        name="codeplane_health",
        title="Health & Maintenance",
        annotations=ToolAnnotations(title="Health & Maintenance"),
        description=(
            "Service health and maintenance. Actions: check, cleanup."
            "\n\n"
            "- check: returns status, uptime, active/queued job counts"
            "\n- cleanup: remove worktrees for completed jobs"
        ),
    )
    async def codeplane_health(action: Literal["check", "cleanup"] = "check") -> McpToolResult:
        config = load_config()

        if action == "check":
            sf = _get_session_factory()
            async with sf() as session:
                svc = _make_job_service(session, config)
                active = await svc.count_active_jobs()
                queued = await svc.count_queued_jobs()
            return HealthResponse(
                status=HealthStatus.healthy,
                version=__version__,
                uptime_seconds=round(time.monotonic() - _start_time, 1),
                active_jobs=active,
                queued_jobs=queued,
            ).model_dump(mode="json")

        if action == "cleanup":
            git = GitService(config)
            total = 0
            for repo in config.repos:
                try:
                    count = await git.cleanup_worktrees(repo)
                    total += count
                except GitError:
                    log.warning("cleanup_worktrees_failed", repo=repo)
            return {"removed": total}

        return {"error": f"Unknown action: {action}. Use: check, cleanup"}
