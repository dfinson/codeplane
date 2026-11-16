"""Explicit same-Job continuation, separate from live attempt messaging."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from backend.models.domain import JobNotFoundError, JobSource, JobState, StateConflictError
from backend.models.events import EventKind, EventMetadata, new_event
from backend.models.schemas.recipes import RecipeCodeReference
from backend.persistence.database import serialized_write
from backend.persistence.job_repo import JobRepository
from backend.persistence.recipe_repo import RecipeRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.recipe.definition_service import RecipeDefinitionService
from backend.services.recipe.run_service import RecipeRunService
from backend.services.recipe.validation import content_checksum

if TYPE_CHECKING:
    from backend.models.domain import Job
    from backend.models.schemas.recipes import RecipeRunResponse, RecipeSelection
    from backend.services.runtime.service import RuntimeService

log = structlog.get_logger()


async def resume_recipe_job(
    host: RuntimeService,
    job_id: str,
    instruction: str | None,
    *,
    expected_run_id: str,
    start_request_key: str | None = None,
    selection: RecipeSelection | None = None,
) -> tuple[Job, RecipeRunResponse]:
    if host._shutting_down:
        raise StateConflictError("The runtime is shutting down and cannot admit continuation.")
    note = (instruction or "").strip() or "Continue the current task from where you left off and finish it."
    request_key = start_request_key or f"resume:{uuid4()}"
    start = False
    async with host._session_factory() as session:
        repository = RecipeRunRepository(session)
        before = await repository.get_for_job(job_id)
        prior_request = await repository.get_by_start_key(request_key)
    if before and before.id == expected_run_id and before.resolved_at is not None and prior_request is None:
        owner = host._tasks.get(job_id)
        if owner is not None:
            if owner is asyncio.current_task():
                raise StateConflictError("The previous runtime owner must finish before continuing this Job.")
            await asyncio.shield(owner)
    async with host._execution_control.hold(job_id):
        async with host._session_factory() as session:
            runs = RecipeRunRepository(session)
            job = await JobRepository(session).get(job_id)
            previous = await runs.get(expected_run_id)
            existing = await runs.get_by_start_key(request_key)
            current = await runs.get_for_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} does not exist.")
        if job.source != JobSource.managed:
            raise StateConflictError("Only managed Jobs admit recipe continuation.")
        if previous is None or previous.job_id != job_id:
            raise StateConflictError("The selected predecessor does not belong to this Job.")
        if existing is not None and existing.job_id != job_id:
            raise StateConflictError("This start request key belongs to another Job.")
        if previous.snapshot_checksum != content_checksum(
            previous.snapshot.model_dump(mode="json", exclude_unset=True)
        ):
            raise StateConflictError("The pinned predecessor snapshot failed its integrity check.")
        if previous.resolved_at is None:
            if current is None or current.id != previous.id or previous.cancel_requested_at is not None:
                raise StateConflictError("This unresolved run cannot be resumed before its effects are reconciled.")
            if selection is not None:
                raise StateConflictError("An unfinished run cannot adopt a different revision or input selection.")
            if job.state != JobState.failed:
                raise StateConflictError("The unfinished recipe is already active or awaiting recovery.")
            async with serialized_write(host._session_factory) as session:
                await JobRepository(session).reset_for_recovery(job.id, job.session_count)
            run = previous
            start = True
        else:
            if existing is not None and existing.continuation_of_id != previous.id:
                raise StateConflictError("This start request key identifies a different admission.")
            if existing is None and (
                current is None
                or current.id != previous.id
                or job.state not in {JobState.review, JobState.completed, JobState.failed, JobState.canceled}
            ):
                raise StateConflictError("The selected run is no longer the Job's current resolved run.")
            snapshot = previous.snapshot
            parameters = dict(previous.inputs)
            request = f"{job.prompt}\n\nContinuation instruction:\n{note}"
            if selection is not None:
                async with host._session_factory() as session:
                    service = RecipeRunService(
                        RecipeDefinitionService(RecipeRepository(session)),
                        RecipeRunRepository(session),
                    )
                    snapshot, parameters = await service.prepare_job_selection(selection, request)
            elif (
                snapshot.definition.parameters.get("request") is not None
                and snapshot.definition.parameters["request"].type == "string"
            ):
                parameters["request"] = request
            code = existing.code_ref if existing is not None else None
            if code is None:
                git = host._git_service
                if git is None:
                    raise StateConflictError("Continuation requires an exact current-code snapshot.")
                path = job.execution_workspace
                identity = hashlib.sha256(f"{job_id}:{request_key}".encode()).hexdigest()
                commit = await git.capture_worktree_commit(
                    cwd=path,
                    snapshot_ref=f"refs/codeplane/recipe-inputs/continuation-{identity}",
                )
                code = RecipeCodeReference(workspace=path, commit=commit)
            async with serialized_write(host._session_factory) as session:
                run = await RecipeRunRepository(session).create(
                    job.id,
                    snapshot,
                    parameters,
                    start_request_key=request_key,
                    continuation_of_id=previous.id,
                    instruction=note,
                    code_ref=code,
                    allow_revision_change=selection is not None,
                )
                if existing is None:
                    await JobRepository(session).reset_for_resume(
                        job.id,
                        job.session_count + 1,
                        merge_status=job.merge_status,
                    )
                    start = True
                elif (
                    run.resolved_at is None
                    and current is not None
                    and current.id == run.id
                    and job.id not in host._tasks
                    and job.id not in host._starting_tasks
                    and job.id not in host._pending_starts
                    and not await RecipeRunRepository(session).has_attempts(run.id)
                ):
                    if job.state == JobState.failed:
                        await JobRepository(session).reset_for_recovery(job.id, job.session_count)
                    start = job.state in {JobState.running, JobState.queued, JobState.failed}
        async with host._session_factory() as session:
            job = await JobRepository(session).get(job_id)
        assert job is not None
    if start:
        try:
            await host.start_or_enqueue(job, override_prompt=note)
        except Exception as exc:
            async with serialized_write(host._session_factory) as session:
                await RecipeRunRepository(session).mark_interrupted(run.id, f"Continuation startup failed: {exc}")
            await host._fail_job(job_id, f"Continuation startup failed: {exc}")
            log.error("recipe_continuation_start_failed", job_id=job_id, run_id=run.id, exc_info=True)
            raise
        await host._event_bus.publish(
            new_event(
                job_id,
                EventKind.recipe_updated,
                {"run_id": run.id, "continuation_of_id": run.continuation_of_id},
                metadata=EventMetadata(source_framework="codeplane.recipe", recipe_run_id=run.id),
            )
        )
    async with host._session_factory() as session:
        final_job = await JobRepository(session).get(job_id)
        final_run = await RecipeRunRepository(session).get(run.id)
    if final_job is None or final_run is None:
        raise StateConflictError("The admitted continuation no longer exists.")
    return final_job, final_run
