"""Integration tests for the V8 layered diff endpoints.

- GET /api/jobs/{job_id}/motivations
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient

    from backend.tests.integration.conftest import SeedJobFn


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/motivations
# ---------------------------------------------------------------------------


class TestMotivations:
    """GET /api/jobs/{job_id}/motivations"""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_spans(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
    ) -> None:
        job_id = await seed_job()
        resp = await client.get(f"/api/jobs/{job_id}/motivations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["jobId"] == job_id
        assert body["fileMotivations"] == {}
        assert body["hunkMotivations"] == {}

    @pytest.mark.asyncio
    async def test_404_for_nonexistent_job(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/jobs/ghost-id/motivations")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_response_structure(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
    ) -> None:
        """Response uses camelCase field names."""
        job_id = await seed_job()
        resp = await client.get(f"/api/jobs/{job_id}/motivations")
        body = resp.json()
        assert "jobId" in body
        assert "fileMotivations" in body
        assert "hunkMotivations" in body


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("GET", "structural-diff"),
        ("GET", "multi-session"),
        ("GET", "impact-graph/worker"),
        ("POST", "impact-graph-batch"),
        ("GET", "communities"),
        ("GET", "review-story"),
        ("GET", "covering-tests"),
        ("GET", "line-coverage"),
        ("GET", "blast-radius"),
    ],
)
async def test_retired_structural_routes_are_absent(
    client: AsyncClient, seed_job: SeedJobFn, method: str, suffix: str
) -> None:
    job_id = await seed_job()
    response = await client.request(method, f"/api/jobs/{job_id}/{suffix}")
    assert response.status_code == 404


async def test_retired_repo_health_route_is_absent(client: AsyncClient) -> None:
    response = await client.get("/api/settings/repos/%2Ftest%2Frepo/health")
    assert response.status_code == 404
