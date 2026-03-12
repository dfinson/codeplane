"""Artifact metadata persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from datetime import datetime

from backend.models.db import ArtifactRow
from backend.persistence.repository import BaseRepository


@dataclass
class Artifact:
    """Domain representation of an artifact record."""

    id: str
    job_id: str
    name: str
    type: str
    mime_type: str
    size_bytes: int
    disk_path: str
    phase: str
    created_at: datetime


class ArtifactRepository(BaseRepository):
    """Database access for artifact metadata records."""

    @staticmethod
    def _to_domain(row: ArtifactRow) -> Artifact:
        return Artifact(
            id=row.id,  # type: ignore[arg-type]
            job_id=row.job_id,  # type: ignore[arg-type]
            name=row.name,  # type: ignore[arg-type]
            type=row.type,  # type: ignore[arg-type]
            mime_type=row.mime_type,  # type: ignore[arg-type]
            size_bytes=row.size_bytes,  # type: ignore[arg-type]
            disk_path=row.disk_path,  # type: ignore[arg-type]
            phase=row.phase,  # type: ignore[arg-type]
            created_at=row.created_at,  # type: ignore[arg-type]
        )

    async def create(self, artifact: Artifact) -> Artifact:
        """Insert an artifact metadata record."""
        row = ArtifactRow(
            id=artifact.id,
            job_id=artifact.job_id,
            name=artifact.name,
            type=artifact.type,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size_bytes,
            disk_path=artifact.disk_path,
            phase=artifact.phase,
            created_at=artifact.created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return artifact

    async def list_for_job(self, job_id: str) -> list[Artifact]:
        """List all artifacts for a given job."""
        stmt = select(ArtifactRow).where(ArtifactRow.job_id == job_id).order_by(ArtifactRow.created_at)
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def get(self, artifact_id: str) -> Artifact | None:
        """Retrieve a single artifact by ID."""
        result = await self._session.execute(select(ArtifactRow).where(ArtifactRow.id == artifact_id))
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._to_domain(row)
