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


async def _operation_at(service, operation_id, predicate):
    for _ in range(200):
        operation = await service.get_operation(operation_id)
        if predicate(operation):
            return operation
        await asyncio.sleep(0.01)
    pytest.fail(f"Operation did not reach expected condition: {operation.state}, {operation.error_detail}")


async def test_approval_commit_binding_gap_reuses_original_request(tracker_db, monkeypatch):
    factory, engine, _ = tracker_db
    dispatch = AsyncMock(return_value={"id": "receipt"})
    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", dispatch)

    class InterruptedApproval(ApprovalService):
        def __init__(self):
            super().__init__(factory)
            self.persisted = asyncio.Event()

        async def create_request(self, *args, **kwargs):
            approval = await super().create_request(*args, **kwargs)
            self.persisted.set()
            raise asyncio.CancelledError(f"Crash after approval {approval.id} committed")

    approvals = InterruptedApproval()
    original = TrackerWriteService(approvals, session_factory=factory)
    caller = asyncio.create_task(original.execute("job", _request(), source_key="gap"))
    try:
        await asyncio.wait_for(approvals.persisted.wait(), 3)
    finally:
        await original.close()
        await _cancel(caller)
    before = await approvals.list_for_job("job")
    assert len(before) == 1
    await engine.dispose()
    recovered_approvals = ApprovalService(factory)
    await recovered_approvals.recover_pending_approvals()
    recovered = TrackerWriteService(recovered_approvals, session_factory=factory)
    try:
        await recovered.recover_on_startup()
        operations = await recovered.list_operations("project")
        assert len(operations) == 1 and operations[0].approval_ref == before[0].id
        recovered.wake()
        await recovered_approvals.resolve(before[0].id, ApprovalResolution.approved)
        applied = await _operation_at(recovered, operations[0].id, lambda item: item.state == "applied")
        assert applied.provider_receipt.id == "receipt"
        assert len(await recovered_approvals.list_for_job("job")) == 1
        dispatch.assert_awaited_once()
    finally:
        await recovered.close()


async def test_actual_process_loss_after_submission_is_uncertain_not_replayed(tracker_db, monkeypatch, tmp_path):
    import subprocess

    factory, engine, _ = tracker_db
    code = """
import asyncio, os, sys
from pathlib import Path
from backend.persistence.database import create_engine, create_session_factory
from backend.services.job.approval_service import ApprovalService
from backend.services.tracker_write_service import TrackerWriteService, TrackerWriteRequest, TrackerWriteAction
import backend.services.tracker_resolution as resolution
async def accepted(factory, request):
    from backend.persistence.tracker_operation_repo import TrackerOperationRepository
    async with factory() as session:
        op = await TrackerOperationRepository(session).get(request.operation_id)
        assert op.state == 'dispatching'
    Path(sys.argv[2]).write_text('accepted', encoding='utf-8')
    os._exit(24)
async def main():
    sf=create_session_factory(create_engine(Path(sys.argv[1])))
    approvals=ApprovalService(sf)
    resolution.dispatch_tracker_write=accepted
    service=TrackerWriteService(approvals, session_factory=sf)
    caller=asyncio.create_task(service.execute('job', TrackerWriteRequest(
        'link', 'owner/repo#7', TrackerWriteAction.comment, 'Task completed.'), source_key='process-loss'))
    for _ in range(500):
        pending=await approvals.list_pending('job')
        if pending:
            await approvals.resolve(pending[0].id, 'approved')
            break
        await asyncio.sleep(.01)
    await caller
asyncio.run(main())
"""
    marker = tmp_path / "provider-accepted"
    result = await asyncio.to_thread(
        subprocess.run,
        ["uv", "run", "--no-sync", "python", "-c", code, str(tmp_path / "tracker.db"), str(marker)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 24, result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8") == "accepted"
    await engine.dispose()
    dispatch = AsyncMock()
    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", dispatch)
    service = TrackerWriteService(ApprovalService(factory), session_factory=factory)
    try:
        before = (await service.list_operations("project"))[0]
        assert before.state == "dispatching"
        await service.recover_on_startup()
        service.wake()
        await asyncio.sleep(0.1)
        after = await service.get_operation(before.id)
        assert after.state == "uncertain"
        assert after.error_code == "dispatch_interrupted"
        dispatch.assert_not_awaited()
    finally:
        await service.close()


async def test_uncertainty_requires_evidence_then_fresh_approval_for_retry(tracker_db, monkeypatch):
    from backend.models.domain import StateConflictError

    factory, _, _ = tracker_db
    approvals = ApprovalService(factory)
    writes = []
    calls = 0

    async def provider(_factory, request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("No acknowledgement")
        writes.append(request.value)
        return {"id": "confirmed-comment", "http_status": 201}

    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", provider)
    service = TrackerWriteService(approvals, session_factory=factory)
    try:
        original = await service.enqueue("job", _request(), source_key="retry-original")
        pending = await _operation_at(service, original.id, lambda item: item.approval_id is not None)
        await approvals.resolve(pending.approval_id, ApprovalResolution.approved)
        uncertain = await _operation_at(service, original.id, lambda item: item.state == "uncertain")
        with pytest.raises(StateConflictError):
            await service.retry_operation(
                "project",
                original.id,
                expected_version=uncertain.version,
                request_id="unsafe",
            )
        assert calls == 1 and writes == []
        with pytest.raises(ValueError):
            await service.reconcile_operation(
                "project",
                original.id,
                expected_version=uncertain.version,
                resolution="not_applied",
                evidence=" ",
                actor="operator",
            )
        resolved = await service.reconcile_operation(
            "project",
            original.id,
            expected_version=uncertain.version,
            resolution="not_applied",
            evidence="Controlled provider audit log contains no original write.",
            actor="operator",
        )
        with pytest.raises(StateConflictError):
            await service.reconcile_operation(
                "project",
                original.id,
                expected_version=uncertain.version,
                resolution="applied",
                evidence="Stale decision",
                actor="operator",
            )
        retry = await service.retry_operation(
            "project",
            original.id,
            expected_version=resolved.version,
            request_id="confirmed-retry",
        )
        repeated = await service.retry_operation(
            "project",
            original.id,
            expected_version=resolved.version,
            request_id="confirmed-retry",
        )
        assert repeated.id == retry.id and retry.id != original.id and retry.retry_of_id == original.id
        pending_retry = await _operation_at(service, retry.id, lambda item: item.approval_id is not None)
        assert pending_retry.approval_id != pending.approval_id
        assert calls == 1
        await approvals.resolve(pending_retry.approval_id, ApprovalResolution.approved)
        applied = await _operation_at(service, retry.id, lambda item: item.state == "applied")
        assert applied.provider_receipt.id == "confirmed-comment"
        assert writes == ["Task completed."]
        history = await service.get_operation(original.id)
        assert history.state == "not_applied"
        assert history.reconciliation.evidence == "Controlled provider audit log contains no original write."
    finally:
        await service.close()


@pytest.mark.parametrize("dependency", ["job", "project", "tracker_link", "approval", "task_link"])
async def test_missing_live_dependency_cannot_dispatch_from_snapshot(tracker_db, monkeypatch, dependency):
    from sqlalchemy import delete, select

    from backend.lifespan import _init_event_infrastructure
    from backend.models.db import ApprovalRow, EventRow, TaskLinkRow
    from backend.persistence.task_link_repo import TaskLinkRepository
    from backend.services.events.sse_manager import SSEConnection

    factory, _, config = tracker_db
    task_id = None
    if dependency == "task_link":
        async with factory() as session:
            task = await TaskLinkRepository(session).create_manual(
                project_id="project",
                repo_path=config.repos[0],
                tracker_link_id="link",
                tracker_ticket_ref="owner/repo#7",
                prompt_override="Test",
            )
            await TaskLinkRepository(session).set_job_id(task.id, "job")
            await session.commit()
            task_id = task.id
    approvals = ApprovalService(factory)
    dispatch = AsyncMock()
    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", dispatch)
    bus, sse, dead_letter = _init_event_infrastructure(factory)
    connection = SSEConnection()
    sse.register(connection)
    service = TrackerWriteService(approvals, session_factory=factory, event_bus=bus)
    try:
        created = await service.enqueue("job", _request(), task_link_id=task_id)
        pending = await _operation_at(service, created.id, lambda item: item.approval_id is not None)
        async with database.serialized_write(factory) as session:
            if dependency in {"job", "project", "approval"}:
                await session.execute(delete(ApprovalRow).where(ApprovalRow.job_id == "job"))
            if dependency in {"job", "project"}:
                await session.execute(delete(EventRow).where(EventRow.job_id == "job"))
                await session.execute(delete(JobRow).where(JobRow.id == "job"))
            if dependency == "project":
                await session.execute(delete(ProjectRow).where(ProjectRow.id == "project"))
            if dependency == "tracker_link":
                await session.execute(delete(TrackerLinkRow).where(TrackerLinkRow.id == "link"))
            if dependency == "task_link":
                await session.execute(delete(TaskLinkRow).where(TaskLinkRow.id == task_id))
        service.wake()
        failed = await _operation_at(service, created.id, lambda item: item.state == "failed")
        assert failed.error_code == "dependency_deleted"
        assert failed.job_ref == "job" and failed.project_ref == "project"
        assert failed.approval_ref == pending.approval_id
        assert failed.approval_snapshot.id == pending.approval_id
        assert len(await service.list_operations("project", job_ref="job")) == 1
        dispatch.assert_not_awaited()
        if dependency in {"job", "project"}:
            for _ in range(50):
                frames = list(connection.queue._queue)
                if any('"session_id":""' in frame and '"failed"' in frame for frame in frames):
                    break
                await asyncio.sleep(0.01)
            assert any('"session_id":""' in frame and '"failed"' in frame for frame in frames)
            async with factory() as session:
                assert (await session.execute(select(EventRow).where(EventRow.job_id == "job"))).all() == []
    finally:
        await service.close()
        dead_letter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dead_letter
        sse.close_all()


@pytest.mark.parametrize(
    "decision, expected", [(None, "awaiting_approval"), ("approved", "uncertain"), ("rejected", "rejected")]
)
async def test_legacy_approval_import_preserves_delivery_uncertainty(tracker_db, monkeypatch, decision, expected):
    factory, _, _ = tracker_db
    approvals = ApprovalService(factory)
    legacy = await approvals.create_request(
        "job",
        "Legacy comment",
        proposed_action=_request().proposed_action(),
        requires_explicit_approval=True,
    )
    if decision is not None:
        await approvals.resolve(legacy.id, ApprovalResolution(decision))
    dispatch = AsyncMock()
    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", dispatch)
    service = TrackerWriteService(approvals, session_factory=factory)
    try:
        await service.recover_on_startup()
        await service.recover_on_startup()
        operations = await service.list_operations("project")
        assert len(operations) == 1
        assert operations[0].state == expected
        assert operations[0].approval_ref == legacy.id
        service.wake()
        await asyncio.sleep(0.1)
        dispatch.assert_not_awaited()
        if decision is None:
            await approvals.resolve(legacy.id, ApprovalResolution.rejected)
            rejected = await _operation_at(service, operations[0].id, lambda item: item.state == "rejected")
            assert rejected.approval_snapshot.resolution == "rejected"
    finally:
        await service.close()


async def test_noncanonical_approved_request_cannot_override_canonical_denial(tracker_db, monkeypatch):
    factory, _, _ = tracker_db
    approvals = ApprovalService(factory)
    dispatch = AsyncMock()
    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", dispatch)
    service = TrackerWriteService(approvals, session_factory=factory)
    try:
        operation = await service.enqueue("job", _request(), source_key="canonical")
        bound = await _operation_at(service, operation.id, lambda item: item.approval_id is not None)
        canonical = await approvals.get(bound.approval_id)
        extra = await approvals.create_request(
            "job",
            canonical.description,
            proposed_action=canonical.proposed_action,
            requires_explicit_approval=True,
        )
        await approvals.resolve(extra.id, ApprovalResolution.approved)
        await approvals.resolve(canonical.id, ApprovalResolution.rejected)
        denied = await _operation_at(service, operation.id, lambda item: item.state == "rejected")
        assert denied.approval_ref == canonical.id
        dispatch.assert_not_awaited()
    finally:
        await service.close()


async def test_0075_upgrade_preserves_existing_jobs_and_approvals(tmp_path):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    migration_path = Path("alembic") / "versions" / "0075_tracker_operations.py"
    module_spec = importlib.util.spec_from_file_location("tracker_operations_migration", migration_path)
    assert module_spec is not None and module_spec.loader is not None
    migration = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(migration)
    assert migration.revision == "0075" and migration.down_revision == "0074"
    engine = database.create_engine(tmp_path / "migration.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: Base.metadata.create_all(
                    sync,
                    tables=[table for table in Base.metadata.sorted_tables if table.name != "tracker_operations"],
                )
            )
        factory = database.create_session_factory(engine)
        now = datetime.now(UTC)
        async with factory() as session:
            session.add(
                ProjectRow(
                    id="legacy-project",
                    name="Legacy",
                    repo_paths="[]",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                JobRow(
                    id="legacy-job",
                    project_id="legacy-project",
                    repo="/legacy",
                    prompt="Preserve",
                    state="completed",
                    resolution="merged",
                    base_ref="main",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        approval = await ApprovalService(factory).create_request(
            "legacy-job",
            "Preserve explicit decision",
            proposed_action="legacy-action",
            requires_explicit_approval=True,
        )

        def upgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.upgrade()
            inspector = inspect(sync)
            columns = {column["name"]: column for column in inspector.get_columns("tracker_operations")}
            for name in ("job_id", "project_id", "task_link_id", "tracker_link_id", "approval_id"):
                assert columns[name]["nullable"]
            fks = {
                tuple(fk["constrained_columns"]): fk["options"].get("ondelete")
                for fk in inspector.get_foreign_keys("tracker_operations")
            }
            for name in ("job_id", "project_id", "task_link_id", "tracker_link_id", "approval_id"):
                assert fks[(name,)] == "SET NULL"
            assert columns["job_ref"]["nullable"] is False
            assert columns["project_ref"]["nullable"] is False

        async with engine.begin() as conn:
            await conn.run_sync(upgrade)
        async with factory() as session:
            job = await session.get(JobRow, "legacy-job")
            assert job is not None and job.prompt == "Preserve" and job.resolution == "merged"
            assert (await session.execute(text("PRAGMA foreign_key_check"))).all() == []
        preserved = await ApprovalService(factory).get(approval.id)
        assert preserved is not None and preserved.requires_explicit_approval
        assert preserved.proposed_action == "legacy-action"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("pause_at", ["before_commit", "after_commit"])
async def test_competing_workers_fence_stale_approval_creator(tracker_db, monkeypatch, pause_at):
    from datetime import timedelta

    from backend.persistence.project_repo import ProjectRepository
    from backend.persistence.tracker_operation_repo import TrackerOperationRepository

    factory, _, _ = tracker_db

    class PausingApprovals(ApprovalService):
        def __init__(self):
            super().__init__(factory)
            self.create_count = 0
            self.created_requests = {}
            self.first_worker = None
            self.first_paused = asyncio.Event()
            self.release_first = asyncio.Event()
            self.first_returned = asyncio.Event()
            self.stale_worker_resumed = asyncio.Event()
            self.resolutions = []
            self.worker_tasks = set()
            self.canonical_id = None
            self.approved_readers = set()
            self.both_read_approval = asyncio.Event()
            self.release_decisions = asyncio.Event()

        async def create_request(self, *args, **kwargs):
            index = self.create_count
            self.create_count += 1
            if index == 0:
                self.first_worker = asyncio.current_task()
                if pause_at == "before_commit":
                    # Pause before the producer opens its own short writer transaction.
                    self.first_paused.set()
                    await self.release_first.wait()
            approval = await super().create_request(*args, **kwargs)
            self.created_requests[index] = approval
            if index == 0:
                if pause_at == "after_commit":
                    self.first_paused.set()
                    await self.release_first.wait()
                self.first_returned.set()
            return approval

        async def get(self, approval_id):
            approval = await super().get(approval_id)
            task = asyncio.current_task()
            # resolve() may read an extra request before rejecting it. Only a
            # canonical read marks the worker's next iteration after retirement.
            if (
                self.first_returned.is_set()
                and task is self.first_worker
                and approval_id == self.canonical_id
            ):
                self.stale_worker_resumed.set()
            if (
                approval is not None
                and approval.id == self.canonical_id
                and approval.resolution == ApprovalResolution.approved
                and task in self.worker_tasks
            ):
                self.approved_readers.add(task)
                if self.approved_readers == self.worker_tasks:
                    self.both_read_approval.set()
                await self.release_decisions.wait()
            return approval

        async def resolve(self, approval_id, resolution, notes=None):
            approval = await super().resolve(approval_id, resolution, notes=notes)
            self.resolutions.append((approval_id, approval.resolution, notes))
            return approval

    approvals = PausingApprovals()
    first = TrackerWriteService(approvals, session_factory=factory)
    second = TrackerWriteService(approvals, session_factory=factory)
    deliveries = []

    async def provider(_factory, request):
        async with factory() as session:
            operation = await TrackerOperationRepository(session).get(request.operation_id)
        assert operation is not None and operation.state == "dispatching"
        assert operation.approval_ref == approvals.canonical_id
        decision = await approvals.get(operation.approval_ref)
        assert decision.resolution == ApprovalResolution.approved
        deliveries.append((operation.id, operation.approval_ref, request.value))
        return {"id": "single-provider-comment", "http_status": 201}

    monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", provider)

    async def expire_claim_and_prove_writer_progress(operation_id):
        async with database.serialized_write(factory) as session:
            repo = TrackerOperationRepository(session)
            claimed = await repo.get(operation_id)
            assert claimed is not None and claimed.claim_token is not None
            assert claimed.approval_ref is None
            await ProjectRepository(session).create("writer-proof", "Unrelated writer", ["/unrelated-writer"])
            now = datetime.now(UTC)
            expired = await repo.compare_and_set(
                claimed, now=now, changes={"claim_expires_at": now - timedelta(seconds=1)},
            )
            assert expired is not None and expired.claim_token == claimed.claim_token
        return expired

    try:
        original = await first.enqueue("job", _request(), source_key=f"competing-workers:{pause_at}")
        await asyncio.wait_for(approvals.first_paused.wait(), 5)
        try:
            expired = await asyncio.wait_for(expire_claim_and_prove_writer_progress(original.id), 5)
        except TimeoutError:
            pytest.fail("Approval provisioning retained a writer lock or uncommitted write across create_request")
        assert not approvals.release_first.is_set()
        async with factory() as session:
            assert await ProjectRepository(session).get("writer-proof") is not None

        duplicate = await second.enqueue("job", _request(), source_key=f"competing-workers:{pause_at}")
        assert duplicate.id == original.id
        bound = await _operation_at(second, original.id, lambda item: item.approval_ref is not None)
        assert not approvals.release_first.is_set()
        assert bound.version == expired.version + 2
        assert bound.claim_token is None
        assert bound.state == "awaiting_approval"
        canonical_creator = 1 if pause_at == "before_commit" else 0
        canonical = approvals.created_requests[canonical_creator]
        assert bound.approval_ref == canonical.id
        assert approvals.create_count == (2 if pause_at == "before_commit" else 1)
        assert json.loads(canonical.proposed_action)["operationId"] == original.id
        assert canonical.requires_explicit_approval
        approvals.canonical_id = canonical.id

        approvals.release_first.set()
        await asyncio.wait_for(approvals.first_returned.wait(), 5)
        await asyncio.wait_for(approvals.stale_worker_resumed.wait(), 5)
        rows = await approvals.list_for_job("job")
        extras = [row for row in rows if row.id != canonical.id]
        assert len(rows) == (2 if pause_at == "before_commit" else 1)
        assert all(row.resolution == ApprovalResolution.rejected for row in extras)
        assert {
            approval_id for approval_id, decision, _ in approvals.resolutions
            if decision == ApprovalResolution.rejected
        } == {row.id for row in extras}
        assert all(f"canonical tracker approval {canonical.id}" in row.notes for row in extras)
        after_stale_creator = await first.get_operation(original.id)
        assert after_stale_creator.approval_ref == canonical.id
        assert after_stale_creator.version == bound.version
        assert after_stale_creator.state == "awaiting_approval"
        assert deliveries == []
        assert (await approvals.get(canonical.id)).resolution is None

        # Both genuine worker loops read the durable decision before either can attempt the state CAS.
        approvals.worker_tasks = {first._worker, second._worker}
        assert None not in approvals.worker_tasks and len(approvals.worker_tasks) == 2
        await approvals.resolve(canonical.id, ApprovalResolution.approved)
        first.wake()
        second.wake()
        await asyncio.wait_for(approvals.both_read_approval.wait(), 5)
        assert (await first.get_operation(original.id)).state == "awaiting_approval"
        assert deliveries == []
        approvals.release_decisions.set()
        applied = await _operation_at(first, original.id, lambda item: item.state == "applied")
        assert deliveries == [(original.id, canonical.id, "Task completed.")]
        assert applied.provider_receipt.id == "single-provider-comment"
        assert applied.approval_snapshot.id == canonical.id
        assert applied.approval_snapshot.resolution == "approved"
        history = await second.list_operations("project")
        assert [operation.id for operation in history] == [original.id]
        assert len(await approvals.list_for_job("job")) == len(rows)
    finally:
        approvals.release_first.set()
        approvals.release_decisions.set()
        closed = await asyncio.gather(first.close(), second.close(), return_exceptions=True)
        for result in closed:
            if isinstance(result, BaseException):
                raise result
