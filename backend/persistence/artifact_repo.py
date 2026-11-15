"""Artifact metadata persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from backend.models.api_schemas import ArtifactType, ExecutionPhase
from backend.models.db import ArtifactRow
from backend.models.domain import Artifact
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime


class ArtifactRepository(BaseRepository):
    """Database access for artifact metadata records."""

    @staticmethod
    def _to_domain(row: ArtifactRow) -> Artifact:
        return Artifact(
            id=row.id,
            job_id=row.job_id,
            name=row.name,
            type=ArtifactType(row.type),
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            disk_path=row.disk_path,
            phase=ExecutionPhase(row.phase),
            created_at=row.created_at,
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

    async def update_size_bytes(self, artifact_id: str, size_bytes: int, *, disk_path: str | None = None) -> None:
        """Update file size and, for an explicit legacy upsert, its new path."""
        result = await self._session.execute(select(ArtifactRow).where(ArtifactRow.id == artifact_id))
        row = result.scalar_one_or_none()
        if row is not None:
            row.size_bytes = size_bytes
            if disk_path is not None:
                row.disk_path = disk_path
            await self._session.flush()

    async def delete_expired(self, cutoff: datetime) -> list[Artifact]:
        """Find and delete artifact rows created before *cutoff*.

        Returns the domain objects so the caller can clean up disk files.
        """
        stmt = select(ArtifactRow).where(ArtifactRow.created_at < cutoff)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        if not rows:
            return []

        domain_objects = [self._to_domain(r) for r in rows]
        artifact_ids = [a.id for a in domain_objects]
        await self._session.execute(delete(ArtifactRow).where(ArtifactRow.id.in_(artifact_ids)))
        await self._session.flush()
        return domain_objects
