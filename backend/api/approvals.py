"""Approval resolution and operator message endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.api_schemas import (
    ApprovalListResponse,
    ApprovalResponse,
    ResolveApprovalRequest,
    ResolveBatchRequest,
    ResolveBatchResponse,
    SendMessageRequest,
    SendMessageResponse,
    TrustJobResponse,
)
from backend.services.events.ingest_service import IngestService
from backend.services.job.approval_service import ApprovalService
from backend.services.job.job_service import JobService
from backend.services.recipe.recipe_service import RecipeService
from backend.services.runtime import RuntimeService
from backend.services.task_execution_service import parse_spawn_action
from backend.services.tracker_write_service import TrackerWriteService

if TYPE_CHECKING:
    from backend.models.domain import Approval

router = APIRouter(tags=["approvals"], route_class=DishkaRoute)


def _to_response(approval: Approval) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id,
        job_id=approval.job_id,
        description=approval.description,
        proposed_action=approval.proposed_action,
        requested_at=approval.requested_at,
        resolved_at=approval.resolved_at,
        resolution=approval.resolution,
        requires_explicit_approval=approval.requires_explicit_approval,
        notes=approval.notes,
    )


@router.get("/jobs/{job_id}/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    job_id: str,
    approval_service: FromDishka[ApprovalService],
) -> ApprovalListResponse:
    """List all approvals for a job."""
    approvals = await approval_service.list_for_job(job_id)
    return ApprovalListResponse(items=[_to_response(a) for a in approvals])


@router.post("/approvals/{approval_id}/resolve", response_model=ApprovalResponse)
async def resolve_approval(
    approval_id: str,
    body: ResolveApprovalRequest,
    background_tasks: BackgroundTasks,
    approval_service: FromDishka[ApprovalService],
    recipe_service: FromDishka[RecipeService],
    runtime_service: FromDishka[RuntimeService],
    session: FromDishka[AsyncSession],
    tracker_write_service: FromDishka[TrackerWriteService],
) -> ApprovalResponse:
    """Approve or reject a pending approval request.

    Story 5.4 (AC #1): when an approval created for a gated chain's dependent
    spawn (``proposed_action`` of the form ``"spawn_task:{task_link_id}"``) is
    approved, the deferred spawn is performed here and the new job is started.
    Rejection (AC #3) needs no extra handling — the TaskLink simply keeps
    ``job_id = None``, and a later dependency-satisfying event creates a fresh,
    independent approval on its own.
    """
    approval = await approval_service.resolve(approval_id, body.resolution, notes=body.notes)

    tracker_write_service.notify_approval_resolved(approval)
    spawn_action = parse_spawn_action(approval.proposed_action)
    if approval.resolution == "approved" and spawn_action is not None:
        task_link_id = spawn_action["taskLinkId"]
        job = await recipe_service.spawn_approved_task_link(task_link_id, parent_job_id=approval.job_id)
        if job is not None:
            await session.commit()
            background_tasks.add_task(runtime_service.setup_and_start, job)

    return _to_response(approval)


@router.post("/jobs/{job_id}/approvals/trust", response_model=TrustJobResponse)
async def trust_job(
    job_id: str,
    approval_service: FromDishka[ApprovalService],
    runtime_service: FromDishka[RuntimeService],
) -> TrustJobResponse:
    """Trust a job session — auto-approve all current and future permission requests."""
    count = await approval_service.trust_job(job_id)
    await runtime_service.trust_job_policy(job_id)
    return TrustJobResponse(resolved=count)


@router.post("/jobs/{job_id}/messages", response_model=SendMessageResponse)
async def send_message(
    job_id: str,
    body: SendMessageRequest,
    runtime_service: FromDishka[RuntimeService],
    ingest: FromDishka[IngestService],
    job_service: FromDishka[JobService],
) -> SendMessageResponse:
    """Inject an operator message into a running job's agent session."""
    from datetime import UTC, datetime

    # Delegate to IngestService for imported sessions
    from backend.services.completers.copilot_steer import NativeDeliveryError

    try:
        sent = await runtime_service.send_operator_message(job_id, body.content)
    except NativeDeliveryError as exc:
        raise HTTPException(
            status_code=503,
            detail={"reason": "delivery_unknown" if exc.uncertain else "delivery_failed", "message": str(exc)},
        ) from exc
    if not sent:
        raise HTTPException(
            status_code=409,
            detail="Job is not running and could not be auto-resumed",
        )
    return SendMessageResponse(
        seq=0,
        timestamp=datetime.now(UTC),
    )


@router.post("/jobs/{job_id}/batches/resolve", response_model=ResolveBatchResponse)
async def resolve_batch(
    job_id: str,
    body: ResolveBatchRequest,
    runtime_service: FromDishka[RuntimeService],
) -> ResolveBatchResponse:
    """Resolve a pending action policy batch for a job."""
    resolved = await runtime_service.resolve_policy_batch(
        job_id=job_id,
        batch_id=body.batch_id,
        resolution=body.resolution,
        approved_ids=body.approved_ids,
        trust_grant_id=body.trust_grant_id,
    )
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail="Batch not found or already resolved",
        )
    return ResolveBatchResponse(resolved=True)
