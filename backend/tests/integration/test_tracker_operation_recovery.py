"""Durable outbound behavior with real SQLite and controlled provider I/O."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import inspect

import backend
from backend.config import CPLConfig
from backend.models.db import Base, CredentialRow, JobRow, ProjectRow, TrackerLinkRow
from backend.models.domain import ApprovalResolution
from backend.persistence import database
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.events.event_bus import EventBus
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime import RuntimeService
from backend.services.tracker_write_service import TrackerWriteAction, TrackerWriteRequest, TrackerWriteService


@pytest.fixture
async def tracker_db(tmp_path, monkeypatch):
    assert Path(backend.__file__).resolve().parents[1] == Path.cwd().resolve()
    monkeypatch.setattr(database, "_write_lock", None)
    engine = database.create_engine(tmp_path / "tracker.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = database.create_session_factory(engine)
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(
            ProjectRow(
                id="project",
                name="Tracker recovery",
                repo_paths=json.dumps([str(tmp_path)]),
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            CredentialRow(
                id="credential",
                provider="github",
                label="Test",
                base_url="https://api.github.com",
                encrypted_secret="not-used-by-controlled-dispatch",
                created_at=now.isoformat(),
            )
        )
        await session.flush()
        session.add(
            JobRow(
                id="job",
                project_id="project",
                repo=str(tmp_path),
                prompt="Test",
                state="completed",
                resolution="merged",
                base_ref="main",
                created_at=now,
                updated_at=now,
                session_count=1,
                artifact_collection_status="completed",
                artifact_collection_session_count=1,
            )
        )
        session.add(
            TrackerLinkRow(
                id="link",
                project_id="project",
                credential_id="credential",
                external_ref="owner/1",
                created_at=now.isoformat(),
            )
        )
        await session.commit()
    yield factory, engine, CPLConfig(repos=[str(tmp_path)])
    await engine.dispose()


class _Approvals(ApprovalService):
    def __init__(self, factory):
        super().__init__(factory)
        self.created = asyncio.Event()

    async def create_request(self, *args, **kwargs):
        approval = await super().create_request(*args, **kwargs)
        self.created.set()
        return approval


def _request():
    return TrackerWriteRequest("link", "owner/repo#7", TrackerWriteAction.comment, "Task completed.")


async def _close(service):
    close = getattr(service, "close", None)
    if close is not None:
        await close()


async def _cancel(task):
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_pending_tracker_dispatch_survives_restart(tracker_db, monkeypatch):
    factory, engine, config = tracker_db
    approvals = _Approvals(factory)
    dispatch = AsyncMock()
    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", dispatch)
    original = TrackerWriteService(approvals, session_factory=factory)
    caller = asyncio.create_task(original.execute("job", _request()))
    try:
        await asyncio.wait_for(approvals.created.wait(), 3)
        approval = (await approvals.list_pending("job"))[0]
    finally:
        await _close(original)
        await _cancel(caller)
    await engine.dispose()
    recovered_approvals = ApprovalService(factory)
    runtime = RuntimeService(
        factory,
        EventBus(),
        AdapterRegistry(),
        config,
        approval_service=recovered_approvals,
    )
    await runtime.recover_on_startup()
    recovered = TrackerWriteService(recovered_approvals, session_factory=factory)
    recovery = getattr(recovered, "recover_on_startup", None)
    if recovery is not None:
        await recovery()
        recovered.wake()
    try:
        await recovered_approvals.resolve(approval.id, ApprovalResolution.approved)
        for _ in range(60):
            if dispatch.await_count:
                break
            await asyncio.sleep(0.025)
        assert dispatch.await_count == 1, "Recovered approval has no provider dispatcher"
    finally:
        await _close(recovered)


async def test_provider_outcome_is_persisted_not_only_returned(tracker_db, monkeypatch):
    factory, engine, _ = tracker_db
    approvals = _Approvals(factory)
    dispatch = AsyncMock(side_effect=RuntimeError("provider refused"))
    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", dispatch)
    service = TrackerWriteService(approvals, session_factory=factory)
    caller = asyncio.create_task(service.execute("job", _request()))
    try:
        await asyncio.wait_for(approvals.created.wait(), 3)
        approval = (await approvals.list_pending("job"))[0]
        await approvals.resolve(approval.id, ApprovalResolution.approved)
        outcome = await asyncio.wait_for(caller, 4)
        assert outcome.applied is False
    finally:
        await _close(service)
        await _cancel(caller)
    await engine.dispose()
    async with engine.connect() as conn:

        def find_evidence(sync):
            found = []
            for table in inspect(sync).get_table_names():
                for row in sync.exec_driver_sql(f'SELECT * FROM "{table}"').mappings():
                    if "provider refused" in json.dumps(dict(row), default=str):
                        found.append(table)
            return found

        persisted = await conn.run_sync(find_evidence)
    assert persisted, "Provider outcome was returned but not persisted"


async def test_response_loss_is_not_reported_as_definite_provider_failure(tracker_db, monkeypatch):
    factory, _, _ = tracker_db
    approvals = _Approvals(factory)
    delivered = []

    async def accepted_then_lost(_factory, request):
        delivered.append(request.value)
        raise httpx.ReadTimeout("response lost after acceptance")

    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", accepted_then_lost)
    service = TrackerWriteService(approvals, session_factory=factory)
    caller = asyncio.create_task(service.execute("job", _request()))
    try:
        await asyncio.wait_for(approvals.created.wait(), 3)
        approval = (await approvals.list_pending("job"))[0]
        await approvals.resolve(approval.id, ApprovalResolution.approved)
        outcome = await asyncio.wait_for(caller, 4)
        assert delivered == ["Task completed."]
        assert outcome.state.value == "uncertain", f"Accepted comment with lost response reported {outcome.state}"
    finally:
        await _close(service)
        await _cancel(caller)
