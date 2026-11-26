"""Tests for approval-gated tracker writes (Story 3.4)."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from backend.models.db import Base, CredentialRow, JobRow, ProjectRow, TrackerLinkRow
from backend.models.domain import StateConflictError
from backend.persistence import database
from backend.services.job.approval_service import ApprovalService
from backend.services.tracker_adapter import TrackerAdapterError
from backend.services.tracker_write_service import (
    TrackerWriteAction,
    TrackerWriteRequest,
    TrackerWriteService,
    TrackerWriteState,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture
async def session_factory(tmp_path, monkeypatch) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    monkeypatch.setattr(database, "_write_lock", None)
    engine = database.create_engine(tmp_path / "tracker-write.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = database.create_session_factory(engine)
    async with factory() as session:
        session.add(
            ProjectRow(
                id="proj-1",
                name="Test Project",
                repo_paths='["/test"]',
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.add(
            CredentialRow(
                id="credential-1",
                provider="github",
                label="Controlled dispatch",
                base_url="https://api.github.com",
                encrypted_secret="unused",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        await session.flush()
        session.add(
            TrackerLinkRow(
                id="link-1",
                project_id="proj-1",
                credential_id="credential-1",
                external_ref="acme/7",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        session.add(
            JobRow(
                id="job-1",
                repo="/test",
                project_id="proj-1",
                prompt="test",
                state="running",
                base_ref="main",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def approval_service(session_factory: async_sessionmaker[AsyncSession]) -> ApprovalService:
    return ApprovalService(session_factory)


@pytest.fixture
async def tracker_write_service(approval_service: ApprovalService, session_factory):
    service = TrackerWriteService(approval_service, session_factory=session_factory)
    try:
        yield service
    finally:
        await service.close()


async def _pending_approval(approval_service: ApprovalService):
    async with asyncio.timeout(3):
        while True:
            pending = await approval_service.list_pending("job-1")
            if pending:
                return pending[0]
            await asyncio.sleep(0.01)


@asynccontextmanager
async def _execution(service, request, dispatch, **kwargs):
    task = asyncio.create_task(service.execute("job-1", request, dispatch, **kwargs))
    try:
        yield task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_rejected_write_uses_existing_approval_and_is_never_dispatched(
    approval_service: ApprovalService,
    tracker_write_service: TrackerWriteService,
) -> None:
    request = TrackerWriteRequest(
        tracker_link_id="link-1",
        ticket_ref="acme/shop#42",
        action=TrackerWriteAction.comment,
        value="Ready for review",
    )
    dispatch = AsyncMock(return_value=None)

    async with _execution(tracker_write_service, request, dispatch) as execution:
        approval = await _pending_approval(approval_service)

        assert approval.requires_explicit_approval is True
        assert approval.description == "Comment on tracker ticket acme/shop#42?"
        proposed = json.loads(approval.proposed_action)
        operation = await tracker_write_service.get_operation(proposed["operationId"])
        assert proposed == {
            "schemaVersion": 1,
            "operationId": operation.id,
            "action": "comment",
            "ticketRef": "acme/shop#42",
            "trackerLinkId": "link-1",
            "value": "Ready for review",
            "projectRef": "proj-1",
            "jobRef": "job-1",
            "taskLinkRef": None,
            "provider": "github",
            "baseUrl": "https://api.github.com",
            "externalRef": "acme/7",
            "credentialIdRef": "credential-1",
            "targetKind": "comment",
            "normalizedValue": None,
        }
        assert operation.state == "awaiting_approval"

        await approval_service.resolve(approval.id, "rejected")
        outcome = await asyncio.wait_for(execution, 3)

    assert outcome.state == TrackerWriteState.rejected
    persisted = await tracker_write_service.get_operation(outcome.operation_id)
    assert persisted.state == "rejected"
    assert persisted.approval_ref == approval.id
    assert persisted.approval_snapshot.resolution == "rejected"
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_approved_write_is_dispatched_once(
    approval_service: ApprovalService,
    tracker_write_service: TrackerWriteService,
) -> None:
    request = TrackerWriteRequest(
        tracker_link_id="link-1",
        ticket_ref="acme/shop#42",
        action=TrackerWriteAction.transition,
        value="Done",
    )
    dispatch = AsyncMock(return_value={"http_status": 200, "id": "42"})

    async with _execution(tracker_write_service, request, dispatch) as execution:
        approval = await _pending_approval(approval_service)
        await approval_service.resolve(approval.id, "approved")
        outcome = await asyncio.wait_for(execution, 3)

    assert outcome.state == TrackerWriteState.applied
    assert outcome.target_kind == "github_issue_state"
    assert outcome.normalized_target == "closed"
    persisted = await tracker_write_service.get_operation(outcome.operation_id)
    assert persisted.state == "applied"
    assert persisted.provider_receipt.id == "42"
    assert persisted.provider_receipt.http_status == 200
    assert persisted.approval_snapshot.resolution == "approved"
    assert persisted.dispatch_started_at is not None
    assert persisted.completed_at is not None
    dispatch.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_blanket_job_trust_cannot_auto_approve_tracker_write(
    approval_service: ApprovalService,
    tracker_write_service: TrackerWriteService,
) -> None:
    request = TrackerWriteRequest(
        tracker_link_id="link-1",
        ticket_ref="acme/shop#42",
        action=TrackerWriteAction.comment,
        value="Ready for review",
    )
    dispatch = AsyncMock(return_value=None)

    async with _execution(tracker_write_service, request, dispatch) as execution:
        approval = await _pending_approval(approval_service)

        assert await approval_service.trust_job("job-1") == 0
        assert not execution.done()
        assert (await approval_service.get(approval.id)).resolution is None

        await approval_service.resolve(approval.id, "rejected")
        assert (await asyncio.wait_for(execution, 3)).state == TrackerWriteState.rejected
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_is_reported_as_failed(
    approval_service: ApprovalService,
    tracker_write_service: TrackerWriteService,
) -> None:
    request = TrackerWriteRequest(
        tracker_link_id="link-1",
        ticket_ref="acme/shop#42",
        action=TrackerWriteAction.comment,
        value="Ready for review",
    )
    dispatch = AsyncMock(side_effect=TrackerAdapterError("provider refused", code="provider_rejected"))

    async with _execution(tracker_write_service, request, dispatch) as execution:
        approval = await _pending_approval(approval_service)
        await approval_service.resolve(approval.id, "approved")
        outcome = await asyncio.wait_for(execution, 3)

    assert outcome.state == TrackerWriteState.failed
    assert outcome.applied is False
    assert outcome.error == "provider refused"
    persisted = await tracker_write_service.get_operation(outcome.operation_id)
    assert persisted.state == "failed"
    assert persisted.error_code == "provider_rejected"
    assert persisted.error_detail == "provider refused"
    dispatch.assert_awaited_once_with(request)


async def test_untyped_postdispatch_failure_is_persisted_as_uncertain(
    approval_service: ApprovalService,
    tracker_write_service: TrackerWriteService,
) -> None:
    request = TrackerWriteRequest("link-1", "acme/shop#42", TrackerWriteAction.comment, "  Exact body.\n")
    dispatch = AsyncMock(side_effect=RuntimeError("provider outcome unknown"))
    async with _execution(tracker_write_service, request, dispatch) as execution:
        approval = await _pending_approval(approval_service)
        await approval_service.resolve(approval.id, "approved")
        outcome = await asyncio.wait_for(execution, 3)
    assert outcome.state == TrackerWriteState.uncertain
    assert not outcome.applied
    persisted = await tracker_write_service.get_operation(outcome.operation_id)
    assert persisted.state == "uncertain"
    assert persisted.error_code == "dispatch_error"
    assert persisted.value == "  Exact body.\n"
    assert persisted.provider_receipt is None
    dispatch.assert_awaited_once_with(request)


async def test_repeated_caller_identity_reuses_operation_and_approval(
    approval_service: ApprovalService,
    tracker_write_service: TrackerWriteService,
) -> None:
    request = TrackerWriteRequest("link-1", "acme/shop#42", TrackerWriteAction.comment, "Same request")
    dispatch = AsyncMock(return_value=None)
    async with _execution(
        tracker_write_service,
        request,
        dispatch,
        source_key="mcp:job-1:request-1",
    ) as execution:
        approval = await _pending_approval(approval_service)
        await approval_service.resolve(approval.id, "approved")
        original = await asyncio.wait_for(execution, 3)
    replay = await tracker_write_service.execute(
        "job-1",
        request,
        dispatch,
        source_key="mcp:job-1:request-1",
    )
    assert replay.operation_id == original.operation_id
    assert replay.applied
    assert len(await approval_service.list_for_job("job-1")) == 1
    assert len(await tracker_write_service.list_operations("proj-1", job_ref="job-1")) == 1
    with pytest.raises(StateConflictError, match="another request"):
        await tracker_write_service.execute(
            "job-1",
            TrackerWriteRequest("link-1", "acme/shop#42", TrackerWriteAction.comment, "Changed body"),
            dispatch,
            source_key="mcp:job-1:request-1",
        )
    dispatch.assert_awaited_once_with(request)


async def test_unconfigured_service_cannot_dispatch_ephemerally(approval_service: ApprovalService) -> None:
    service = TrackerWriteService(approval_service)
    request = TrackerWriteRequest("link-1", "acme/shop#42", TrackerWriteAction.comment, "No outbox")
    dispatch = AsyncMock()
    with pytest.raises(RuntimeError, match="session factory"):
        await service.execute("job-1", request, dispatch)
    dispatch.assert_not_awaited()
    assert await approval_service.list_for_job("job-1") == []
