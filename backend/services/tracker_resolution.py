"""Explicit tracker identity resolution and the provider dispatch boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from backend.persistence.approval_repo import ApprovalRepository
from backend.persistence.credential_repo import CredentialRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.persistence.task_link_repo import TaskLinkRepository
from backend.persistence.tracker_link_repo import TrackerLinkRepository
from backend.persistence.tracker_operation_repo import TrackerOperationRepository
from backend.persistence.tracker_summary_repo import TrackerSummaryRepository
from backend.services.credentials.encryption import CredentialDecryptionError
from backend.services.tracker_adapter import TrackerAdapterError, TrackerAdapterInterface, build_tracker_adapters

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.schemas.tracker_operations import TrackerOperationResponse
    from backend.services.tracker_write_service import TrackerWriteRequest


class TrackerResolutionError(Exception):
    """The requested explicit tracker route cannot be used."""

    def __init__(self, message: str, *, code: str = "dependency_deleted") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolvedTracker:
    tracker_link_id: str
    credential_id: str
    ticket_ref: str
    task_link_id: str | None = None


async def resolve_tracker_for_job(session: AsyncSession, job_id: str) -> ResolvedTracker:
    """Resolve the TaskLink's explicit pair, never the first Project link."""
    task_link = await TaskLinkRepository(session).get_by_job_id(job_id)
    if task_link is None:
        raise TrackerResolutionError(f"Job '{job_id}' has no associated TaskLink")
    if not task_link.tracker_link_id or not task_link.tracker_ticket_ref:
        raise TrackerResolutionError(f"TaskLink for job '{job_id}' has no explicit TrackerLink/ticket pair")
    link = await TrackerLinkRepository(session).get(task_link.tracker_link_id)
    if link is None or link["project_id"] != task_link.project_id:
        raise TrackerResolutionError(
            f"TrackerLink '{task_link.tracker_link_id}' is not attached to Project '{task_link.project_id}'"
        )
    return ResolvedTracker(
        tracker_link_id=link["id"],
        credential_id=link["credential_id"],
        ticket_ref=task_link.tracker_ticket_ref,
        task_link_id=task_link.id,
    )


async def validate_operation_dependencies(
    session: AsyncSession,
    operation: TrackerOperationResponse,
    *,
    require_approval: bool = False,
) -> None:
    """Validate live correlations without repairing a deleted FK from its snapshot."""
    if not operation.project_id or not operation.job_id or not operation.tracker_link_id:
        raise TrackerResolutionError("A required tracker operation dependency was deleted")
    if operation.task_link_ref is not None and operation.task_link_id is None:
        raise TrackerResolutionError("The originating TaskLink was deleted")
    job = await JobRepository(session).get(operation.job_id)
    project = await ProjectRepository(session).get(operation.project_id)
    target = await TrackerSummaryRepository(session).get_target_by_link_id(operation.tracker_link_id)
    if job is None or project is None or target is None:
        raise TrackerResolutionError("A required Job, Project, TrackerLink, or credential was deleted")
    if (
        operation.job_id != operation.job_ref
        or operation.project_id != operation.project_ref
        or operation.tracker_link_id != operation.tracker_link_ref
        or job.project_id != operation.project_ref
        or target["project_id"] != operation.project_ref
        or target["credential_id"] != operation.credential_id_ref
        or target["provider"] != operation.provider
        or target["base_url"] != operation.base_url
        or target["external_ref"] != operation.external_ref
    ):
        raise TrackerResolutionError("The approved tracker route has changed", code="route_changed")
    if operation.task_link_ref is not None:
        task = await TaskLinkRepository(session).get(operation.task_link_ref)
        if task is None:
            raise TrackerResolutionError("The originating TaskLink was deleted")
        if (
            operation.task_link_id != operation.task_link_ref
            or task.project_id != operation.project_ref
            or task.job_id != operation.job_ref
            or task.tracker_link_id != operation.tracker_link_ref
            or task.tracker_ticket_ref != operation.ticket_ref
        ):
            raise TrackerResolutionError("The originating TaskLink route has changed", code="route_changed")
    if require_approval:
        if not operation.approval_id or operation.approval_id != operation.approval_ref:
            raise TrackerResolutionError("The canonical tracker approval was deleted")
        approval = await ApprovalRepository(session).get(operation.approval_id)
        if approval is None:
            raise TrackerResolutionError("The canonical tracker approval was deleted")
        if (
            approval.job_id != operation.job_ref
            or approval.resolution != "approved"
            or not approval.requires_explicit_approval
            or operation.approval_snapshot is None
            or approval.proposed_action != operation.approval_snapshot.proposed_action
            or approval.requested_at.replace(tzinfo=None)
            != operation.approval_snapshot.requested_at.replace(tzinfo=None)
        ):
            raise TrackerResolutionError(
                "The canonical approval no longer authorizes this request",
                code="approval_changed",
            )


async def dispatch_tracker_write(
    session_factory: async_sessionmaker[AsyncSession],
    request: TrackerWriteRequest,
    *,
    adapters: dict[str, TrackerAdapterInterface] | None = None,
) -> dict[str, str | int] | None:
    """Resolve credentials at point of use; never persist or log their secrets."""
    async with session_factory() as session:
        if request.operation_id is not None:
            operation = await TrackerOperationRepository(session).get(request.operation_id)
            if operation is None or operation.state != "dispatching":
                raise TrackerAdapterError("Tracker dispatch is not authorized", code="approval_changed")
            try:
                await validate_operation_dependencies(session, operation, require_approval=True)
            except TrackerResolutionError as exc:
                raise TrackerAdapterError(str(exc), code=exc.code) from exc
            if (
                request.tracker_link_id != operation.tracker_link_ref
                or request.ticket_ref != operation.ticket_ref
                or request.action.value != operation.action
                or request.value != operation.value
            ):
                raise TrackerAdapterError("Tracker dispatch payload changed", code="route_changed")
        target = await TrackerSummaryRepository(session).get_target_by_link_id(request.tracker_link_id)
        if target is None:
            raise TrackerAdapterError("TrackerLink or credential no longer exists", code="dependency_deleted")
        try:
            token = await CredentialRepository(session).resolve_secret(target["credential_id"])
        except (CredentialDecryptionError, OSError, UnicodeError, ValueError) as exc:
            raise TrackerAdapterError(
                "Tracker credential could not be resolved",
                code="credential_unavailable",
            ) from exc
    if token is None:
        raise TrackerAdapterError("Tracker credential could not be resolved", code="credential_unavailable")

    async def write(adapter: TrackerAdapterInterface) -> dict[str, str | int] | None:
        return await adapter.write(
            base_url=target["base_url"],
            external_ref=target["external_ref"],
            token=token,
            ticket_ref=request.ticket_ref,
            action=request.action.value,
            value=request.value,
            email=target["email"],
        )

    if adapters is not None:
        adapter = adapters.get(target["provider"])
        if adapter is None:
            raise TrackerAdapterError("Unsupported tracker provider", code="invalid_request")
        return await write(adapter)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=5.0)) as client:
        adapter = build_tracker_adapters(client).get(target["provider"])
        if adapter is None:
            raise TrackerAdapterError("Unsupported tracker provider", code="invalid_request")
        return await write(adapter)
