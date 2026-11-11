"""Integration tests for TaskLink ingestion endpoints (Story 4.2 / CAP-9).

Exercises:
  POST /api/settings/projects/{id}/ingest-tasks
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



def _write_spec_task(repo_root: Path, filename: str, dependency: str = "") -> None:
    tasks_file = repo_root / "specs" / "fixture" / "tasks.md"
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    task_id = filename.removesuffix(".md")
    suffix = f" (depends on: {dependency})" if dependency else ""
    with tasks_file.open("a", encoding="utf-8") as handle:
        handle.write(f"- [ ] {task_id} Fixture task{suffix}\n")

class TestIngestTasks:
    @pytest.mark.asyncio
    async def test_ingest_creates_task_links_for_member_repos(self, client: AsyncClient, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-a"
        repo_a.mkdir()
        _write_spec_task(repo_a, "T001.md")
        _write_spec_task(repo_a, "T002.md", "T001")

        created = await client.post(
            "/api/settings/projects",
            json={"name": "Ingest Me", "repoPaths": [str(repo_a)]},
        )
        assert created.status_code == 201
        project_id = created.json()["id"]

        resp = await client.post(f"/api/settings/projects/{project_id}/ingest-tasks")
        assert resp.status_code == 201
        items = resp.json()["items"]
        assert len(items) == 2
        by_node = {i["storyNodeId"]: i for i in items}
        assert by_node["T001"]["epicId"] is None
        assert by_node["T002"]["dependsOn"][0].endswith("::T001")

    @pytest.mark.asyncio
    async def test_reingest_upserts_not_duplicates(self, client: AsyncClient, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-b"
        repo_a.mkdir()
        _write_spec_task(repo_a, "T002.md")

        created = await client.post(
            "/api/settings/projects",
            json={"name": "Reingest Me", "repoPaths": [str(repo_a)]},
        )
        project_id = created.json()["id"]

        first = await client.post(f"/api/settings/projects/{project_id}/ingest-tasks")
        second = await client.post(f"/api/settings/projects/{project_id}/ingest-tasks")

        assert len(first.json()["items"]) == 1
        assert len(second.json()["items"]) == 1
        assert first.json()["items"][0]["id"] == second.json()["items"][0]["id"]

    @pytest.mark.asyncio
    async def test_ingest_missing_project_returns_404(self, client: AsyncClient) -> None:
        resp = await client.post("/api/settings/projects/does-not-exist/ingest-tasks")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_ingest_never_writes_to_source_repo(self, client: AsyncClient, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-c"
        repo_a.mkdir()
        _write_spec_task(repo_a, "T003.md")
        story_file = repo_a / "specs" / "fixture" / "tasks.md"
        before = story_file.read_text(encoding="utf-8")

        created = await client.post(
            "/api/settings/projects",
            json={"name": "Read Only", "repoPaths": [str(repo_a)]},
        )
        project_id = created.json()["id"]
        await client.post(f"/api/settings/projects/{project_id}/ingest-tasks")

        assert story_file.read_text(encoding="utf-8") == before


class TestListTaskLinks:
    @pytest.mark.asyncio
    async def test_list_returns_ingested_task_links(self, client: AsyncClient, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-d"
        repo_a.mkdir()
        _write_spec_task(repo_a, "T005.md")

        created = await client.post(
            "/api/settings/projects",
            json={"name": "Listable", "repoPaths": [str(repo_a)]},
        )
        project_id = created.json()["id"]
        await client.post(f"/api/settings/projects/{project_id}/ingest-tasks")

        resp = await client.get(f"/api/settings/projects/{project_id}/task-links")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    @pytest.mark.asyncio
    async def test_list_before_ingest_is_empty(self, client: AsyncClient) -> None:
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
