"""Persistence for TrackerLinks (Story 3.2, CAP-7/AD-6).

A ``TrackerLinkRow`` is the many-to-many join between a Project and a
Credential: attaching a Credential to a Project along with an external
project/board reference. ``project_id`` is a plain string (not a foreign
key — see ``TrackerLinkRow`` docstring), so this repository validates
Project existence itself before inserting, using ``ProjectRow`` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select

from backend.models.db import CredentialRow, ProjectRow, TrackerLinkRow
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult


class TrackerLinkCredentialNotFoundError(Exception):
    """Raised when attaching a TrackerLink to a Credential that does not exist."""


class TrackerLinkProjectNotFoundError(Exception):
    """Raised when attaching a TrackerLink to a Project that does not exist."""


class TrackerLinkRepository(BaseRepository):
    """Database access for TrackerLinks."""

    async def create(self, *, link_id: str, project_id: str, credential_id: str, external_ref: str) -> dict[str, Any]:
        """Attach a Credential to a Project via a new TrackerLink (AC1).

        Validates both the Project and the Credential exist before inserting,
        since ``project_id`` is a plain string with no DB-level FK constraint.
        """
        project_result = await self._session.execute(select(ProjectRow.id).where(ProjectRow.id == project_id))
        if project_result.scalar_one_or_none() is None:
            raise TrackerLinkProjectNotFoundError(f"Project '{project_id}' does not exist")

        credential_result = await self._session.execute(
            select(CredentialRow.id).where(
                CredentialRow.id == credential_id,
                CredentialRow.provider.in_(("github", "jira", "azure_devops")),
            )
        )
        if credential_result.scalar_one_or_none() is None:
            raise TrackerLinkCredentialNotFoundError(f"Credential '{credential_id}' does not exist")

        row = TrackerLinkRow(
            id=link_id,
            project_id=project_id,
            credential_id=credential_id,
            external_ref=external_ref,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._session.add(row)
        await self._session.flush()
        return _row_to_dict(row)

    async def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        """List all TrackerLinks attached to a Project (AC2 — a Project may have several)."""
        result = await self._session.execute(
            select(TrackerLinkRow).where(TrackerLinkRow.project_id == project_id).order_by(TrackerLinkRow.created_at)
        )
        return [_row_to_dict(r) for r in result.scalars()]

    async def get(self, tracker_link_id: str) -> dict[str, Any] | None:
        """Return one TrackerLink by stable ID."""
        row = await self._session.get(TrackerLinkRow, tracker_link_id)
        return _row_to_dict(row) if row is not None else None

    async def delete_for_project(self, *, project_id: str, link_id: str) -> bool:
        """Detach one TrackerLink only when it belongs to the requested Project."""
        result = await self._session.execute(
            delete(TrackerLinkRow).where(
                TrackerLinkRow.id == link_id,
                TrackerLinkRow.project_id == project_id,
            )
        )
        return cast("CursorResult[Any]", result).rowcount > 0


def _row_to_dict(row: TrackerLinkRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "credential_id": row.credential_id,
        "external_ref": row.external_ref,
        "created_at": row.created_at,
    }
