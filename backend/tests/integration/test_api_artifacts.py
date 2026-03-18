"""Integration tests for artifact endpoints.

- GET /api/jobs/{job_id}/artifacts  (list)
- GET /api/artifacts/{artifact_id}  (download)
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from backend.models.db import ArtifactRow

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.tests.integration.conftest import SeedJobFn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_artifact(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    artifact_id: str = "art-1",
    job_id: str,
    name: str = "output.txt",
    artifact_type: str = "document",
    mime_type: str = "text/plain",
    size_bytes: int = 100,
    disk_path: str = "/tmp/test-artifacts/output.txt",
    phase: str = "finalization",
) -> ArtifactRow:
    row = ArtifactRow(
        id=artifact_id,
        job_id=job_id,
        name=name,
        type=artifact_type,
        mime_type=mime_type,
        size_bytes=size_bytes,
        disk_path=disk_path,
        phase=phase,
        created_at=datetime.now(UTC),
    )
    async with session_factory() as session:
        session.add(row)
        await session.commit()
    return row


# ---------------------------------------------------------------------------
# List artifacts
# ---------------------------------------------------------------------------


class TestListArtifacts:
    """GET /api/jobs/{job_id}/artifacts"""

    @pytest.mark.asyncio
    async def test_empty_list_for_job_with_no_artifacts(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
    ) -> None:
        job_id = await seed_job()
        resp = await client.get(f"/api/jobs/{job_id}/artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []

    @pytest.mark.asyncio
    async def test_returns_artifacts_for_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        job_id = await seed_job()
        await _insert_artifact(session_factory, artifact_id="art-1", job_id=job_id, name="a.txt")
        await _insert_artifact(session_factory, artifact_id="art-2", job_id=job_id, name="b.txt")

        resp = await client.get(f"/api/jobs/{job_id}/artifacts")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        names = {i["name"] for i in items}
        assert names == {"a.txt", "b.txt"}

    @pytest.mark.asyncio
    async def test_response_structure(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """ArtifactListResponse items have expected camelCase fields."""
        job_id = await seed_job()
        await _insert_artifact(
            session_factory,
            job_id=job_id,
            artifact_type="document",
            phase="finalization",
        )

        resp = await client.get(f"/api/jobs/{job_id}/artifacts")
        item = resp.json()["items"][0]
        assert "id" in item
        assert "jobId" in item
        assert "name" in item
        assert "type" in item
        assert "mimeType" in item
        assert "sizeBytes" in item
        assert "phase" in item
        assert "createdAt" in item

    @pytest.mark.asyncio
    async def test_does_not_leak_artifacts_from_other_jobs(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        job_a = await seed_job(job_id="job-a")
        job_b = await seed_job(job_id="job-b")
        await _insert_artifact(session_factory, artifact_id="art-a", job_id=job_a)
        await _insert_artifact(session_factory, artifact_id="art-b", job_id=job_b)

        resp = await client.get(f"/api/jobs/{job_a}/artifacts")
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == "art-a"

    @pytest.mark.asyncio
    async def test_nonexistent_job_returns_empty_list(
        self,
        client: AsyncClient,
    ) -> None:
        """Non-existent job_id returns empty items — no 404."""
        resp = await client.get("/api/jobs/no-such-job/artifacts")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# Download artifact
# ---------------------------------------------------------------------------


class TestDownloadArtifact:
    """GET /api/artifacts/{artifact_id}"""

    @pytest.mark.asyncio
    async def test_nonexistent_artifact_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/artifacts/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_file_missing_from_disk_returns_404(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Artifact exists in DB but the file has been deleted from disk."""
        with tempfile.TemporaryDirectory() as artifacts_base:
            monkeypatch.setattr(
                "backend.services.artifact_service._ARTIFACTS_BASE",
                Path(artifacts_base),
            )
            job_id = await seed_job()
            missing_path = str(Path(artifacts_base) / "missing.txt")
            await _insert_artifact(
                session_factory,
                job_id=job_id,
                disk_path=missing_path,
            )

            resp = await client.get("/api/artifacts/art-1")
            assert resp.status_code == 404
            assert "missing from disk" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_path_outside_allowed_directory_returns_403(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Artifact whose disk_path is outside _ARTIFACTS_BASE is rejected."""
        with tempfile.TemporaryDirectory() as artifacts_base:
            monkeypatch.setattr(
                "backend.services.artifact_service._ARTIFACTS_BASE",
                Path(artifacts_base),
            )
            job_id = await seed_job()
            await _insert_artifact(
                session_factory,
                job_id=job_id,
                disk_path="/etc/passwd",
            )

            resp = await client.get("/api/artifacts/art-1")
            assert resp.status_code == 403
            assert "outside allowed directory" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_valid_download(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Downloading a valid artifact returns 200 with correct content."""
        with tempfile.TemporaryDirectory() as artifacts_base:
            monkeypatch.setattr(
                "backend.services.artifact_service._ARTIFACTS_BASE",
                Path(artifacts_base),
            )
            job_id = await seed_job()
            file_path = Path(artifacts_base) / "output.txt"
            file_path.write_text("hello artifact")

            await _insert_artifact(
                session_factory,
                job_id=job_id,
                disk_path=str(file_path),
                mime_type="text/plain",
                name="output.txt",
            )

            resp = await client.get("/api/artifacts/art-1")
            assert resp.status_code == 200
            assert "text/plain" in resp.headers["content-type"]
            assert resp.text == "hello artifact"
