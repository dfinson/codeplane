"""Approval-gated tracker outbox; application-owned progress survives caller loss."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

import httpx
import structlog

from backend.models.domain import (
    Approval,
    ApprovalAlreadyResolvedError,
    ApprovalNotFoundError,
    ApprovalResolution,
    StateConflictError,
)
from backend.models.events import new_event
from backend.models.schemas.tracker_operations import (
    ReconcileTrackerOperationRequest,
    RetryTrackerOperationRequest,
    TrackerApprovalSnapshot,
    TrackerOperationResponse,
    TrackerOperationState,
    TrackerProviderReceipt,
    TrackerReconciliation,
)
from backend.persistence.approval_repo import ApprovalRepository
from backend.persistence.database import serialized_write
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.persistence.task_link_repo import TaskLinkRepository
from backend.persistence.tracker_operation_repo import TrackerOperationNotFoundError, TrackerOperationRepository
from backend.persistence.tracker_summary_repo import TrackerSummaryRepository
from backend.services.tracker_adapter import (
    TrackerAdapterError,
    normalize_tracker_target,
    sanitize_tracker_text,
)
from backend.services.tracker_resolution import TrackerResolutionError, validate_operation_dependencies

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.services.events.event_bus import EventBus
    from backend.services.job.approval_service import ApprovalService

log = structlog.get_logger()
_ACTIVE = ("awaiting_approval", "approved")
_TERMINAL = ("applied", "rejected", "failed", "uncertain", "not_applied")
_POLL_SECONDS = 0.05
_CLAIM_SECONDS = 30


class TrackerWriteAction(StrEnum):
    comment = "comment"
    transition = "transition"


@dataclass(frozen=True)
class TrackerWriteRequest:
    tracker_link_id: str
    ticket_ref: str
    action: TrackerWriteAction
    value: str
    # Internal correlation for the production provider boundary, not a new caller obligation.
    operation_id: str | None = field(default=None, compare=False, repr=False)

    def approval_description(self) -> str:
        return f"{self.action.value.capitalize()} on tracker ticket {self.ticket_ref}?"

    def proposed_action(self) -> str:
        return json.dumps(
            {
                "action": self.action.value,
                "ticketRef": self.ticket_ref,
                "trackerLinkId": self.tracker_link_id,
                "value": self.value,
            },
            separators=(",", ":"),
            sort_keys=True,
        )


TrackerWriteDispatcher = Callable[[TrackerWriteRequest], Awaitable[dict[str, str | int] | None]]
# Preserve the public enum import while exposing every durable phase.
TrackerWriteState = TrackerOperationState


@dataclass(frozen=True)
class TrackerWriteOutcome:
    state: TrackerWriteState
    error: str | None = None
    operation_id: str | None = None
    target_kind: str | None = None
    normalized_target: str | None = None

    @property
    def applied(self) -> bool:
        return self.state == TrackerWriteState.applied


def _request(operation: TrackerOperationResponse) -> TrackerWriteRequest:
    return TrackerWriteRequest(
        operation.tracker_link_ref,
        operation.ticket_ref,
        TrackerWriteAction(operation.action),
        operation.value,
        operation_id=operation.id,
    )


def _action(operation: TrackerOperationResponse) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operationId": operation.id,
        "action": operation.action,
        "ticketRef": operation.ticket_ref,
        "trackerLinkId": operation.tracker_link_ref,
        "value": operation.value,
        "projectRef": operation.project_ref,
        "jobRef": operation.job_ref,
        "taskLinkRef": operation.task_link_ref,
        "provider": operation.provider,
        "baseUrl": operation.base_url,
        "externalRef": operation.external_ref,
        "credentialIdRef": operation.credential_id_ref,
        "targetKind": operation.target_kind,
        "normalizedValue": operation.normalized_value,
    }


def _parse_action(approval: Approval) -> dict[str, Any] | None:
    try:
        action = json.loads(approval.proposed_action or "")
    except (ValueError, TypeError):
        return None
    if not isinstance(action, dict) or action.get("action") not in {"comment", "transition"}:
        return None
    if any(not isinstance(action.get(key), str) for key in ("trackerLinkId", "ticketRef", "value")):
        return None
    return action


def _matches(operation: TrackerOperationResponse, approval: Approval) -> bool:
    if approval.job_id != operation.job_ref:
        return False
    action = _parse_action(approval)
    if action is None:
        return False
    if operation.source_key == f"legacy:{approval.id}":
        return bool(action == json.loads(_request(operation).proposed_action()))
    return action == _action(operation)


def _snapshot(approval: Approval) -> TrackerApprovalSnapshot:
    return TrackerApprovalSnapshot(
        id=approval.id,
        job_id=approval.job_id,
        description=approval.description,
        proposed_action=approval.proposed_action,
        requested_at=approval.requested_at,
        resolved_at=approval.resolved_at,
        resolution=approval.resolution,
        requires_explicit_approval=approval.requires_explicit_approval,
        notes=sanitize_tracker_text(approval.notes) if approval.notes is not None else None,
    )


def _failure(exc: Exception) -> tuple[TrackerWriteState, str, str]:
    """Only transport/provider evidence can establish non-delivery after dispatch."""
    if isinstance(exc, TrackerAdapterError):
        state = TrackerWriteState.uncertain if exc.delivery_uncertain else TrackerWriteState.failed
        return state, exc.code, sanitize_tracker_text(str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        definite = 400 <= status < 500 and status != 408
        return (
            TrackerWriteState.failed if definite else TrackerWriteState.uncertain,
            "provider_rejected" if definite else "provider_response_ambiguous",
            f"Tracker provider returned HTTP {status}",
        )
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return TrackerWriteState.failed, "connection_not_established", "Tracker connection was not established"
    if isinstance(exc, (httpx.HTTPError, ConnectionError, TimeoutError, OSError)):
        return TrackerWriteState.uncertain, "transport_ambiguous", "Tracker submission may have reached the provider"
    return (
        TrackerWriteState.uncertain,
        "dispatch_error",
        sanitize_tracker_text(str(exc)) or "Tracker dispatch did not return an acknowledgement",
    )


class TrackerWriteService:
    def __init__(
        self,
        approval_service: ApprovalService,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._approval_service = approval_service
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._worker: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._closed = False
        self._claims: dict[str, str] = {}
        self._dispatch_overrides: dict[str, tuple[TrackerWriteDispatcher, TrackerWriteRequest]] = {}

    def _factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("Durable tracker operations require a session factory")
        return self._session_factory

    async def execute(
        self,
        job_id: str,
        request: TrackerWriteRequest,
        dispatch: TrackerWriteDispatcher | None = None,
        *,
        source_key: str | None = None,
        task_link_id: str | None = None,
    ) -> TrackerWriteOutcome:
        self._factory()
        key = source_key if source_key is not None else f"agent:{uuid.uuid4()}"
        if dispatch is not None:
            self._dispatch_overrides[key] = (dispatch, request)
        try:
            operation = await self.enqueue(job_id, request, source_key=key, task_link_id=task_link_id)
        except Exception:
            self._dispatch_overrides.pop(key, None)
            raise
        while operation.state not in _TERMINAL:
            await asyncio.sleep(_POLL_SECONDS)
            operation = await self.get_operation(operation.id)
        self._dispatch_overrides.pop(key, None)
        return TrackerWriteOutcome(
            operation.state,
            operation.error_detail,
            operation.id,
            operation.target_kind,
            operation.normalized_value,
        )

    async def enqueue(
        self,
        job_id: str,
        request: TrackerWriteRequest,
        *,
        source_key: str | None = None,
        task_link_id: str | None = None,
    ) -> TrackerOperationResponse:
        if self._closed:
            raise StateConflictError("Tracker worker is closed")
        self.wake()
        async with serialized_write(self._factory()) as session:
            operation = await self.enqueue_in_session(
                session,
                job_id,
                request,
                source_key=source_key,
                task_link_id=task_link_id,
            )
        # Wake before notifying observers: their failure/cancellation cannot strand the intent.
        self.wake()
        await self._publish(operation)
        return operation

    async def enqueue_in_session(
        self,
        session: AsyncSession,
        job_id: str,
        request: TrackerWriteRequest,
        *,
        source_key: str | None = None,
        task_link_id: str | None = None,
    ) -> TrackerOperationResponse:
        """Stage intent in the caller's transaction; no approval, event, or worker side effect."""
        key = source_key if source_key is not None else f"agent:{uuid.uuid4()}"
        if not key.strip():
            raise ValueError("Tracker source identity must not be blank")
        repo = TrackerOperationRepository(session)
        existing = await repo.get_by_source_key(key)
        if existing is not None:
            if (
                existing.job_ref != job_id
                or existing.task_link_ref != task_link_id
                or existing.tracker_link_ref != request.tracker_link_id
                or existing.ticket_ref != request.ticket_ref
                or existing.action != request.action.value
                or existing.value != request.value
            ):
                raise StateConflictError("Tracker source identity already names another request")
            return existing
        operation = await self._new_operation(
            session,
            job_id,
            request,
            source_key=key,
            task_link_id=task_link_id,
        )
        return await repo.create(operation)

    async def _new_operation(
        self,
        session: AsyncSession,
        job_id: str,
        request: TrackerWriteRequest,
        *,
        source_key: str,
        task_link_id: str | None,
    ) -> TrackerOperationResponse:
        job = await JobRepository(session).get(job_id)
        if job is None:
            raise TrackerResolutionError("Tracker operation Job does not exist")
        if await ProjectRepository(session).get(job.project_id) is None:
            raise TrackerResolutionError("Tracker operation Project does not exist")
        target = await TrackerSummaryRepository(session).get_target_by_link_id(request.tracker_link_id)
        if target is None:
            raise TrackerResolutionError("TrackerLink or credential no longer exists")
        if target["project_id"] != job.project_id:
            raise TrackerResolutionError("TrackerLink belongs to another Project", code="route_changed")
        if task_link_id is not None:
            task = await TaskLinkRepository(session).get(task_link_id)
            if task is None:
                raise TrackerResolutionError("Originating TaskLink does not exist")
            if (
                task.project_id != job.project_id
                or task.job_id != job.id
                or task.tracker_link_id != request.tracker_link_id
                or task.tracker_ticket_ref != request.ticket_ref
            ):
                raise TrackerResolutionError("TaskLink does not name this Job and tracker route", code="route_changed")
        kind, normalized = normalize_tracker_target(target["provider"], request.action.value, request.value)
        now = datetime.now(UTC)
        return TrackerOperationResponse(
            id=str(uuid.uuid4()),
            source_key=source_key,
            project_id=job.project_id,
            project_ref=job.project_id,
            job_id=job.id,
            job_ref=job.id,
            task_link_id=task_link_id,
            task_link_ref=task_link_id,
            tracker_link_id=request.tracker_link_id,
            tracker_link_ref=request.tracker_link_id,
            provider=target["provider"],
            base_url=target["base_url"],
            external_ref=target["external_ref"],
            credential_id_ref=target["credential_id"],
            ticket_ref=request.ticket_ref,
            action=request.action.value,
            target_kind=kind,
            value=request.value,
            normalized_value=normalized,
            state=TrackerOperationState.awaiting_approval,
            version=0,
            created_at=now,
            updated_at=now,
        )

    def wake(self) -> None:
        """Start or wake the single application-owned worker, never a request-owned task."""
        if self._closed:
            return
        self._factory()
        self._wake_event.set()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="tracker-outbox")

    def notify_approval_resolved(self, approval: Approval) -> None:
        if _parse_action(approval) is not None:
            self.wake()

    async def close(self) -> None:
        self._closed = True
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        if self._claims:
            async with serialized_write(self._factory()) as session:
                repo = TrackerOperationRepository(session)
                for operation_id, token in self._claims.items():
                    await repo.release_claim(operation_id, token, datetime.now(UTC))
            self._claims.clear()
        self._dispatch_overrides.clear()

    async def get_operation(
        self,
        operation_id: str,
        *,
        project_ref: str | None = None,
    ) -> TrackerOperationResponse:
        async with self._factory()() as session:
            operation = await TrackerOperationRepository(session).get(operation_id)
        if operation is None or (project_ref is not None and operation.project_ref != project_ref):
            raise TrackerOperationNotFoundError("Tracker operation not found")
        return operation

    async def list_operations(
        self,
        project_ref: str,
        *,
        job_ref: str | None = None,
        tracker_link_ref: str | None = None,
    ) -> list[TrackerOperationResponse]:
        async with self._factory()() as session:
            return await TrackerOperationRepository(session).list_operations(
                project_ref,
                job_ref=job_ref,
                tracker_link_ref=tracker_link_ref,
            )

    async def list_operations_for_job(self, job_ref: str) -> list[TrackerOperationResponse]:
        async with self._factory()() as session:
            return await TrackerOperationRepository(session).list_for_job_ref(job_ref)

    async def reconcile_operation(
        self,
        project_ref: str,
        operation_id: str,
        *,
        expected_version: int,
        resolution: Literal["applied", "not_applied"],
        evidence: str,
        actor: str,
    ) -> TrackerOperationResponse:
        validated = ReconcileTrackerOperationRequest(
            expected_version=expected_version,
            resolution=resolution,
            evidence=evidence,
        )
        if not actor.strip():
            raise ValueError("Reconciliation requires an authenticated actor")
        now = datetime.now(UTC)
        async with serialized_write(self._factory()) as session:
            repo = TrackerOperationRepository(session)
            operation = await self._scoped(repo, project_ref, operation_id)
            if operation.version != validated.expected_version or operation.state != "uncertain":
                raise StateConflictError("Only the expected uncertain operation can be reconciled")
            updated = await repo.compare_and_set(
                operation,
                now=now,
                changes={
                    "state": validated.resolution,
                    "completed_at": now,
                    "reconciliation": TrackerReconciliation(
                        resolution=validated.resolution,
                        evidence=sanitize_tracker_text(validated.evidence, limit=4000),
                        actor=sanitize_tracker_text(actor, limit=200),
                        recorded_at=now,
                    ),
                },
            )
            if updated is None:
                raise StateConflictError("Tracker operation changed while reconciling")
        await self._publish(updated)
        return updated

    async def retry_operation(
        self,
        project_ref: str,
        operation_id: str,
        *,
        expected_version: int,
        request_id: str,
    ) -> TrackerOperationResponse:
        if self._closed:
            raise StateConflictError("Tracker worker is closed")
        validated = RetryTrackerOperationRequest(expected_version=expected_version, request_id=request_id)
        source_key = json.dumps(["retry", operation_id, validated.request_id], separators=(",", ":"))
        touched = None
        async with serialized_write(self._factory()) as session:
            repo = TrackerOperationRepository(session)
            original = await self._scoped(repo, project_ref, operation_id)
            existing = await repo.get_by_source_key(source_key)
            if existing is not None:
                if existing.retry_of_id != original.id:
                    raise StateConflictError("Retry source identity already names another request")
                created = existing
            else:
                if original.version != validated.expected_version or original.state not in {"failed", "not_applied"}:
                    raise StateConflictError("Retry requires the expected failed or verified not-applied operation")
                await validate_operation_dependencies(session, original)
                retry = await self._new_operation(
                    session,
                    original.job_ref,
                    _request(original),
                    source_key=source_key,
                    task_link_id=original.task_link_ref,
                )
                retry.retry_of_id = original.id
                touched = await repo.compare_and_set(original, now=datetime.now(UTC), changes={})
                if touched is None:
                    raise StateConflictError("Another tracker retry or reconciliation won")
                created = await repo.create(retry)
        self.wake()
        if touched is not None:
            await self._publish(touched)
        await self._publish(created)
        return created

    @staticmethod
    async def _scoped(
        repo: TrackerOperationRepository,
        project_ref: str,
        operation_id: str,
    ) -> TrackerOperationResponse:
        operation = await repo.get(operation_id)
        if operation is None or operation.project_ref != project_ref:
            raise TrackerOperationNotFoundError("Tracker operation not found")
        return operation

    async def _publish(self, operation: TrackerOperationResponse) -> None:
        if self._event_bus is None:
            return
        try:
            async with self._factory()() as session:
                repo = TrackerOperationRepository(session)
                current = await repo.get(operation.id)
                if current is None:
                    return
                job_id = await repo.live_job_id(current)
            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    kind="tracker.operation_updated",
                    payload={"operation": current.model_dump(mode="json", by_alias=True)},
                )
            )
        except Exception as exc:
            # Persistence already committed. Subscriber failure cannot change the delivery outcome.
            log.warning("tracker_operation_event_failed", operation_id=operation.id, error_type=type(exc).__name__)

    async def _change(
        self,
        operation: TrackerOperationResponse,
        **changes: Any,
    ) -> TrackerOperationResponse | None:
        async with serialized_write(self._factory()) as session:
            updated = await TrackerOperationRepository(session).compare_and_set(
                operation,
                now=datetime.now(UTC),
                changes=changes,
            )
        if updated is not None:
            await self._publish(updated)
        return updated

    async def _fail(
        self,
        operation: TrackerOperationResponse,
        code: str,
        detail: str,
    ) -> TrackerOperationResponse | None:
        return await self._change(
            operation,
            state="failed",
            error_code=code,
            error_detail=sanitize_tracker_text(detail),
            claim_token=None,
            claim_expires_at=None,
            completed_at=datetime.now(UTC),
        )

    async def _run(self) -> None:
        while not self._closed:
            self._wake_event.clear()
            try:
                async with self._factory()() as session:
                    operations = await TrackerOperationRepository(session).list_states(*_ACTIVE)
                if not operations and not self._wake_event.is_set():
                    return
                for operation in operations:
                    if self._closed:
                        return
                    try:
                        await self._advance(operation)
                    except Exception as exc:
                        log.error(
                            "tracker_worker_operation_error", operation_id=operation.id, error_type=type(exc).__name__
                        )
                        await self._interrupted_dispatch(operation.id)
            except Exception as exc:
                log.error("tracker_worker_iteration_error", error_type=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake_event.wait(), timeout=_POLL_SECONDS)

    async def _advance(self, operation: TrackerOperationResponse) -> None:
        try:
            async with self._factory()() as session:
                await validate_operation_dependencies(session, operation)
        except TrackerResolutionError as exc:
            await self._fail(operation, exc.code, str(exc))
            return
        if operation.approval_ref is None:
            provisioned = await self._provision(operation)
            if provisioned is None:
                return
            operation = provisioned
        if operation.approval_id is None:
            await self._fail(operation, "dependency_deleted", "The canonical tracker approval was deleted")
            return
        decision = await self._approval_service.get(operation.approval_id)
        if decision is None:
            await self._fail(operation, "dependency_deleted", "The canonical tracker approval was deleted")
            return
        if not _matches(operation, decision) or not decision.requires_explicit_approval:
            await self._fail(operation, "approval_changed", "Canonical explicit approval does not match the request")
            return
        if decision.resolution is None:
            return
        if decision.resolution == ApprovalResolution.rejected:
            await self._change(
                operation,
                state="rejected",
                approval_snapshot=_snapshot(decision),
                completed_at=datetime.now(UTC),
            )
            return
        if operation.state == "awaiting_approval":
            approved = await self._change(operation, state="approved", approval_snapshot=_snapshot(decision))
            if approved is None:
                return
            operation = approved
        await self._dispatch(operation)

    async def _provision(
        self,
        operation: TrackerOperationResponse,
        *,
        create: bool = True,
    ) -> TrackerOperationResponse | None:
        token = str(uuid.uuid4())
        now = datetime.now(UTC)
        async with serialized_write(self._factory()) as session:
            claimed = await TrackerOperationRepository(session).claim(
                operation.id,
                expected_version=operation.version,
                token=token,
                now=now,
                expires_at=now + timedelta(seconds=_CLAIM_SECONDS),
            )
        if claimed is None:
            return None
        self._claims[operation.id] = token
        candidates = await self._approval_service.list_for_job(operation.job_ref)
        if not any(_matches(operation, candidate) for candidate in candidates):
            if not create:
                async with serialized_write(self._factory()) as session:
                    await TrackerOperationRepository(session).release_claim(operation.id, token, datetime.now(UTC))
                self._claims.pop(operation.id, None)
                return None
            description = _request(operation).approval_description()
            if operation.target_kind == "github_issue_state":
                description += f" GitHub issue state: {operation.normalized_value} (not Project Status)."
            # No open session or writer lock crosses this call.
            await self._approval_service.create_request(
                operation.job_ref,
                description,
                proposed_action=json.dumps(_action(operation), separators=(",", ":"), sort_keys=True),
                requires_explicit_approval=True,
            )
        async with serialized_write(self._factory()) as session:
            repo = TrackerOperationRepository(session)
            current = await repo.get(operation.id)
            candidates = await ApprovalRepository(session).list_for_job(operation.job_ref)
            matching = sorted(
                (candidate for candidate in candidates if _matches(operation, candidate)),
                key=lambda candidate: (candidate.requested_at.replace(tzinfo=UTC), candidate.id),
            )
            bound = None
            if current is not None and current.version == claimed.version and current.approval_ref is None and matching:
                canonical = matching[0]
                bound = await repo.compare_and_set(
                    current,
                    now=datetime.now(UTC),
                    claim_token=token,
                    changes={
                        "approval_id": canonical.id,
                        "approval_ref": canonical.id,
                        "approval_snapshot": _snapshot(canonical),
                        "claim_token": None,
                        "claim_expires_at": None,
                    },
                )
        self._claims.pop(operation.id, None)
        if bound is not None:
            await self._publish(bound)
        # A stale claimant may retire extras, but may not dispatch from its stale result.
        latest = bound or await self.get_operation(operation.id)
        if latest.approval_ref is not None:
            await self._reject_extras(latest)
        return bound

    async def _reject_extras(self, operation: TrackerOperationResponse) -> None:
        for candidate in await self._approval_service.list_for_job(operation.job_ref):
            action = _parse_action(candidate)
            if candidate.id == operation.approval_ref or action is None or action.get("operationId") != operation.id:
                continue
            try:
                await self._approval_service.resolve(
                    candidate.id,
                    ApprovalResolution.rejected,
                    notes=f"Superseded by canonical tracker approval {operation.approval_ref}.",
                )
            except (ApprovalAlreadyResolvedError, ApprovalNotFoundError):
                # Resolve CAS deliberately cannot rewrite an already-recorded operator decision.
                log.info("tracker_noncanonical_approval_ignored", operation_id=operation.id, approval_id=candidate.id)

    async def _dispatch(self, operation: TrackerOperationResponse) -> None:
        async with serialized_write(self._factory()) as session:
            repo = TrackerOperationRepository(session)
            current = await repo.get(operation.id)
            if current is None or current.state != "approved" or current.version != operation.version:
                return
            try:
                await validate_operation_dependencies(session, current, require_approval=True)
            except TrackerResolutionError as exc:
                changed = await repo.compare_and_set(
                    current,
                    now=datetime.now(UTC),
                    changes={
                        "state": "failed",
                        "error_code": exc.code,
                        "error_detail": str(exc),
                        "completed_at": datetime.now(UTC),
                    },
                )
            else:
                changed = await repo.compare_and_set(
                    current,
                    now=datetime.now(UTC),
                    changes={
                        "state": "dispatching",
                        "dispatch_started_at": datetime.now(UTC),
                        "claim_token": None,
                        "claim_expires_at": None,
                    },
                )
        if changed is None:
            return
        try:
            await self._publish(changed)
            if changed.state != "dispatching":
                return
            override = self._dispatch_overrides.get(changed.source_key)
            try:
                if override is not None:
                    receipt = await override[0](override[1])
                else:
                    from backend.services.tracker_resolution import dispatch_tracker_write

                    receipt = await dispatch_tracker_write(self._factory(), _request(changed))
            except Exception as exc:
                state, code, detail = _failure(exc)
                log.warning(
                    "tracker_dispatch_outcome", operation_id=changed.id, state=state, error_type=type(exc).__name__
                )
                await self._change(
                    changed,
                    state=state,
                    error_code=code,
                    error_detail=detail,
                    completed_at=datetime.now(UTC),
                )
            else:
                safe_receipt = None
                if isinstance(receipt, dict):
                    from backend.services.tracker_adapter import safe_tracker_receipt

                    safe_receipt = TrackerProviderReceipt.model_validate(safe_tracker_receipt(receipt))
                await self._change(
                    changed,
                    state="applied",
                    provider_receipt=safe_receipt,
                    completed_at=datetime.now(UTC),
                )
        except asyncio.CancelledError:
            await self._interrupted_dispatch(operation.id)
            raise
        finally:
            self._dispatch_overrides.pop(operation.source_key, None)

    async def _interrupted_dispatch(self, operation_id: str) -> None:
        operation = await self.get_operation(operation_id)
        if operation.state == "dispatching":
            await self._change(
                operation,
                state="uncertain",
                error_code="dispatch_interrupted",
                error_detail="Dispatch may have reached the provider; reconcile delivery before retrying",
                completed_at=datetime.now(UTC),
            )

    async def recover_on_startup(self) -> None:
        """Rebuild associations before admission; wake() explicitly starts outbound work."""
        if self._worker is not None and not self._worker.done():
            raise StateConflictError("Tracker recovery must precede starting its worker")
        async with self._factory()() as session:
            approvals = await TrackerOperationRepository(session).list_approvals()
        for approval in approvals:
            action = _parse_action(approval)
            if action is not None and "operationId" not in action:
                await self._import_legacy(approval, action)
        async with self._factory()() as session:
            operations = await TrackerOperationRepository(session).list_states()
        for operation in operations:
            if operation.state == "dispatching":
                await self._interrupted_dispatch(operation.id)
            elif operation.state in _ACTIVE and operation.approval_ref is None:
                await self._provision(operation, create=False)
            if operation.approval_ref is not None:
                await self._reject_extras(operation)

    async def _import_legacy(self, approval: Approval, action: dict[str, Any]) -> None:
        source_key = f"legacy:{approval.id}"
        async with serialized_write(self._factory()) as session:
            repo = TrackerOperationRepository(session)
            if await repo.get_by_source_key(source_key) is not None:
                return
            try:
                operation = await self._new_operation(
                    session,
                    approval.job_id,
                    TrackerWriteRequest(
                        action["trackerLinkId"],
                        action["ticketRef"],
                        TrackerWriteAction(action["action"]),
                        action["value"],
                    ),
                    source_key=source_key,
                    task_link_id=None,
                )
            except (TrackerResolutionError, TrackerAdapterError) as exc:
                # Legacy JSON did not snapshot provider identity. Never invent or rebind missing routing.
                log.warning(
                    "tracker_legacy_import_missing_identity", approval_id=approval.id, error_type=type(exc).__name__
                )
                return
            operation.approval_id = approval.id
            operation.approval_ref = approval.id
            operation.approval_snapshot = _snapshot(approval)
            operation.created_at = approval.requested_at
            if approval.resolution == ApprovalResolution.approved:
                operation.state = TrackerWriteState.uncertain
                operation.error_code = "legacy_delivery_unknown"
                operation.error_detail = "Legacy approved request has no durable dispatch history"
                operation.completed_at = datetime.now(UTC)
            elif approval.resolution == ApprovalResolution.rejected:
                operation.state = TrackerWriteState.rejected
                operation.completed_at = approval.resolved_at or datetime.now(UTC)
            created = await repo.create(operation)
        await self._publish(created)
