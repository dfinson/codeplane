"""Artifact retrieval endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from backend.config import CPLConfig, load_config
from backend.models.api_schemas import ArtifactListResponse, ArtifactResponse
from backend.persistence.artifact_repo import ArtifactRepository
from backend.services.artifact_service import ArtifactService

router = APIRouter(tags=["artifacts"])


def _get_config() -> CPLConfig:
    return load_config()


@router.get("/jobs/{job_id}/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(
    request: Request,
    job_id: str,
) -> ArtifactListResponse:
    """List all artifacts for a job."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        svc = ArtifactService(ArtifactRepository(session))
        artifacts = await svc.list_for_job(job_id)
        await session.commit()

    items = [
        ArtifactResponse(
            id=a.id,
            job_id=a.job_id,
            name=a.name,
            type=a.type,
            mime_type=a.mime_type,
            size_bytes=a.size_bytes,
            phase=a.phase,
            created_at=a.created_at,
        )
        for a in artifacts
    ]
    return ArtifactListResponse(items=items)


@router.get("/artifacts/{artifact_id}")
async def download_artifact(
    request: Request,
    artifact_id: str,
) -> FileResponse:
    """Download an artifact file."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        svc = ArtifactService(ArtifactRepository(session))
        artifact = await svc.get(artifact_id)
        await session.commit()

    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    disk_path = Path(artifact.disk_path).resolve()
    # Validate artifact path is within the expected artifacts directory
    from backend.services.artifact_service import _ARTIFACTS_BASE

    if not disk_path.is_relative_to(_ARTIFACTS_BASE.resolve()):
        raise HTTPException(status_code=403, detail="Artifact path outside allowed directory")
    if not disk_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file missing from disk")

    return FileResponse(
        path=str(disk_path),
        media_type=artifact.mime_type,
        filename=artifact.name,
    )
