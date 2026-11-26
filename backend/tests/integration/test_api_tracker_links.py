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


class TestCreateTrackerLink:
    @pytest.mark.asyncio
    async def test_copilot_credential_is_rejected_without_saving_link(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        mock_tracker_sync_service: AsyncMock,
    ) -> None:
        from unittest.mock import AsyncMock

        from backend.services.tracker_sync_service import TrackerSyncService

        await _seed_project(session_factory)
        credential = await client.post(
            "/api/settings/credentials",
            json={
                "provider": "copilot",
                "label": "Inference only",
                "baseUrl": "https://github.com",
                "pat": "test-inference-token",
            },
        )
        assert credential.status_code == 201
        adapter = AsyncMock()
        service = TrackerSyncService(
            session_factory=session_factory,
            adapters={"github": adapter, "jira": adapter, "azure_devops": adapter},
        )
        mock_tracker_sync_service.test_link.side_effect = service.test_link

        response = await client.post(
            "/api/projects/proj-1/tracker-links",
            json={"credentialId": credential.json()["id"], "externalRef": "acme/1"},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "Unsupported tracker provider: copilot"
        assert (await client.get("/api/projects/proj-1/tracker-links")).json() == {"trackerLinks": []}
        adapter.test_connection.assert_not_awaited()

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
