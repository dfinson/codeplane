"""Artifact metadata persistence."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select

from backend.models.db import ArtifactRow
from backend.models.domain import Artifact
from backend.persistence.repository import BaseRepository


class ArtifactRepository(BaseRepository):
    """Database access for artifact metadata records."""

    @staticmethod
    def _to_domain(row: ArtifactRow) -> Artifact:
        # SQLAlchemy Column descriptors return Any at the type level;
        # cast() documents the expected runtime type for each field.
        return Artifact(
            id=cast(str, row.id),
            job_id=cast(str, row.job_id),
            name=cast(str, row.name),
            type=cast(str, row.type),
            mime_type=cast(str, row.mime_type),
            size_bytes=cast(int, row.size_bytes),
            disk_path=cast(str, row.disk_path),
            phase=cast(str, row.phase),
            created_at=cast("datetime", row.created_at),
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

    async def update_size_bytes(self, artifact_id: str, size_bytes: int) -> None:
        """Update the stored file size after appending to a unified log."""
        result = await self._session.execute(select(ArtifactRow).where(ArtifactRow.id == artifact_id))
        row = result.scalar_one_or_none()
        if row is not None:
            row.size_bytes = size_bytes  # type: ignore[assignment]  # Column[int] vs int
            await self._session.flush()
