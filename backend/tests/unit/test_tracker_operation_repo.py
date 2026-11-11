"""Outbox identities, fencing, and evidence retention against production SQLite."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, text, update

from backend.models.db import ApprovalRow, Base, CredentialRow, JobRow, ProjectRow, TaskLinkRow, TrackerLinkRow
from backend.models.domain import StateConflictError
from backend.models.schemas.tracker_operations import (
    TrackerApprovalSnapshot,
    TrackerOperationResponse,
    TrackerOperationState,
    TrackerProviderReceipt,
    TrackerReconciliation,
)
from backend.persistence.database import create_engine, create_session_factory
from backend.persistence.tracker_operation_repo import TrackerOperationRepository

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
_ACTION = json.dumps(
    {
        "schemaVersion": 1,
        "operationId": "operation-1",
        "action": "comment",
        "trackerLinkId": "link-1",
        "ticketRef": "acme/shop#42",
        "value": "  Reviewed.\n",
    },
    sort_keys=True,
)


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_engine(tmp_path / "tracker-operations.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert await connection.scalar(text("PRAGMA journal_mode")) == "wal"
    factory = create_session_factory(engine)
    async with factory() as session:
        session.add(
            ProjectRow(
                id="project-1",
                name="Outbox",
                repo_paths='["/repo"]',
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        session.add(
            CredentialRow(
                id="credential-1",
                provider="github",
                label="Controlled dispatch",
                base_url="https://api.github.com",
                encrypted_secret="unused",
                created_at=_NOW.isoformat(),
            )
        )
        await session.flush()
        session.add(
            JobRow(
                id="job-1",
                project_id="project-1",
                repo="/repo",
                prompt="Review",
                base_ref="main",
                state="completed",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        session.add(
            TrackerLinkRow(
                id="link-1",
                project_id="project-1",
                credential_id="credential-1",
                external_ref="acme/7",
                created_at=_NOW.isoformat(),
            )
        )
        await session.flush()
        session.add(
            TaskLinkRow(
                id="task-1",
                project_id="project-1",
                repo_path="/repo",
                job_id="job-1",
                tracker_link_id="link-1",
                tracker_ticket_ref="acme/shop#42",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        session.add(
            ApprovalRow(
                id="approval-1",
                job_id="job-1",
                description="Post reviewed result",
                proposed_action=_ACTION,
                requested_at=_NOW,
                resolved_at=_NOW + timedelta(seconds=1),
                resolution="approved",
                requires_explicit_approval=True,
                notes="Verified by operator",
            )
        )
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


def _intent(**updates: object) -> TrackerOperationResponse:
    values = {
        "id": "operation-1",
        "source_key": "task:task-1:job-1:1:tracker_write",
        "project_id": "project-1",
        "project_ref": "project-1",
        "job_id": "job-1",
        "job_ref": "job-1",
        "task_link_id": "task-1",
        "task_link_ref": "task-1",
        "tracker_link_id": "link-1",
        "tracker_link_ref": "link-1",
        "provider": "github",
        "base_url": "https://api.github.com",
        "external_ref": "acme/7",
        "credential_id_ref": "credential-1",
        "ticket_ref": "acme/shop#42",
        "action": "comment",
        "target_kind": "comment",
        "value": "  Reviewed.\n",
        "normalized_value": None,
        "state": TrackerOperationState.awaiting_approval,
        "version": 0,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    return TrackerOperationResponse.model_validate({**values, **updates})


def _snapshot() -> TrackerApprovalSnapshot:
    return TrackerApprovalSnapshot(
        id="approval-1",
        job_id="job-1",
        description="Post reviewed result",
        proposed_action=_ACTION,
        requested_at=_NOW,
        resolved_at=_NOW + timedelta(seconds=1),
        resolution="approved",
        requires_explicit_approval=True,
        notes="Verified by operator",
    )


async def test_source_key_replay_returns_original_intent_after_reopening(session_factory) -> None:
    async with session_factory() as session:
        original = await TrackerOperationRepository(session).create(_intent())
        await session.commit()
    async with session_factory() as session:
        duplicate = await TrackerOperationRepository(session).create(_intent(id="other-generated-id"))
        await session.commit()
    async with session_factory() as session:
        rows = await TrackerOperationRepository(session).list_operations("project-1")
    assert duplicate.id == original.id
    assert [row.id for row in rows] == [original.id]
    assert rows[0].value == "  Reviewed.\n"


@pytest.mark.parametrize(
    "changes",
    [
        {"value": "A different comment"},
        {"ticket_ref": "acme/other#7"},
        {"credential_id_ref": "other-credential"},
    ],
)
async def test_source_key_cannot_replace_payload_or_route(session_factory, changes) -> None:
    async with session_factory() as session:
        await TrackerOperationRepository(session).create(_intent())
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(StateConflictError, match="different request"):
            await TrackerOperationRepository(session).create(_intent(id="other-operation", **changes))
        await session.rollback()
    async with session_factory() as session:
        rows = await TrackerOperationRepository(session).list_operations("project-1")
    assert len(rows) == 1
    assert rows[0].value == "  Reviewed.\n"
    assert rows[0].ticket_ref == "acme/shop#42"
    assert rows[0].credential_id_ref == "credential-1"


async def test_dispatch_cas_rejects_wrong_version_and_second_attempt(session_factory) -> None:
    async with session_factory() as session:
        repo = TrackerOperationRepository(session)
        operation = await repo.create(
            _intent(
                state="approved",
                approval_id="approval-1",
                approval_ref="approval-1",
                approval_snapshot=_snapshot(),
            )
        )
        touched = await repo.compare_and_set(operation, now=_NOW, changes={})
        assert touched is not None
        await session.commit()
    async with session_factory() as session:
        repo = TrackerOperationRepository(session)
        assert (
            await repo.compare_and_set(
                operation,
                now=_NOW,
                changes={"state": "dispatching", "dispatch_started_at": _NOW},
            )
            is None
        )
        dispatched = await repo.compare_and_set(
            touched,
            now=_NOW,
            changes={"state": "dispatching", "dispatch_started_at": _NOW},
        )
        assert dispatched is not None
        assert await repo.compare_and_set(touched, now=_NOW, changes={"state": "dispatching"}) is None
        assert (
            await repo.claim(
                operation.id,
                expected_version=dispatched.version,
                token="late-claim",
                now=_NOW + timedelta(days=1),
                expires_at=_NOW + timedelta(days=2),
            )
            is None
        )
        with pytest.raises(StateConflictError, match="Invalid tracker operation transition"):
            await repo.compare_and_set(dispatched, now=_NOW, changes={"state": "approved"})
        await session.commit()
    async with session_factory() as session:
        persisted = await TrackerOperationRepository(session).get(operation.id)
    assert persisted is not None
    assert persisted.state == "dispatching"
    assert persisted.version == touched.version + 1
    assert persisted.dispatch_started_at == _NOW


async def test_expired_claim_cannot_bind_or_release_successor_claim(session_factory) -> None:
    async with session_factory() as session:
        repo = TrackerOperationRepository(session)
        original = await repo.create(_intent())
        first = await repo.claim(
            original.id,
            expected_version=original.version,
            token="first-worker",
            now=_NOW,
            expires_at=_NOW + timedelta(seconds=1),
        )
        assert first is not None
        assert (
            await repo.claim(
                original.id,
                expected_version=first.version,
                token="early-competitor",
                now=_NOW,
                expires_at=_NOW + timedelta(seconds=10),
            )
            is None
        )
        await session.commit()
    async with session_factory() as session:
        repo = TrackerOperationRepository(session)
        second = await repo.claim(
            original.id,
            expected_version=first.version,
            token="second-worker",
            now=_NOW + timedelta(seconds=2),
            expires_at=_NOW + timedelta(seconds=10),
        )
        assert second is not None
        binding = {
            "approval_id": "approval-1",
            "approval_ref": "approval-1",
            "approval_snapshot": _snapshot(),
            "claim_token": None,
            "claim_expires_at": None,
        }
        # Use the current version to isolate token fencing from ordinary version CAS.
        assert (
            await repo.compare_and_set(
                second,
                now=_NOW + timedelta(seconds=3),
                claim_token="first-worker",
                changes=binding,
            )
            is None
        )
        assert (
            await repo.compare_and_set(
                second,
                now=_NOW + timedelta(seconds=10),
                claim_token="second-worker",
                changes=binding,
            )
            is None
        )
        await repo.release_claim(original.id, "first-worker", _NOW + timedelta(seconds=3))
        current = await repo.get(original.id)
        assert current is not None
        assert current.claim_token == "second-worker"
        assert current.version == second.version
        bound = await repo.compare_and_set(
            current,
            now=_NOW + timedelta(seconds=3),
            claim_token="second-worker",
            changes=binding,
        )
        assert bound is not None
        with pytest.raises(StateConflictError, match="cannot be replaced"):
            await repo.compare_and_set(bound, now=_NOW, changes={"approval_ref": "another-approval"})
        await session.commit()
    async with session_factory() as session:
        persisted = await TrackerOperationRepository(session).get(original.id)
    assert persisted is not None
    assert persisted.approval_ref == "approval-1"
    assert persisted.approval_snapshot == _snapshot()
    assert persisted.claim_token is None


@pytest.mark.parametrize(
    "parent, field",
    [
        (ApprovalRow, "approval_id"),
        (TaskLinkRow, "task_link_id"),
        (TrackerLinkRow, "tracker_link_id"),
        (JobRow, "job_id"),
        (ProjectRow, "project_id"),
    ],
)
async def test_parent_deletion_nulls_live_reference_but_retains_evidence(session_factory, parent, field) -> None:
    receipt = TrackerProviderReceipt(id="comment-123", url="https://github.com/acme/shop/issues/42", http_status=201)
    reconciliation = TrackerReconciliation(
        resolution="applied",
        evidence="Provider comment 123 verified",
        actor="operator",
        recorded_at=_NOW,
    )
    async with session_factory() as session:
        original = await TrackerOperationRepository(session).create(
            _intent(
                state="applied",
                approval_id="approval-1",
                approval_ref="approval-1",
                approval_snapshot=_snapshot(),
                provider_receipt=receipt,
                reconciliation=reconciliation,
                dispatch_started_at=_NOW,
                completed_at=_NOW + timedelta(seconds=2),
            )
        )
        await session.commit()
    async with session_factory() as session:
        # These pre-existing parent FKs are restrictive, unlike the new outbox correlations.
        if parent in {JobRow, ProjectRow}:
            await session.execute(delete(ApprovalRow).where(ApprovalRow.id == "approval-1"))
            await session.execute(update(TaskLinkRow).where(TaskLinkRow.id == "task-1").values(job_id=None))
        if parent is ProjectRow:
            await session.execute(delete(TaskLinkRow).where(TaskLinkRow.id == "task-1"))
            await session.execute(delete(JobRow).where(JobRow.id == "job-1"))
        await session.execute(delete(parent).where(parent.id == getattr(original, field)))
        await session.commit()
    async with session_factory() as session:
        repo = TrackerOperationRepository(session)
        persisted = await repo.get(original.id)
        history = await repo.list_operations("project-1", job_ref="job-1", tracker_link_ref="link-1")
    assert persisted is not None
    assert getattr(persisted, field) is None
    assert (persisted.project_ref, persisted.job_ref, persisted.task_link_ref, persisted.tracker_link_ref) == (
        "project-1",
        "job-1",
        "task-1",
        "link-1",
    )
    assert persisted.approval_ref == "approval-1"
    assert persisted.approval_snapshot == _snapshot()
    assert persisted.provider_receipt == receipt
    assert persisted.reconciliation == reconciliation
    assert persisted.value == original.value
    assert persisted.state == "applied"
    assert [row.id for row in history] == [original.id]
