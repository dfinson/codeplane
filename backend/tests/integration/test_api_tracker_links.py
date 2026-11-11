"""Integration tests for the TrackerLink attach/list API (Story 3.2).

Exercises:
  POST /api/projects/{project_id}/tracker-links
  GET  /api/projects/{project_id}/tracker-links
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
    from unittest.mock import AsyncMock

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture(autouse=True)
def _isolated_codeplane_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.config.get_codeplane_dir", lambda: tmp_path)


async def _seed_project(session_factory: async_sessionmaker[AsyncSession], project_id: str = "proj-1") -> None:
    from datetime import UTC, datetime

    from backend.models.db import ProjectRow

    async with session_factory() as session:
        session.add(
            ProjectRow(
                id=project_id,
                name="Test Project",
                repo_paths="[]",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _create_credential(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/settings/credentials",
        json={"provider": "github", "label": "GH", "baseUrl": "https://api.github.com", "pat": "p"},
    )
    return str(resp.json()["id"])


class TestTrackerOperations:
    @pytest.fixture
    async def engine(self, tmp_path, monkeypatch):
        from backend.models.db import Base
        from backend.persistence import database

        monkeypatch.setattr(database, "_write_lock", None)
        engine = database.create_engine(tmp_path / "operation-api.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
        await engine.dispose()

    async def _operation(self, client, app, session_factory):
        from datetime import UTC, datetime

        from backend.models.db import JobRow
        from backend.services.tracker_write_service import TrackerWriteAction, TrackerWriteRequest, TrackerWriteService

        await _seed_project(session_factory)
        credential_id = await _create_credential(client)
        link = await client.post(
            "/api/projects/proj-1/tracker-links",
            json={"credentialId": credential_id, "externalRef": "owner/1"},
        )
        assert link.status_code == 201
        now = datetime.now(UTC)
        async with session_factory() as session:
            session.add(
                JobRow(
                    id="operation-job",
                    project_id="proj-1",
                    repo="/test/repo",
                    prompt="Test",
                    state="completed",
                    resolution="merged",
                    base_ref="main",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        service = await app.state.dishka_container.get(TrackerWriteService)
        operation = await service.enqueue(
            "operation-job",
            TrackerWriteRequest(
                link.json()["id"],
                "owner/repo#7",
                TrackerWriteAction.comment,
                "Exact comment.\nNo marker.",
            ),
            source_key="api-operation",
        )
        return service, operation

    async def _wait(self, service, operation_id, predicate):
        import asyncio

        for _ in range(200):
            operation = await service.get_operation(operation_id)
            if predicate(operation):
                return operation
            await asyncio.sleep(0.01)
        pytest.fail(f"Operation did not reach expected condition: {operation.state}")

    async def test_list_and_detail_are_project_scoped(self, client, app, session_factory):
        service, operation = await self._operation(client, app, session_factory)
        response = await client.get(
            "/api/projects/proj-1/tracker-links/operations", params={"job_ref": "operation-job"}
        )
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["id"] == operation.id
        assert item["projectRef"] == "proj-1"
        assert item["jobRef"] == "operation-job"
        assert item["value"] == "Exact comment.\nNo marker."
        assert item["state"] == "awaiting_approval"
        detail = await client.get(f"/api/projects/proj-1/tracker-links/operations/{operation.id}")
        assert detail.status_code == 200 and detail.json()["id"] == operation.id
        wrong_scope = await client.get(f"/api/projects/foreign/tracker-links/operations/{operation.id}")
        assert wrong_scope.status_code == 404
        filtered = await client.get(
            "/api/projects/proj-1/tracker-links/operations",
            params={"job_ref": "unrelated"},
        )
        assert filtered.json() == {"items": []}
        assert len(await service.list_operations("proj-1")) == 1

    async def test_reconciliation_and_retry_enforce_evidence_version_and_approval(
        self,
        client,
        app,
        session_factory,
        monkeypatch,
    ):
        from unittest.mock import AsyncMock

        import httpx

        dispatch = AsyncMock(side_effect=httpx.ReadTimeout("Lost response"))
        monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", dispatch)
        service, operation = await self._operation(client, app, session_factory)
        pending = await self._wait(service, operation.id, lambda item: item.approval_id is not None)
        approved = await client.post(
            f"/api/approvals/{pending.approval_id}/resolve",
            json={"resolution": "approved"},
        )
        assert approved.status_code == 200
        uncertain = await self._wait(service, operation.id, lambda item: item.state == "uncertain")
        endpoint = f"/api/projects/proj-1/tracker-links/operations/{operation.id}"
        refused = await client.post(
            endpoint + "/retry",
            json={"expectedVersion": uncertain.version, "requestId": "unsafe"},
        )
        assert refused.status_code == 409
        blank = await client.post(
            endpoint + "/reconcile",
            json={"expectedVersion": uncertain.version, "resolution": "not_applied", "evidence": " "},
        )
        assert blank.status_code == 422
        reconciled = await client.post(
            endpoint + "/reconcile",
            json={
                "expectedVersion": uncertain.version,
                "resolution": "not_applied",
                "evidence": "Verified no write in controlled provider audit log.",
            },
        )
        assert reconciled.status_code == 200
        assert reconciled.json()["reconciliation"]["actor"] == "operator"
        stale = await client.post(
            endpoint + "/reconcile",
            json={"expectedVersion": uncertain.version, "resolution": "applied", "evidence": "Stale decision"},
        )
        assert stale.status_code == 409
        body = {"expectedVersion": reconciled.json()["version"], "requestId": "retry"}
        retry = await client.post(endpoint + "/retry", json=body)
        repeated = await client.post(endpoint + "/retry", json=body)
        assert retry.status_code == repeated.status_code == 201
        assert retry.json()["id"] == repeated.json()["id"] != operation.id
        assert retry.json()["retryOfId"] == operation.id
        new_pending = await self._wait(service, retry.json()["id"], lambda item: item.approval_id is not None)
        assert new_pending.approval_id != pending.approval_id
        assert new_pending.state == "awaiting_approval"
        dispatch.assert_awaited_once()

    async def test_deleted_parent_keeps_readable_history_and_refuses_retry(self, client, app, session_factory):
        from sqlalchemy import delete

        from backend.models.db import ApprovalRow, JobRow, ProjectRow

        service, operation = await self._operation(client, app, session_factory)
        await self._wait(service, operation.id, lambda item: item.approval_id is not None)
        async with session_factory() as session:
            await session.execute(delete(ApprovalRow).where(ApprovalRow.job_id == "operation-job"))
            await session.execute(delete(JobRow).where(JobRow.id == "operation-job"))
            await session.execute(delete(ProjectRow).where(ProjectRow.id == "proj-1"))
            await session.commit()
        service.wake()
        failed = await self._wait(service, operation.id, lambda item: item.state == "failed")
        endpoint = f"/api/projects/proj-1/tracker-links/operations/{operation.id}"
        detail = await client.get(endpoint)
        assert detail.status_code == 200
        assert detail.json()["jobId"] is None and detail.json()["projectId"] is None
        assert detail.json()["jobRef"] == "operation-job" and detail.json()["projectRef"] == "proj-1"
        assert detail.json()["approvalSnapshot"]["id"] == failed.approval_ref
        retry = await client.post(
            endpoint + "/retry",
            json={"expectedVersion": failed.version, "requestId": "missing-owner"},
        )
        assert retry.status_code == 409
        assert len((await client.get("/api/projects/proj-1/tracker-links/operations")).json()["items"]) == 1

    def _mcp_tracker(self, session_factory, approval_service, service):
        from unittest.mock import AsyncMock

        from backend.mcp.server import create_mcp_server

        server = create_mcp_server(
            session_factory=session_factory,
            runtime_service=AsyncMock(),
            approval_service=approval_service,
            merge_service=AsyncMock(),
            tracker_write_service=service,
        )
        return server._tool_manager._tools["codeplane_tracker"].fn

    async def test_mcp_keeps_originating_task_link_as_live_dispatch_dependency(
        self,
        client,
        app,
        session_factory,
        approval_service,
        monkeypatch,
    ):
        import asyncio
        from unittest.mock import AsyncMock

        from sqlalchemy import delete

        from backend.models.db import TaskLinkRow
        from backend.persistence.database import serialized_write
        from backend.persistence.task_link_repo import TaskLinkRepository

        dispatch = AsyncMock(return_value={"id": "must-not-send"})
        monkeypatch.setattr("backend.services.tracker_resolution.dispatch_tracker_write", dispatch)
        service, original = await self._operation(client, app, session_factory)
        async with serialized_write(session_factory) as session:
            task = await TaskLinkRepository(session).create_manual(
                project_id="proj-1",
                repo_path="/test/repo",
                tracker_link_id=original.tracker_link_ref,
                tracker_ticket_ref="owner/repo#7",
                prompt_override="Originating task",
            )
            await TaskLinkRepository(session).set_job_id(task.id, "operation-job")
        tool = self._mcp_tracker(session_factory, approval_service, service)
        caller = asyncio.create_task(
            tool(
                action="comment",
                job_id="operation-job",
                value="Origin must remain live.",
                request_id="origin-required",
            )
        )
        try:
            operation = None
            for _ in range(200):
                matches = [
                    item
                    for item in await service.list_operations("proj-1")
                    if item.source_key == "mcp:operation-job:origin-required"
                ]
                if matches and matches[0].approval_id is not None:
                    operation = matches[0]
                    break
                await asyncio.sleep(0.01)
            assert operation is not None
            async with serialized_write(session_factory) as session:
                await session.execute(delete(TaskLinkRow).where(TaskLinkRow.id == task.id))
            await approval_service.resolve(operation.approval_id, "approved")
            outcome = await asyncio.wait_for(caller, 3)
            assert outcome["state"] == "failed"
            persisted = await service.get_operation(operation.id)
            assert persisted.task_link_ref == task.id and persisted.task_link_id is None
            assert persisted.error_code == "dependency_deleted"
            dispatch.assert_not_awaited()
        finally:
            if not caller.done():
                caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)

    async def test_mcp_list_recovers_history_without_a_live_job(
        self,
        client,
        app,
        session_factory,
        approval_service,
    ):
        from sqlalchemy import delete

        from backend.models.db import ApprovalRow, JobRow
        from backend.persistence.database import serialized_write

        service, operation = await self._operation(client, app, session_factory)
        await self._wait(service, operation.id, lambda item: item.approval_id is not None)
        async with serialized_write(session_factory) as session:
            await session.execute(delete(ApprovalRow).where(ApprovalRow.job_id == "operation-job"))
            await session.execute(delete(JobRow).where(JobRow.id == "operation-job"))
        await self._wait(service, operation.id, lambda item: item.state == "failed")
        tool = self._mcp_tracker(session_factory, approval_service, service)
        listed = await tool(action="list", job_id="operation-job")
        assert "items" in listed, listed
        assert len(listed["items"]) == 1
        assert listed["items"][0]["id"] == operation.id
        assert listed["items"][0]["jobId"] is None
        assert listed["items"][0]["jobRef"] == "operation-job"
        detail = await tool(action="status", job_id="operation-job", operation_id=operation.id)
        assert detail["id"] == operation.id


class TestCreateTrackerLink:
    @pytest.mark.asyncio
    async def test_validates_before_starting_insert_transaction(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        mock_tracker_sync_service: AsyncMock,
    ) -> None:
        from sqlalchemy import func, select

        from backend.models.db import TrackerLinkRow

        await _seed_project(session_factory)
        credential_id = await _create_credential(client)

        async def assert_no_pending_insert(**_: str) -> None:
            async with session_factory() as session:
                count = await session.scalar(select(func.count()).select_from(TrackerLinkRow))
            assert count == 0

        mock_tracker_sync_service.test_link.side_effect = assert_no_pending_insert
        resp = await client.post(
            "/api/projects/proj-1/tracker-links",
            json={"credentialId": credential_id, "externalRef": "ORG/board-1"},
        )

        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_attach_credential_to_project_creates_tracker_link(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        mock_tracker_sync_service: AsyncMock,
    ) -> None:
        await _seed_project(session_factory)
        credential_id = await _create_credential(client)

        resp = await client.post(
            "/api/projects/proj-1/tracker-links",
            json={"credentialId": credential_id, "externalRef": "ORG/board-1"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["projectId"] == "proj-1"
        assert body["credentialId"] == credential_id
        assert body["externalRef"] == "ORG/board-1"
        assert "id" in body
        assert "createdAt" in body
        mock_tracker_sync_service.test_link.assert_awaited_once_with(
            credential_id=credential_id,
            external_ref="ORG/board-1",
        )

    @pytest.mark.asyncio
    async def test_project_can_have_more_than_one_tracker_link(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project(session_factory)
        cred_1 = await _create_credential(client)
        cred_2_resp = await client.post(
            "/api/settings/credentials",
            json={
                "provider": "jira",
                "label": "Jira",
                "baseUrl": "https://x.atlassian.net",
                "pat": "tok",
                "email": "jira@example.com",
            },
        )
        cred_2 = cred_2_resp.json()["id"]

        await client.post(
            "/api/projects/proj-1/tracker-links", json={"credentialId": cred_1, "externalRef": "ORG/board-1"}
        )
        await client.post(
            "/api/projects/proj-1/tracker-links", json={"credentialId": cred_2, "externalRef": "ORG/board-2"}
        )

        resp = await client.get("/api/projects/proj-1/tracker-links")
        assert resp.status_code == 200
        links = resp.json()["trackerLinks"]
        assert len(links) == 2

    @pytest.mark.asyncio
    async def test_same_credential_attaches_to_multiple_projects(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project(session_factory, "proj-1")
        await _seed_project(session_factory, "proj-2")
        credential_id = await _create_credential(client)

        resp_1 = await client.post(
            "/api/projects/proj-1/tracker-links", json={"credentialId": credential_id, "externalRef": "ORG/board-1"}
        )
        resp_2 = await client.post(
            "/api/projects/proj-2/tracker-links", json={"credentialId": credential_id, "externalRef": "ORG/board-2"}
        )
        assert resp_1.status_code == 201
        assert resp_2.status_code == 201

    @pytest.mark.asyncio
    async def test_returns_404_when_project_does_not_exist(self, client: AsyncClient) -> None:
        credential_id = await _create_credential(client)

        resp = await client.post(
            "/api/projects/does-not-exist/tracker-links",
            json={"credentialId": credential_id, "externalRef": "ORG/board-1"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_404_when_credential_does_not_exist(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project(session_factory)

        resp = await client.post(
            "/api/projects/proj-1/tracker-links",
            json={"credentialId": "does-not-exist", "externalRef": "ORG/board-1"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rejects_empty_external_ref(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project(session_factory)
        credential_id = await _create_credential(client)

        resp = await client.post(
            "/api/projects/proj-1/tracker-links", json={"credentialId": credential_id, "externalRef": ""}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_provider_validation_failure_does_not_save_link(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        mock_tracker_sync_service: AsyncMock,
    ) -> None:
        from backend.services.tracker_sync_service import TrackerLinkValidationError

        await _seed_project(session_factory)
        credential_id = await _create_credential(client)
        mock_tracker_sync_service.test_link.side_effect = TrackerLinkValidationError(
            "GitHub external ref must use owner/project-number"
        )

        resp = await client.post(
            "/api/projects/proj-1/tracker-links",
            json={"credentialId": credential_id, "externalRef": "invalid"},
        )

        assert resp.status_code == 422
        listed = await client.get("/api/projects/proj-1/tracker-links")
        assert listed.json() == {"trackerLinks": []}


class TestDetachTrackerLink:
    @pytest.mark.asyncio
    async def test_detach_removes_only_explicit_project_link(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await _seed_project(session_factory, "proj-1")
        await _seed_project(session_factory, "proj-2")
        credential_id = await _create_credential(client)
        first = await client.post(
            "/api/projects/proj-1/tracker-links",
            json={"credentialId": credential_id, "externalRef": "acme/1"},
        )
        second = await client.post(
            "/api/projects/proj-2/tracker-links",
            json={"credentialId": credential_id, "externalRef": "acme/2"},
        )

        response = await client.delete(f"/api/projects/proj-1/tracker-links/{first.json()['id']}")

        assert response.status_code == 204
        assert (await client.get("/api/projects/proj-1/tracker-links")).json() == {"trackerLinks": []}
        project_two = await client.get("/api/projects/proj-2/tracker-links")
        assert project_two.json()["trackerLinks"][0]["id"] == second.json()["id"]

    @pytest.mark.asyncio
    async def test_detach_rejects_link_owned_by_another_project(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await _seed_project(session_factory, "proj-1")
        await _seed_project(session_factory, "proj-2")
        credential_id = await _create_credential(client)
        created = await client.post(
            "/api/projects/proj-2/tracker-links",
            json={"credentialId": credential_id, "externalRef": "acme/2"},
        )

        response = await client.delete(f"/api/projects/proj-1/tracker-links/{created.json()['id']}")

        assert response.status_code == 404
        project_two = await client.get("/api/projects/proj-2/tracker-links")
        assert len(project_two.json()["trackerLinks"]) == 1


class TestListTrackerLinks:
    @pytest.mark.asyncio
    async def test_empty_list_for_project_with_no_links(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project(session_factory)

        resp = await client.get("/api/projects/proj-1/tracker-links")
        assert resp.status_code == 200
        assert resp.json() == {"trackerLinks": []}

    @pytest.mark.asyncio
    async def test_list_does_not_leak_across_projects(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project(session_factory, "proj-1")
        await _seed_project(session_factory, "proj-2")
        credential_id = await _create_credential(client)

        await client.post(
            "/api/projects/proj-1/tracker-links", json={"credentialId": credential_id, "externalRef": "ORG/board-1"}
        )

        resp = await client.get("/api/projects/proj-2/tracker-links")
        assert resp.json() == {"trackerLinks": []}

    @pytest.mark.asyncio
    async def test_list_includes_persisted_summary_without_secret_material(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        from backend.persistence.tracker_summary_repo import TrackerSummaryRepository
        from backend.services.tracker_adapter import TrackerTicket

        await _seed_project(session_factory)
        credential_id = await _create_credential(client)
        created = await client.post(
            "/api/projects/proj-1/tracker-links",
            json={"credentialId": credential_id, "externalRef": "acme/7"},
        )
        link_id = created.json()["id"]
        async with session_factory() as session:
            await TrackerSummaryRepository(session).record_success(
                link_id,
                [TrackerTicket(id="42", title="Ship it", status="Ready", url=None)],
            )
            await session.commit()

        resp = await client.get("/api/projects/proj-1/tracker-links")

        assert resp.status_code == 200
        summary = resp.json()["trackerLinks"][0]["summary"]
        assert summary["tickets"][0]["title"] == "Ship it"
        assert "secret" not in str(resp.json()).lower()
        assert "encrypted" not in str(resp.json()).lower()


class TestRefreshTrackerLink:
    @pytest.mark.asyncio
    async def test_manual_refresh_delegates_to_sync_service(
        self,
        client: AsyncClient,
        mock_tracker_sync_service: AsyncMock,
    ) -> None:
        mock_tracker_sync_service.refresh_link.return_value = {
            "tracker_link_id": "link-1",
            "tickets": [{"id": "1", "title": "Ticket", "status": "Open", "url": None}],
            "last_synced_at": "2026-08-10T12:00:00+00:00",
            "last_error": None,
        }

        resp = await client.post("/api/projects/proj-1/tracker-links/link-1/refresh")

        assert resp.status_code == 200
        assert resp.json()["tickets"][0]["status"] == "Open"
        mock_tracker_sync_service.refresh_link.assert_awaited_once_with(
            project_id="proj-1",
            link_id="link-1",
        )

    @pytest.mark.asyncio
    async def test_no_inbound_webhook_route_exists(self, client: AsyncClient) -> None:
        resp = await client.post("/api/tracker-webhooks/github", json={})

        assert resp.status_code == 404
