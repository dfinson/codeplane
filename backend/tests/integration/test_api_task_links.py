"""Source-ingestion retirement and preserved TaskLink assignment APIs.

Exercises:
  POST /api/settings/projects/{id}/task-links
  GET  /api/settings/projects/{id}/task-links
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
    from unittest.mock import AsyncMock

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _attach_tracker_link(client: AsyncClient, project_id: str) -> str:
    credential = await client.post(
        "/api/settings/credentials",
        json={
            "provider": "github",
            "label": f"Credential {project_id}",
            "baseUrl": "https://api.github.com",
            "pat": "test-token",
        },
    )
    assert credential.status_code == 201
    tracker_link = await client.post(
        f"/api/projects/{project_id}/tracker-links",
        json={"credentialId": credential.json()["id"], "externalRef": "ORG/board"},
    )
    assert tracker_link.status_code == 201
    return str(tracker_link.json()["id"])


class TestTaskSourceRetirement:
    @pytest.mark.asyncio
    async def test_source_ingestion_is_absent_and_preserves_saved_tasks(
        self, client: AsyncClient, tmp_path: Path, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        from backend.persistence.task_link_repo import TaskLinkRepository

        repo = tmp_path / "historical-tasks"
        repo.mkdir()
        source = repo / "tasks.md"
        source.write_text("- [ ] T001 Do not import this file\n", encoding="utf-8")
        nested_source = repo / "specs" / "nested" / "tasks.md"
        nested_source.parent.mkdir(parents=True)
        nested_source.write_text("- [ ] T002 Do not discover nested task files\n", encoding="utf-8")
        original_sources = {path: path.read_bytes() for path in (source, nested_source)}
        project = await client.post(
            "/api/settings/projects", json={"name": "Preserved tasks", "repoPaths": [str(repo)]}
        )
        project_id = project.json()["id"]
        async with session_factory() as session:
            await TaskLinkRepository(session).upsert_many(
                project_id,
                [
                    {"repo_path": str(repo), "story_node_id": "historic-root", "depends_on": []},
                    {
                        "repo_path": str(repo),
                        "story_node_id": "historic-child",
                        "depends_on": [f"{repo}::historic-root"],
                    },
                ],
            )
            await session.commit()
        endpoint = f"/api/settings/projects/{project_id}/task-links"
        before = (await client.get(endpoint)).json()
        assert len(before["items"]) == 2
        response = await client.post(f"/api/settings/projects/{project_id}/ingest-tasks")
        assert response.status_code == 404
        assert (await client.get(endpoint)).json() == before
        assert {path: path.read_bytes() for path in original_sources} == original_sources

    def test_openapi_omits_source_ingestion_contract(self) -> None:
        from backend.app_factory import create_app

        schema = create_app().openapi()
        assert "/api/settings/projects/{project_id}/ingest-tasks" not in schema["paths"]
        assert "IngestTaskGraphResponse" not in schema["components"]["schemas"]


class TestListTaskLinks:
    @pytest.mark.asyncio
    async def test_list_returns_saved_task_links(
        self, client: AsyncClient, tmp_path: Path, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        from backend.persistence.task_link_repo import TaskLinkRepository

        repo_a = tmp_path / "repo-d"
        repo_a.mkdir()

        created = await client.post(
            "/api/settings/projects",
            json={"name": "Listable", "repoPaths": [str(repo_a)]},
        )
        project_id = created.json()["id"]
        async with session_factory() as session:
            await TaskLinkRepository(session).upsert_many(
                project_id, [{"repo_path": str(repo_a), "story_node_id": "saved-task", "depends_on": []}]
            )
            await session.commit()

        resp = await client.get(f"/api/settings/projects/{project_id}/task-links")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    @pytest.mark.asyncio
    async def test_list_without_saved_tasks_is_empty(self, client: AsyncClient) -> None:
        created = await client.post(
            "/api/settings/projects",
            json={"name": "Empty Project", "repoPaths": ["/test/empty-proj"]},
        )
        project_id = created.json()["id"]

        resp = await client.get(f"/api/settings/projects/{project_id}/task-links")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    @pytest.mark.asyncio
    async def test_list_missing_project_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/settings/projects/does-not-exist/task-links")
        assert resp.status_code == 404


class TestCreateManualTaskLink:
    @pytest.mark.asyncio
    async def test_create_and_later_list_manual_task_link(
        self, client: AsyncClient, tmp_path: Path, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        from sqlalchemy import select

        from backend.models.db import JobRow

        repo_path = tmp_path / "manual-repo"
        repo_path.mkdir()
        created_project = await client.post(
            "/api/settings/projects",
            json={"name": "Manual Tasks", "repoPaths": [str(repo_path)]},
        )
        project_id = created_project.json()["id"]
        tracker_link_id = await _attach_tracker_link(client, project_id)

        created = await client.post(
            f"/api/settings/projects/{project_id}/task-links",
            json={
                "repoPath": str(repo_path),
                "trackerLinkId": tracker_link_id,
                "trackerTicketRef": "JIRA-123",
                "promptOverride": "Implement the ticket",
                "outputRoutes": ["tracker_write"],
            },
        )

        assert created.status_code == 201
        body = created.json()
        assert body["trackerTicketRef"] == "JIRA-123"
        assert body["trackerLinkId"] == tracker_link_id
        assert body["state"] == "ready"
        assert body["promptOverride"] == "Implement the ticket"
        assert body["storyNodeId"] is None
        assert body["dependsOn"] == []
        assert body["jobId"] is None
        assert body["epicId"] is None
        assert body["outputRoutes"] == ["tracker_write"]

        listed = await client.get(f"/api/settings/projects/{project_id}/task-links")
        assert listed.status_code == 200
        assert listed.json()["items"] == [body]
        async with session_factory() as session:
            jobs = (await session.execute(select(JobRow.id))).scalars().all()
        assert jobs == []

    @pytest.mark.asyncio
    async def test_same_ticket_allows_multiple_independent_task_links(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo_path = tmp_path / "multi-task-repo"
        repo_path.mkdir()
        created_project = await client.post(
            "/api/settings/projects",
            json={"name": "Multiple Tasks", "repoPaths": [str(repo_path)]},
        )
        project_id = created_project.json()["id"]
        tracker_link_id = await _attach_tracker_link(client, project_id)
        endpoint = f"/api/settings/projects/{project_id}/task-links"

        first = await client.post(
            endpoint,
            json={
                "repoPath": str(repo_path),
                "trackerLinkId": tracker_link_id,
                "trackerTicketRef": "JIRA-123",
                "promptOverride": "Implement part one",
            },
        )
        second = await client.post(
            endpoint,
            json={
                "repoPath": str(repo_path),
                "trackerLinkId": tracker_link_id,
                "trackerTicketRef": "JIRA-123",
                "promptOverride": "Implement part two",
            },
        )

        assert first.status_code == second.status_code == 201
        assert first.json()["id"] != second.json()["id"]
        listed = (await client.get(endpoint)).json()["items"]
        assert [item["trackerTicketRef"] for item in listed] == ["JIRA-123", "JIRA-123"]
        assert [item["promptOverride"] for item in listed] == ["Implement part one", "Implement part two"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("trackerTicketRef", ""),
            ("trackerTicketRef", "   "),
            ("promptOverride", ""),
            ("promptOverride", "   "),
        ],
    )
    async def test_rejects_blank_required_text(
        self, client: AsyncClient, tmp_path: Path, field: str, value: str
    ) -> None:
        repo_path = tmp_path / f"blank-{field}-{len(value)}"
        repo_path.mkdir()
        created_project = await client.post(
            "/api/settings/projects",
            json={"name": "Validation", "repoPaths": [str(repo_path)]},
        )
        project_id = created_project.json()["id"]
        tracker_link_id = await _attach_tracker_link(client, project_id)
        payload = {
            "repoPath": str(repo_path),
            "trackerLinkId": tracker_link_id,
            "trackerTicketRef": "JIRA-123",
            "promptOverride": "Implement this",
        }
        payload[field] = value

        response = await client.post(
            f"/api/settings/projects/{project_id}/task-links",
            json=payload,
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_repo_outside_project(self, client: AsyncClient, tmp_path: Path) -> None:
        member_repo = tmp_path / "member-repo"
        other_repo = tmp_path / "other-repo"
        member_repo.mkdir()
        other_repo.mkdir()
        created_project = await client.post(
            "/api/settings/projects",
            json={"name": "Scoped Tasks", "repoPaths": [str(member_repo)]},
        )
        project_id = created_project.json()["id"]
        tracker_link_id = await _attach_tracker_link(client, project_id)

        response = await client.post(
            f"/api/settings/projects/{project_id}/task-links",
            json={
                "repoPath": str(other_repo),
                "trackerLinkId": tracker_link_id,
                "trackerTicketRef": "JIRA-123",
                "promptOverride": "Implement this",
            },
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_project_returns_404(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/settings/projects/does-not-exist/task-links",
            json={
                "repoPath": "/repo/a",
                "trackerLinkId": "tracker-link-missing",
                "trackerTicketRef": "JIRA-123",
                "promptOverride": "Implement this",
            },
        )

        assert response.status_code == 404


class TestStartTaskLink:
    @pytest.mark.asyncio
    async def test_ready_root_starts_once_and_returns_linked_job(
        self,
        client: AsyncClient,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        mock_runtime_service: AsyncMock,
    ) -> None:
        from sqlalchemy import select

        from backend.models.db import JobRow

        repo_path = tmp_path / "start-repo"
        repo_path.mkdir()
        project = await client.post(
            "/api/settings/projects",
            json={"name": "Start Tasks", "repoPaths": [str(repo_path)]},
        )
        project_id = project.json()["id"]
        tracker_link_id = await _attach_tracker_link(client, project_id)
        created = await client.post(
            f"/api/settings/projects/{project_id}/task-links",
            json={
                "repoPath": str(repo_path),
                "trackerLinkId": tracker_link_id,
                "trackerTicketRef": "PAY-42",
                "promptOverride": "Implement PAY-42",
            },
        )
        task_link_id = created.json()["id"]

        async def assert_job_is_committed(job: object) -> None:
            async with session_factory() as session:
                persisted = await session.get(JobRow, job.id)
            assert persisted is not None

        mock_runtime_service.setup_and_start.side_effect = assert_job_is_committed

        started = await client.post(f"/api/settings/projects/{project_id}/task-links/{task_link_id}/start")
        assert started.status_code == 200
        assert started.json()["state"] == "running"
        assert started.json()["jobId"]
        mock_runtime_service.setup_and_start.assert_awaited_once()

        duplicate = await client.post(f"/api/settings/projects/{project_id}/task-links/{task_link_id}/start")
        assert duplicate.status_code == 409
        async with session_factory() as session:
            job_ids = (await session.execute(select(JobRow.id))).scalars().all()
        assert job_ids == [started.json()["jobId"]]
