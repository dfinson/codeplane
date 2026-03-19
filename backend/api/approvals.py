"""Approval resolution and operator message endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request

from backend.models.api_schemas import (
    ApprovalResponse,
    ResolveApprovalRequest,
    SendMessageRequest,
    SendMessageResponse,
    TrustJobResponse,
)
from backend.services.approval_service import (
    ApprovalAlreadyResolvedError,
    ApprovalNotFoundError,
    ApprovalService,
)

if TYPE_CHECKING:
    from backend.models.domain import Approval
    from backend.services.runtime_service import RuntimeService

router = APIRouter(tags=["approvals"])


def _to_response(a: Approval) -> ApprovalResponse:
    return ApprovalResponse(
        id=a.id,
        job_id=a.job_id,
        description=a.description,
        proposed_action=a.proposed_action,
        requested_at=a.requested_at,
        resolved_at=a.resolved_at,
        resolution=a.resolution,
    )


@router.get("/jobs/{job_id}/approvals", response_model=list[ApprovalResponse])
async def list_approvals(
    request: Request,
    job_id: str,
) -> list[ApprovalResponse]:
    """List all approvals for a job."""
    svc: ApprovalService = request.app.state.approval_service
    approvals = await svc.list_for_job(job_id)
    return [_to_response(a) for a in approvals]


@router.post("/approvals/{approval_id}/resolve", response_model=ApprovalResponse)
async def resolve_approval(
    request: Request,
    approval_id: str,
    body: ResolveApprovalRequest,
) -> ApprovalResponse:
    """Approve or reject a pending approval request."""
    svc: ApprovalService = request.app.state.approval_service
    try:
        approval = await svc.resolve(approval_id, body.resolution.value)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_response(approval)


@router.post("/jobs/{job_id}/approvals/trust", response_model=TrustJobResponse)
async def trust_job(
    request: Request,
    job_id: str,
) -> TrustJobResponse:
    """Trust a job session — auto-approve all current and future permission requests."""
    svc: ApprovalService = request.app.state.approval_service
    count = await svc.trust_job(job_id)
    return TrustJobResponse(resolved=count)


@router.post("/jobs/{job_id}/messages", response_model=SendMessageResponse)
async def send_message(
    request: Request,
    job_id: str,
    body: SendMessageRequest,
) -> SendMessageResponse:
    """Inject an operator message into a running job's agent session."""
    from datetime import UTC, datetime

    runtime: RuntimeService = request.app.state.runtime_service
    sent = await runtime.send_message(job_id, body.content)
    if not sent:
        raise HTTPException(status_code=409, detail="Job is not currently running")
    return SendMessageResponse(
        seq=0,
        timestamp=datetime.now(UTC),
    )
