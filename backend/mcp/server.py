"""MCP server exposing CodePlane functionality as MCP tools.

Each tool handler is thin: validate input, delegate to the existing service
layer, and return the result — same principle as the REST route handlers.

Tools use an ``action`` parameter to multiplex related operations under a
single tool name, keeping the total tool count low for LLM clients.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from typing_extensions import TypedDict

from backend import __version__
from backend.config import (
    load_config,
)
from backend.models.api_schemas import (  # type: ignore[attr-defined]
    ApprovalResolution,
    ApprovalResponse,
    ArtifactResponse,
    CreateJobResponse,
    HealthResponse,
    HealthStatus,
    JobListResponse,
    JobResponse,
    SendMessageResponse,
    SettingsResponse,
    WorkspaceEntry,
    WorkspaceEntryType,
    WorkspaceListResponse,
)
from backend.models.domain import (
    JobNotFoundError,
    JobState,
    RepoNotAllowedError,
    SDKModelMismatchError,
    StateConflictError,
)
from backend.services.artifacts.artifact_service import ArtifactService
from backend.services.git.git_service import GitError, GitService
from backend.services.job.job_service import JobService
from backend.services.tracker_resolution import TrackerResolutionError, resolve_tracker_for_job
from backend.services.tracker_write_service import TrackerWriteAction, TrackerWriteRequest, TrackerWriteService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CPLConfig
    from backend.models.domain import Job
    from backend.services.job.approval_service import ApprovalService
    from backend.services.merge_service import MergeService
    from backend.services.runtime import RuntimeService
    from backend.services.sidecar.session import SidecarSessionManager

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
type McpToolResult = McpErrorDict | dict[str, Any]


@dataclass(frozen=True)
class MCPState:
    """Immutable bundle of service references for the MCP server."""

    session_factory: async_sessionmaker[AsyncSession]
    runtime_service: RuntimeService
    approval_service: ApprovalService
    merge_service: MergeService
    sidecar_sessions: SidecarSessionManager | None = None


# Strong refs to fire-and-forget tasks to prevent GC before completion.
_background_tasks: set[asyncio.Task[None]] = set()


# ---------------------------------------------------------------------------
# Service factory helpers — avoid repeating construction across tool handlers
# ---------------------------------------------------------------------------


def _make_job_service(
    state: MCPState,
    session: AsyncSession,
    config: CPLConfig,
    *,
    git: bool = True,
) -> JobService:
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.project_repo import ProjectRepository
    from backend.services.completers.naming_service import NamingService

    naming: NamingService | None = None
    if state.sidecar_sessions is not None:
        naming = NamingService(state.sidecar_sessions)
    return JobService(
        job_repo=JobRepository(session),
        git_service=GitService(config) if git else None,
        config=config,
        naming_service=naming,
        project_repo=ProjectRepository(session),
    )


def _make_artifact_service(session: AsyncSession) -> ArtifactService:
    from backend.persistence.artifact_repo import ArtifactRepository

    return ArtifactService(ArtifactRepository(session))


def _job_to_response(job: Job) -> McpToolResult:
    """Convert a domain Job to a serializable dict via JobResponse."""
    resp = JobResponse.from_domain(job)
    return resp.model_dump(mode="json")


def create_mcp_server(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    runtime_service: RuntimeService,
    approval_service: ApprovalService,
    merge_service: MergeService,
    sidecar_sessions: SidecarSessionManager | None = None,
) -> FastMCP:
    """Create and configure the MCP server with all CodePlane tools."""
    state = MCPState(
        session_factory=session_factory,
        runtime_service=runtime_service,
        approval_service=approval_service,
        merge_service=merge_service,
        sidecar_sessions=sidecar_sessions,
    )

    mcp = FastMCP(
        "CodePlane",
        instructions="CodePlane — control plane for running and supervising coding agents.",
        stateless_http=True,
        streamable_http_path="/",
    )

    _register_job_tool(mcp, state)
    _register_pr_tool(mcp, state)
    _register_tracker_tool(mcp, state)
    _register_approval_tool(mcp, state)
    _register_workspace_tool(mcp, state)
    _register_artifact_tool(mcp, state)
    _register_settings_tool(mcp)
    _register_repo_tool(mcp, state)
    _register_project_tool(mcp)
    _register_health_tool(mcp, state)

    return mcp


# ---------------------------------------------------------------------------
# Job Management
# ---------------------------------------------------------------------------


def _register_job_tool(mcp: FastMCP, mcp_state: MCPState) -> None:
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
        sf = mcp_state.session_factory
        config = load_config()

        if action == "create":
            if not repo or not prompt:
                return {"error": "repo and prompt are required for create"}

            from backend.persistence.database import serialized_write

            async with serialized_write(sf) as session:
                svc = _make_job_service(mcp_state, session, config)
                try:
                    from backend.models.domain import JobSpec

                    job = await svc.create_job(
                        JobSpec(
                            repo=repo,
                            prompt=prompt,
                            base_ref=base_ref,
                            branch=branch,
                            model=model,
                            sdk=sdk,
                        )
                    )
                except RepoNotAllowedError as exc:
                    return {"error": str(exc)}
                except SDKModelMismatchError as exc:
                    return {"error": str(exc)}

            # Commit is done — now launch setup in background (same as REST handler)
            # to avoid holding a DB transaction while runtime acquires its own sessions.
            runtime = mcp_state.runtime_service
            if job.state != JobState.failed:

                async def _setup_and_start() -> None:
                    try:
                        await runtime.setup_and_start(job)
                    except Exception:
                        log.warning("mcp_job_setup_failed", job_id=job.id, exc_info=True)

                task = asyncio.create_task(_setup_and_start(), name=f"mcp-setup-{job.id}")
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)

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
                svc = _make_job_service(mcp_state, session, config)
                jobs, next_cursor, has_more = await svc.list_jobs(
                    state=state,
                    limit=min(max(limit, 1), 100),
                    cursor=cursor,
                )
            return JobListResponse(
                items=[JobResponse.from_domain(j) for j in jobs],
                cursor=next_cursor,
                has_more=has_more,
            ).model_dump(mode="json")

        if action == "get":
            if not job_id:
                return {"error": "job_id is required for get"}
            async with sf() as session:
                svc = _make_job_service(mcp_state, session, config)
                try:
                    job = await svc.get_job(job_id)
                except JobNotFoundError as exc:
                    return {"error": str(exc)}
            return _job_to_response(job)

        if action == "cancel":
            if not job_id:
                return {"error": "job_id is required for cancel"}
            from backend.persistence.database import serialized_write

            async with serialized_write(sf) as session:
                svc = _make_job_service(mcp_state, session, config)
                try:
                    job = await svc.cancel_job(job_id)
                except (JobNotFoundError, StateConflictError) as exc:
                    return {"error": str(exc)}
            runtime = mcp_state.runtime_service
            await runtime.cancel(job_id)
            return _job_to_response(job)

        if action == "rerun":
            if not job_id:
                return {"error": "job_id is required for rerun"}

            from backend.persistence.database import serialized_write

            async with serialized_write(sf) as session:
                svc = _make_job_service(mcp_state, session, config)
                try:
                    job = await svc.rerun_job(job_id)
                except (JobNotFoundError, RepoNotAllowedError) as exc:
                    return {"error": str(exc)}

            # Start the rerun job in background (same pattern as create)
            runtime = mcp_state.runtime_service
            if job.state != JobState.failed:

                async def _setup_rerun() -> None:
                    try:
                        await runtime.setup_and_start(job)
                    except Exception:
                        log.warning("mcp_rerun_setup_failed", job_id=job.id, exc_info=True)

                task = asyncio.create_task(_setup_rerun(), name=f"mcp-rerun-{job.id}")
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)

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
            runtime = mcp_state.runtime_service
            sent = await runtime.send_message(job_id, content)
            if not sent:
                return {"error": "Job is not currently running"}
            from datetime import UTC, datetime

            return SendMessageResponse(
                seq=0,
                timestamp=datetime.now(UTC),
            ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Agent-initiated PR request (CAP-14)
# ---------------------------------------------------------------------------


def _register_pr_tool(mcp: FastMCP, mcp_state: MCPState) -> None:
    @mcp.tool(
        name="codeplane_pr",
        title="Request a Pull Request",
        annotations=ToolAnnotations(title="Request a Pull Request", destructiveHint=True, openWorldHint=True),
        description=(
            "Proactively request a PR for the calling Job while it is still running, "
            "instead of waiting for the automatic completion-time PR/merge step."
            "\n\n"
            "- job_id (required)"
            "\n\n"
            "Idempotent per Job: if a PR already exists for this Job (created via an "
            "earlier call to this tool, or the automatic completion-time path), the "
            "existing PR is returned instead of creating a duplicate. The agent never "
            "receives or handles the underlying Credential's decrypted PAT — CodePlane "
            "resolves and uses it server-side."
        ),
    )
    async def codeplane_pr(job_id: str | None = None) -> McpToolResult:
        if not job_id:
            return {"error": "job_id is required"}

        sf = mcp_state.session_factory
        config = load_config()
        async with sf() as session:
            svc = _make_job_service(mcp_state, session, config, git=False)
            try:
                job = await svc.get_job(job_id)
            except JobNotFoundError as exc:
                return {"error": str(exc)}

        result = await mcp_state.merge_service.request_pr_for_job(job)

        if result.error:
            return {"error": result.error}

        return {"pr_url": result.pr_url, "status": str(result.status)}


# ---------------------------------------------------------------------------
# Agent-initiated tracker comment/transition (Story 6.1, CAP-13)
# ---------------------------------------------------------------------------


def _register_tracker_tool(mcp: FastMCP, mcp_state: MCPState) -> None:
    @mcp.tool(
        name="codeplane_tracker",
        title="Comment or Transition Tracker Ticket",
        annotations=ToolAnnotations(
            title="Comment or Transition Tracker Ticket", destructiveHint=True, openWorldHint=True
        ),
        description=(
            "Comment on or transition the external tracker ticket paired with the calling "
            "Job's Project, while the Job is still running — instead of waiting for a "
            "recipe's completion-time tracker_write output route."
            "\n\n"
            "- comment: job_id (required), value (required, the comment text)"
            "\n- transition: job_id (required), value (required, the target status)"
            "\n\n"
            "CodePlane resolves the Job's Project and TrackerLink server-side and creates a "
            "codeplane_approval entry via the exact same gate CAP-11's recipe-driven "
            "tracker_write route already uses — nothing is written to the external tracker "
            "until that approval is granted. The agent never receives or handles the "
            "Credential's decrypted PAT at any point; CodePlane resolves and uses it "
            "server-side on the agent's behalf."
        ),
    )
    async def codeplane_tracker(
        action: Literal["comment", "transition"],
        job_id: str | None = None,
        value: str | None = None,
    ) -> McpToolResult:
        if not job_id:
            return {"error": "job_id is required"}
        if not value:
            return {"error": "value is required"}

        sf = mcp_state.session_factory
        async with sf() as session:
            try:
                resolved = await resolve_tracker_for_job(session, job_id)
            except TrackerResolutionError as exc:
                return {"error": str(exc)}

        tracker_action = TrackerWriteAction.comment if action == "comment" else TrackerWriteAction.transition
        request = TrackerWriteRequest(
            tracker_link_id=resolved.tracker_link_id,
            ticket_ref=resolved.ticket_ref,
            action=tracker_action,
            value=value,
        )

        service = TrackerWriteService(
            mcp_state.approval_service,
            session_factory=sf,
        )
        outcome = await service.execute(job_id, request)

        return {
            "applied": outcome.applied,
            "state": outcome.state.value,
            "error": outcome.error,
            "ticketRef": resolved.ticket_ref,
            "trackerLinkId": resolved.tracker_link_id,
            "action": tracker_action.value,
        }


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def _register_approval_tool(mcp: FastMCP, mcp_state: MCPState) -> None:
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
    ) -> McpToolResult:
        svc = mcp_state.approval_service

        if action == "list":
            if not job_id:
                return {"error": "job_id is required for list"}
            approvals = await svc.list_for_job(job_id)
            return {
                "items": [
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
            }

        if action == "resolve":
            if not approval_id or not resolution:
                return {"error": "approval_id and resolution are required for resolve"}
            if resolution not in (ApprovalResolution.approved, ApprovalResolution.rejected):
                return {"error": "Resolution must be 'approved' or 'rejected'"}
            from backend.models.domain import (
                ApprovalAlreadyResolvedError,
                ApprovalNotFoundError,
            )

            try:
                a = await svc.resolve(approval_id, ApprovalResolution(resolution))
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


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


def _register_workspace_tool(mcp: FastMCP, mcp_state: MCPState) -> None:
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

        sf = mcp_state.session_factory
        config = load_config()
        async with sf() as session:
            svc = _make_job_service(mcp_state, session, config, git=False)
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
                    log.debug("list_dir_resolve_failed", item=str(item))
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


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def _register_artifact_tool(mcp: FastMCP, mcp_state: MCPState) -> None:
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
        sf = mcp_state.session_factory

        if action == "list":
            if not job_id:
                return {"error": "job_id is required for list"}
            async with sf() as session:
                svc = _make_artifact_service(session)
                artifacts = await svc.list_for_job(job_id)
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
            "\n- update: pass any combination of: max_concurrent_jobs,"
            " auto_push, cleanup_worktree,"
            " delete_branch_after_merge, artifact_retention_days,"
            " max_artifact_size_mb, auto_archive_days,"
            " verify, self_review, max_turns, verify_prompt, self_review_prompt"
        ),
    )
    async def codeplane_settings(
        action: Literal["get", "update"],
        max_concurrent_jobs: int | None = None,
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


# ---------------------------------------------------------------------------
# Repository Management
# ---------------------------------------------------------------------------


def _register_repo_tool(mcp: FastMCP, mcp_state: MCPState) -> None:
    @mcp.tool(
        name="codeplane_repo",
        title="Manage Repositories",
        annotations=ToolAnnotations(title="Manage Repositories", destructiveHint=True, openWorldHint=True),
        description=(
            "Inspect repositories managed by Projects. Actions: list, get. "
            "Create and update repository membership through codeplane_project."
            "\n\n"
            "- list: no extra params"
            "\n- get: repo_path (required)"
        ),
    )
    async def codeplane_repo(
        action: Literal["list", "get", "register", "remove"],
        repo_path: str | None = None,
        source: str | None = None,
        clone_to: str | None = None,
    ) -> McpToolResult:
        import urllib.parse

        import httpx

        config = load_config()
        base_url = f"http://{config.server.host}:{config.server.port}/api"

        async with httpx.AsyncClient(timeout=120) as client:
            if action == "list":
                resp = await client.get(f"{base_url}/settings/repos")
                resp.raise_for_status()
                result: dict[str, Any] = resp.json()
                return result

            if action == "get":
                if not repo_path:
                    return {"error": "repo_path is required for get"}
                encoded = urllib.parse.quote(repo_path, safe="")
                resp = await client.get(f"{base_url}/settings/repos/{encoded}")
                if resp.status_code == 404:
                    return {"error": f"Repository '{repo_path}' is not registered."}
                resp.raise_for_status()
                result = resp.json()
                return result

            if action == "register":
                return {
                    "error": "Standalone repository registration is unavailable; "
                    "use codeplane_project create or update."
                }

            if action == "remove":
                return {"error": "Standalone repository removal is unavailable; use codeplane_project update."}


# ---------------------------------------------------------------------------
# Project Management (Story 2.1 / CAP-6)
# ---------------------------------------------------------------------------


def _register_project_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        name="codeplane_project",
        title="Manage Projects",
        annotations=ToolAnnotations(title="Manage Projects", destructiveHint=True, openWorldHint=True),
        description=(
            "Manage Projects — the sole entity that owns repo-path membership. Actions: "
            "list, get, create, update, list_task_links."
            "\n\n"
            "- list: no extra params"
            "\n- get: project_id (required)"
            "\n- create: name (required), repo_paths (required, list of one or more repo paths)"
            "\n- update: project_id (required); name and/or repo_paths (optional, repo_paths replaces "
            "the full membership list); confirm_repo_removal confirms destructive membership removal"
            "\n- list_task_links: project_id (required) — lists the Project's currently persisted TaskLinks"
        ),
    )
    async def codeplane_project(
        action: Literal["list", "get", "create", "update", "list_task_links"],
        project_id: str | None = None,
        name: str | None = None,
        repo_paths: list[str] | None = None,
        confirm_repo_removal: bool = False,
    ) -> McpToolResult:
        import httpx

        config = load_config()
        base_url = f"http://{config.server.host}:{config.server.port}/api"

        async with httpx.AsyncClient(timeout=120) as client:
            if action == "list":
                resp = await client.get(f"{base_url}/settings/projects")
                resp.raise_for_status()
                result: dict[str, Any] = resp.json()
                return result

            if action == "get":
                if not project_id:
                    return {"error": "project_id is required for get"}
                resp = await client.get(f"{base_url}/settings/projects/{project_id}")
                if resp.status_code == 404:
                    return {"error": f"Project '{project_id}' does not exist."}
                resp.raise_for_status()
                result = resp.json()
                return result

            if action == "create":
                if not name:
                    return {"error": "name is required for create"}
                if not repo_paths:
                    return {"error": "repo_paths is required for create"}
                body: dict[str, Any] = {"name": name, "repoPaths": repo_paths}
                resp = await client.post(f"{base_url}/settings/projects", json=body)
                if resp.status_code >= 400:
                    detail = resp.json().get("detail", resp.text)
                    return {"error": str(detail)}
                result = resp.json()
                return result

            if action == "update":
                if not project_id:
                    return {"error": "project_id is required for update"}
                body = {}
                if name is not None:
                    body["name"] = name
                if repo_paths is not None:
                    body["repoPaths"] = repo_paths
                if confirm_repo_removal:
                    body["confirmRepoRemoval"] = True
                resp = await client.patch(f"{base_url}/settings/projects/{project_id}", json=body)
                if resp.status_code == 404:
                    return {"error": f"Project '{project_id}' does not exist."}
                if resp.status_code >= 400:
                    detail = resp.json().get("detail", resp.text)
                    return {"error": str(detail)}
                result = resp.json()
                return result

            if action == "list_task_links":
                if not project_id:
                    return {"error": "project_id is required for list_task_links"}
                resp = await client.get(f"{base_url}/settings/projects/{project_id}/task-links")
                if resp.status_code == 404:
                    return {"error": f"Project '{project_id}' does not exist."}
                resp.raise_for_status()
                result = resp.json()
                return result


# ---------------------------------------------------------------------------
# Health & Observability
# ---------------------------------------------------------------------------


def _register_health_tool(mcp: FastMCP, mcp_state: MCPState) -> None:
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
            sf = mcp_state.session_factory
            async with sf() as session:
                svc = _make_job_service(mcp_state, session, config)
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
            from backend.persistence.project_repo import ProjectRepository
            from backend.services.project.repo_membership import list_managed_repo_paths

            async with mcp_state.session_factory() as session:
                repo_paths = await list_managed_repo_paths(config, ProjectRepository(session))
            total = 0
            for repo in repo_paths:
                try:
                    count = await git.cleanup_worktrees(repo)
                    total += count
                except GitError:
                    log.warning("cleanup_worktrees_failed", repo=repo)
            return {"removed": total}
