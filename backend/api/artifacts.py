"""Artifact retrieval endpoints."""

from __future__ import annotations

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.models.api_schemas import ArtifactListResponse, ArtifactResponse
from backend.models.domain import JobNotFoundError
from backend.services.artifacts.artifact_service import ArtifactService, resolve_artifact_path
from backend.services.job.job_service import JobService

router = APIRouter(tags=["artifacts"], route_class=DishkaRoute)


@router.get("/jobs/{job_id}/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(
    job_id: str,
    svc: FromDishka[ArtifactService],
    job_svc: FromDishka[JobService],
) -> ArtifactListResponse:
    """List all artifacts for a job."""
    artifacts = await svc.list_for_job(job_id)
    try:
        job = await job_svc.get_job(job_id)
    except JobNotFoundError:
        job = None

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
    return ArtifactListResponse(
        items=items,
        collection_status=job.artifact_collection_status if job is not None else "completed",
        collection_error=job.artifact_collection_error if job is not None else None,
        collection_updated_at=job.artifact_collection_updated_at if job is not None else None,
    )


@router.get("/artifacts/{artifact_id}", response_class=FileResponse, response_model=None)
async def download_artifact(
    artifact_id: str,
    svc: FromDishka[ArtifactService],
) -> FileResponse:
    """Download an artifact file."""
    artifact = await svc.get(artifact_id)

    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    try:
        disk_path = resolve_artifact_path(artifact)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=403, detail="Artifact path outside allowed directory") from exc
    if not disk_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file missing from disk")

    return FileResponse(
        path=str(disk_path),
        media_type=artifact.mime_type,
        filename=artifact.name,
    )
