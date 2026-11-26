"""Job CRUD and control endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import CPLConfig
from backend.di import CachedModelsBySdk
from backend.models.api_schemas import (
    ContinueJobRequest,
    CreateJobRequest,
    CreateJobResponse,
    JobListResponse,
    JobResponse,
    ModelInfoResponse,
    ModelListResponse,
    ResolveGateRequest,
    ResumeJobRequest,
    SuggestNamesRequest,
    SuggestNamesResponse,
)
from backend.services.completers.naming_service import NamingService
from backend.services.events.event_bus import EventBus
from backend.services.events.ingest_service import IngestService
from backend.services.job.job_service import JobService, ProgressPreview
from backend.services.runtime import RuntimeService
from backend.services.tool_formatters import format_tool_display, format_tool_display_full

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.models.domain import Job

from backend.models.domain import JobMode, JobSpec, JobState, Preset

log = structlog.get_logger()

router = APIRouter(tags=["jobs"], route_class=DishkaRoute)


def _stringify_tool_args(arguments: Any) -> str | None:
    """Normalise a TF ``arguments`` payload (dict|str|None) to a display string."""
    if arguments is None:
        return None
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, dict):
        import json

        return json.dumps(arguments, ensure_ascii=False)
    return str(arguments)


def resolve_tool_display(payload: dict[str, Any]) -> str | None:
    """Return tool_display from payload, recomputing it from args if missing.

    Stored events pre-dating the tool_display field have no value in their
    payload, which causes the frontend to fall back to the raw tool name
    (e.g. just "Edit" instead of "Edit src/app.py").
    """
    return _resolve_display_field(payload, "tool_display", format_tool_display)


def resolve_tool_display_full(payload: dict[str, Any]) -> str | None:
    """Like _resolve_tool_display but returns the untruncated label.

    Recomputes tool_display_full from args when absent (e.g. events stored
    before this field was introduced).
    """
    return _resolve_display_field(payload, "tool_display_full", format_tool_display_full)


def _resolve_display_field(
    payload: dict[str, Any],
    field: str,
    formatter: Callable[..., str],
) -> str | None:
    stored = payload.get(field)
    if stored is not None:
        return str(stored)
    tool_name: str | None = payload.get("tool_name")
    if not tool_name:
        return None
    tool_args: str | None = _stringify_tool_args(payload.get("arguments"))
    tool_result = payload.get("result") or None  # normalise empty string → None
    tool_success: bool = payload.get("success") is not False
    return str(formatter(tool_name, tool_args, tool_result=tool_result, tool_success=tool_success))


def job_to_response(
    job: Job,
    progress_preview: ProgressPreview | None = None,
    *,
    total_cost_usd: float | int | None = None,
    total_tokens: float | int | None = None,
    input_tokens: float | int | None = None,
    output_tokens: float | int | None = None,
) -> JobResponse:
    """Map a domain Job to a JobResponse."""
    overrides: dict[str, Any] = {}
    if progress_preview is not None:
        overrides["progress_headline"] = progress_preview.headline
        overrides["progress_summary"] = progress_preview.summary
    if total_cost_usd is not None:
        overrides["total_cost_usd"] = float(total_cost_usd)
    if total_tokens is not None:
        overrides["total_tokens"] = int(total_tokens)
    if input_tokens is not None:
        overrides["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        overrides["output_tokens"] = int(output_tokens)
    return JobResponse.from_domain(job, **overrides)


def _job_to_create_response(job: Job) -> CreateJobResponse:
    """Map a domain Job to a CreateJobResponse."""
    return CreateJobResponse(
        id=job.id,
        state=job.state,
        title=job.title,
        branch=job.branch,
        worktree_path=job.worktree_path,
        sdk=job.sdk,
        copilot_credential_id=job.copilot_credential_id,
        copilot_credential_label=job.copilot_credential_label,
        created_at=job.created_at,
    )


@router.post("/jobs/suggest-names", response_model=SuggestNamesResponse)
async def suggest_names(
    body: SuggestNamesRequest,
    naming_service: FromDishka[NamingService],
) -> SuggestNamesResponse:
    """Generate a suggested title, branch name, and worktree name for a task description.

    Uses a one-shot utility session (suggest-names is called before a job exists).
    Returns 503 if the utility LLM is not configured.
    """
    from backend.services.completers.naming_service import NamingError

    try:
        title, description, branch_name, worktree_name = await naming_service.generate(
            body.prompt, copilot_credential_id=body.copilot_credential_id
        )
    except NamingError as exc:
        log.warning("naming_failed", exc_info=exc)
        raise HTTPException(status_code=503, detail="Naming failed") from exc

    return SuggestNamesResponse(
        title=title,
        description=description,
        branch_name=branch_name,
        worktree_name=worktree_name,
    )


@router.post("/jobs", response_model=CreateJobResponse, status_code=201)
async def create_job(
    body: CreateJobRequest,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    runtime_service: FromDishka[RuntimeService],
    naming_service: FromDishka[NamingService],
    event_bus: FromDishka[EventBus],
    session_factory: FromDishka[async_sessionmaker[AsyncSession]],
    config: FromDishka[CPLConfig],
) -> CreateJobResponse:
    """Create a new job.

    Returns immediately with ``state=preparing``. Workspace setup and agent
    launch happen in a background task — the frontend watches progress via
    SSE ``job_setup_progress`` events. Naming is also asynchronous: the job
    gets an instant heuristic-slug identity, and if a title wasn't already
    pre-computed by the frontend, the real LLM-generated title/description
    are filled in afterwards and pushed via SSE ``job_title_updated``.
    """
    import asyncio

    spec = JobSpec(
        repo=body.repo,
        prompt=body.prompt,
        project_id=body.project_id,
        base_ref=body.base_ref,
        branch=body.branch,
        title=body.title,
        description=body.description,
        worktree_name=body.worktree_name,
        preset=body.preset or Preset.supervised,
        model=body.model,
        sdk=body.sdk,
        copilot_credential_id=body.copilot_credential_id,
        verify=body.verify,
        self_review=body.self_review,
        max_turns=body.max_turns,
        verify_prompt=body.verify_prompt,
        self_review_prompt=body.self_review_prompt,
        enable_stall_detection=body.enable_stall_detection,
        enable_plan_tracking=body.enable_plan_tracking,
        mode=body.mode or JobMode.standard,
    )

    job = await svc.create_job(spec)

    # Commit so the job row is visible to background tasks (separate sessions)
    await session.commit()

    # For already-failed jobs (naming failure), skip background setup
    if job.state != JobState.failed:
        # Fire-and-forget background task: setup workspace → start agent
        async def _setup_and_start() -> None:
            try:
                await runtime_service.setup_and_start(
                    job,
                    session_token=body.session_token,
                )
            except Exception:
                log.error("background_job_setup_failed", job_id=job.id, exc_info=True)

        asyncio.create_task(_setup_and_start(), name=f"setup-{job.id}")

        # Fire-and-forget background task: fill in the real title/description
        # via the naming LLM (job creation itself never waits on this).
        if job.title is None:
            resolved_repo = await svc.validate_repo_async(body.repo)

            async def _enrich_naming() -> None:
                try:
                    async with session_factory() as bg_session:
                        bg_svc = JobService.from_session(
                            bg_session, config, naming_service=naming_service, event_bus=event_bus
                        )
                        await bg_svc.enrich_naming_async(job.id, spec, resolved_repo)
                        await bg_session.commit()
                except Exception:
                    log.error("background_naming_enrichment_failed", job_id=job.id, exc_info=True)

            asyncio.create_task(_enrich_naming(), name=f"naming-{job.id}")

    return _job_to_create_response(job)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    state: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    archived: Annotated[bool | None, Query()] = None,
) -> JobListResponse:
    """List jobs with optional state filter and cursor pagination.

    Pass archived=true to list only archived jobs, archived=false to
    exclude them. Default (None) returns all jobs.
    """
    jobs, next_cursor, has_more = await svc.list_jobs(
        state=state,
        limit=limit,
        cursor=cursor,
        archived=archived,
    )
    job_ids = [job.id for job in jobs]
    progress_by_job = await svc.list_latest_progress_previews(job_ids)

    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

    cost_by_job = await TelemetrySummaryRepository(session).batch_cost_tokens(job_ids)

    items = []
    for j in jobs:
        ct = cost_by_job.get(j.id, {})
        items.append(
            job_to_response(
                j,
                progress_by_job.get(j.id),
                total_cost_usd=ct.get("total_cost_usd"),
                total_tokens=ct.get("total_tokens"),
                input_tokens=ct.get("input_tokens"),
                output_tokens=ct.get("output_tokens"),
            )
        )
    return JobListResponse(items=items, cursor=next_cursor, has_more=has_more)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
) -> JobResponse:
    """Get full job detail."""
    job = await svc.get_job(job_id)
    progress_preview = await svc.get_latest_progress_preview(job_id)

    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

    ct = (await TelemetrySummaryRepository(session).batch_cost_tokens([job_id])).get(job_id, {})

    return job_to_response(
        job,
        progress_preview,
        total_cost_usd=ct.get("total_cost_usd"),
        total_tokens=ct.get("total_tokens"),
        input_tokens=ct.get("input_tokens"),
        output_tokens=ct.get("output_tokens"),
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    svc: FromDishka[JobService],
    runtime_service: FromDishka[RuntimeService],
    ingest: FromDishka[IngestService],
    session: FromDishka[AsyncSession],
) -> JobResponse:
    """Cancel a running or queued job."""
    job = await svc.cancel_job(job_id)
    # Scout finalizes through a separate session when the runtime is canceled.
    await session.commit()

    # Delegate abort to IngestService for imported sessions
    if job.source != "managed":
        await ingest.abort_session(job_id)
    else:
        # Also cancel the runtime task if running
        await runtime_service.cancel(job_id)

    return job_to_response(job)


@router.post("/jobs/{job_id}/interrupt", status_code=204)
async def interrupt_job(
    job_id: str,
    runtime_service: FromDishka[RuntimeService],
) -> None:
    """Interrupt the agent's current shell command without canceling the job.

    Sends a non-destructive interrupt (SIGINT-equivalent) to the SDK subprocess.
    The agent session stays alive and can recover or receive new instructions.
    """
    found = await runtime_service.interrupt(job_id)
    if not found:
        raise HTTPException(status_code=404, detail="No active agent session for this job")


@router.post("/jobs/{job_id}/rerun", response_model=CreateJobResponse, status_code=201)
async def rerun_job(
    job_id: str,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    runtime_service: FromDishka[RuntimeService],
) -> CreateJobResponse:
    """Create a new job from an existing job's configuration."""
    job = await svc.rerun_job(job_id)

    await session.commit()

    if job.state != JobState.failed:
        await runtime_service.start_or_enqueue(job)
        job = await svc.get_job(job.id)

    return _job_to_create_response(job)


@router.post("/jobs/{job_id}/pause", status_code=204)
async def pause_job(
    job_id: str,
    svc: FromDishka[JobService],
    runtime_service: FromDishka[RuntimeService],
) -> None:
    """Send a silent pause instruction to the agent of a running job."""
    await svc.get_job(job_id)
    sent = await runtime_service.pause_job(job_id)
    if not sent:
        raise HTTPException(status_code=409, detail="Job is not currently running")


@router.post("/jobs/{job_id}/gate/resolve", status_code=204)
async def resolve_gate(
    job_id: str,
    body: ResolveGateRequest,
    runtime_service: FromDishka[RuntimeService],
) -> None:
    """Resolve a sidecar gate — approve (resume) or keep blocked."""
    await runtime_service.resolve_sidecar_gate(job_id, body.action, body.message)


@router.post("/jobs/{job_id}/continue", response_model=CreateJobResponse, status_code=201)
async def continue_job(
    job_id: str,
    body: ContinueJobRequest,
    runtime_service: FromDishka[RuntimeService],
) -> CreateJobResponse:
    """Create a follow-up job with a new instruction and parent-job handoff context."""
    try:
        job = await runtime_service.create_followup_job(job_id, body.instruction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Follow-up instruction must not be empty") from exc

    return _job_to_create_response(job)


@router.post("/jobs/{job_id}/resume", response_model=JobResponse)
async def resume_job(
    job_id: str,
    runtime_service: FromDishka[RuntimeService],
    body: ResumeJobRequest | None = None,
) -> JobResponse:
    """Resume a completed/failed/canceled job in-place, optionally with extra instruction."""
    job = await runtime_service.resume_job(job_id, body.instruction if body is not None else None)
    return job_to_response(job)


@router.get("/models", response_model=ModelListResponse)
async def list_models(
    cached_models_by_sdk: FromDishka[CachedModelsBySdk],
    session_factory: FromDishka[async_sessionmaker[AsyncSession]],
    sdk: str | None = Query(default=None, description="SDK id (copilot | claude). Omit for default."),
    copilot_credential_id: str | None = Query(default=None, alias="copilotCredentialId", min_length=1),
) -> ModelListResponse:
    """Return the model list for the requested SDK, cached at server startup.

    If the cache is empty for the copilot SDK (e.g. auth wasn't ready at
    startup), attempt a live fetch so the user doesn't have to restart.
    """
    resolved_sdk = sdk if sdk is not None else "copilot"
    if copilot_credential_id is not None:
        from backend.services.copilot_adapter._models import fetch_copilot_models_raw
        from backend.services.inference_account import InferenceAccountError, resolve_inference_token

        if resolved_sdk != "copilot":
            raise HTTPException(status_code=400, detail="Explicit inference accounts require Copilot")
        # Never share the ambient cache or retain a revoked credential's catalogue.
        token = await resolve_inference_token(session_factory, credential_id=copilot_credential_id)
        try:
            models = await fetch_copilot_models_raw(github_token=token)
        except Exception:
            raise InferenceAccountError("Model discovery failed for the selected Copilot credential") from None
        return ModelListResponse(items=[ModelInfoResponse.model_validate(m) for m in models])
    models = cached_models_by_sdk.get(resolved_sdk, [])
    if not models and resolved_sdk == "copilot":
        try:
            from backend.services.copilot_adapter._models import fetch_copilot_models_raw

            live = await fetch_copilot_models_raw()
            if live:
                cached_models_by_sdk[resolved_sdk] = live
                models = live
        except Exception:
            log.debug("model_live_fetch_failed", sdk=resolved_sdk, exc_info=True)
    return ModelListResponse(items=[ModelInfoResponse.model_validate(m) for m in models])
