"""Integration tests for the Project API endpoints (Story 2.1 / CAP-6, Story 2.2 / CAP-2).

Exercises:
  POST  /api/settings/projects
  GET   /api/settings/projects
  GET   /api/settings/projects/summary
  GET   /api/settings/projects/{id}
  PATCH /api/settings/projects/{id}
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from backend.models.db import Base, JobRow
from backend.persistence.database import create_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
    from starlette.types import Message, Receive, Scope, Send


def _resolved(path: str) -> str:
    return str(Path(path).expanduser().resolve())


class TestCreateProject:
    @pytest.mark.asyncio
    async def test_create_single_repo_project(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/settings/projects",
            json={"name": "My Project", "repoPaths": ["/test/repo"]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Project"
        assert data["repoPaths"] == [_resolved("/test/repo")]
        assert "id" in data
        assert "createdAt" in data
        assert "updatedAt" in data

    @pytest.mark.asyncio
    async def test_create_multi_repo_project(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/settings/projects",
            json={"name": "Multi Repo", "repoPaths": ["/test/repo-a", "/test/repo-b"]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert sorted(data["repoPaths"]) == sorted([_resolved("/test/repo-a"), _resolved("/test/repo-b")])

    @pytest.mark.asyncio
    async def test_create_rejects_repo_already_assigned_to_another_project(self, client: AsyncClient) -> None:
        first = await client.post(
            "/api/settings/projects",
            json={"name": "First", "repoPaths": ["/test/shared-repo"]},
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/settings/projects",
            json={"name": "Second", "repoPaths": ["/test/shared-repo"]},
        )
        assert second.status_code == 409

    @pytest.mark.asyncio
    async def test_create_requires_name(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/settings/projects",
            json={"name": "", "repoPaths": ["/test/repo"]},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_requires_at_least_one_repo_path(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/settings/projects",
            json={"name": "No Repos", "repoPaths": []},
        )
        assert resp.status_code == 422


class TestProjectCommitBoundary:
    @pytest.fixture
    async def engine(self, tmp_path: Path) -> AsyncIterator[AsyncEngine]:
        engine = create_engine(tmp_path / "project-boundary.db")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            yield engine
        finally:
            await engine.dispose()

    @pytest.mark.parametrize("operation", ["create", "update"])
    async def test_membership_is_durable_before_the_success_response(
        self, app: FastAPI, client: AsyncClient, tmp_path: Path, operation: str
    ) -> None:
        endpoint = "/api/settings/projects"
        target = "/test/commit-boundary"
        if operation == "update":
            created = await client.post(endpoint, json={"name": "Commit boundary", "repoPaths": [target]})
            assert created.status_code == 201
            endpoint += f"/{created.json()['id']}"
            target += "-updated"
        observed: list[list[list[str]]] = []

        async def boundary(scope: Scope, receive: Receive, send: Send) -> None:
            async def observe(message: Message) -> None:
                if message["type"] == "http.response.start":
                    with sqlite3.connect(f"file:{tmp_path / 'project-boundary.db'}?mode=ro", uri=True) as database:
                        rows = database.execute(
                            "SELECT repo_paths FROM projects WHERE name=?", ("Commit boundary",)
                        ).fetchall()
                    observed.append([json.loads(row[0]) for row in rows])
                await send(message)

            await app(scope, receive, observe)

        async with AsyncClient(transport=ASGITransport(app=boundary), base_url="http://test") as boundary_client:
            response = await boundary_client.request(
                "POST" if operation == "create" else "PATCH",
                endpoint,
                json={
                    "name": "Commit boundary",
                    "repoPaths": [target],
                    **({"confirmRepoRemoval": True} if operation == "update" else {}),
                },
            )
        assert response.status_code == (201 if operation == "create" else 200)
        assert observed == [[[_resolved(target)]]]


class TestListAndGetProject:
    @pytest.mark.asyncio
    async def test_list_returns_created_projects(self, client: AsyncClient) -> None:
        await client.post("/api/settings/projects", json={"name": "A", "repoPaths": ["/test/a"]})
        await client.post("/api/settings/projects", json={"name": "B", "repoPaths": ["/test/b"]})

        resp = await client.get("/api/settings/projects")
        assert resp.status_code == 200
        items = resp.json()["items"]
        names = {p["name"] for p in items}
        assert {"A", "B"}.issubset(names)

    @pytest.mark.asyncio
    async def test_get_existing_project(self, client: AsyncClient) -> None:
        created = await client.post(
            "/api/settings/projects",
            json={"name": "Fetchable", "repoPaths": ["/test/fetchable"]},
        )
        project_id = created.json()["id"]

        resp = await client.get(f"/api/settings/projects/{project_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Fetchable"

    @pytest.mark.asyncio
    async def test_get_missing_project_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/settings/projects/does-not-exist")
        assert resp.status_code == 404


class TestProjectsSummary:
    """AC1-3 (Story 2.2): batch Overview summary — single call, includes zero-job Projects."""

    @pytest.mark.asyncio
    async def test_zero_job_project_appears_with_zero_counts(self, client: AsyncClient) -> None:
        created = await client.post(
            "/api/settings/projects",
            json={"name": "Idle Project", "repoPaths": ["/test/summary-idle"]},
        )
        project_id = created.json()["id"]

        resp = await client.get("/api/settings/projects/summary")
        assert resp.status_code == 200
        items = resp.json()["items"]
        entry = next(i for i in items if i["id"] == project_id)
        assert entry["activeJobCount"] == 0
        assert entry["awaitingInputCount"] == 0
        assert entry["failedCount"] == 0
        assert entry["lastActivityAt"] is None

    @pytest.mark.asyncio
    async def test_counts_bucketed_by_job_state(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        created = await client.post(
            "/api/settings/projects",
            json={"name": "Busy Project", "repoPaths": ["/test/summary-busy"]},
        )
        project_id = created.json()["id"]
        repo = _resolved("/test/summary-busy")

        async with session_factory() as session:
            session.add_all(
                [
                    JobRow(
                        id="job-active",
                        repo=repo,
                        project_id=project_id,
                        prompt="p",
                        state="running",
                        base_ref="main",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    ),
                    JobRow(
                        id="job-awaiting",
                        repo=repo,
                        project_id=project_id,
                        prompt="p",
                        state="waiting_for_approval",
                        base_ref="main",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    ),
                    JobRow(
                        id="job-failed",
                        repo=repo,
                        project_id=project_id,
                        prompt="p",
                        state="failed",
                        base_ref="main",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    ),
                ]
            )
            await session.commit()

        resp = await client.get("/api/settings/projects/summary")
        assert resp.status_code == 200
        items = resp.json()["items"]
        entry = next(i for i in items if i["id"] == project_id)
        assert entry["activeJobCount"] == 1
        assert entry["awaitingInputCount"] == 1
        assert entry["failedCount"] == 1
        assert entry["lastActivityAt"] is not None

    @pytest.mark.asyncio
    async def test_single_call_returns_all_projects(self, client: AsyncClient) -> None:
        """AC2: sourced from a single batch call, never N sequential per-Project fetches."""
        await client.post("/api/settings/projects", json={"name": "P1", "repoPaths": ["/test/summary-p1"]})
        await client.post("/api/settings/projects", json={"name": "P2", "repoPaths": ["/test/summary-p2"]})

        resp = await client.get("/api/settings/projects/summary")
        assert resp.status_code == 200
        names = {i["name"] for i in resp.json()["items"]}
        assert {"P1", "P2"}.issubset(names)


class TestUpdateProject:
    @pytest.mark.asyncio
    async def test_rename_project(self, client: AsyncClient) -> None:
        created = await client.post(
            "/api/settings/projects",
            json={"name": "Old Name", "repoPaths": ["/test/rename"]},
        )
        project_id = created.json()["id"]

        resp = await client.patch(f"/api/settings/projects/{project_id}", json={"name": "New Name"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New Name"
        assert data["repoPaths"] == [_resolved("/test/rename")]

    @pytest.mark.asyncio
    async def test_add_repo_to_project(self, client: AsyncClient) -> None:
        created = await client.post(
            "/api/settings/projects",
            json={"name": "Growable", "repoPaths": ["/test/growable-a"]},
        )
        project_id = created.json()["id"]

        resp = await client.patch(
            f"/api/settings/projects/{project_id}",
            json={"repoPaths": ["/test/growable-a", "/test/growable-b"]},
        )
        assert resp.status_code == 200
        assert sorted(resp.json()["repoPaths"]) == sorted(
            [_resolved("/test/growable-a"), _resolved("/test/growable-b")]
        )

    @pytest.mark.asyncio
    async def test_remove_repo_from_project(self, client: AsyncClient) -> None:
        created = await client.post(
            "/api/settings/projects",
            json={"name": "Shrinkable", "repoPaths": ["/test/shrink-a", "/test/shrink-b"]},
        )
        project_id = created.json()["id"]

        unconfirmed = await client.patch(
            f"/api/settings/projects/{project_id}",
            json={"repoPaths": ["/test/shrink-a"]},
        )
        assert unconfirmed.status_code == 409

        resp = await client.patch(
            f"/api/settings/projects/{project_id}",
            json={"repoPaths": ["/test/shrink-a"], "confirmRepoRemoval": True},
        )
        assert resp.status_code == 200
        assert resp.json()["repoPaths"] == [_resolved("/test/shrink-a")]

    @pytest.mark.asyncio
    async def test_update_rejects_repo_already_assigned_to_another_project(self, client: AsyncClient) -> None:
        first = await client.post(
            "/api/settings/projects",
            json={"name": "First", "repoPaths": ["/test/owned-by-first"]},
        )
        second = await client.post(
            "/api/settings/projects",
            json={"name": "Second", "repoPaths": ["/test/owned-by-second"]},
        )
        second_id = second.json()["id"]
        assert first.status_code == 201

        resp = await client.patch(
            f"/api/settings/projects/{second_id}",
            json={"repoPaths": ["/test/owned-by-second", "/test/owned-by-first"]},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_update_missing_project_returns_404(self, client: AsyncClient) -> None:
        resp = await client.patch("/api/settings/projects/does-not-exist", json={"name": "X"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_edit_is_reflected_immediately_on_get(self, client: AsyncClient) -> None:
        """AC3: an edit is saved and reflected immediately on the Project resource."""
        created = await client.post(
            "/api/settings/projects",
            json={"name": "Reflect Me", "repoPaths": ["/test/reflect"]},
        )
        project_id = created.json()["id"]

        await client.patch(f"/api/settings/projects/{project_id}", json={"name": "Reflected"})

        resp = await client.get(f"/api/settings/projects/{project_id}")
        assert resp.json()["name"] == "Reflected"
