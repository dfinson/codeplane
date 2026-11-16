"""Binding-owned observation registration; no execution or SDK construction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from backend.models.db import IntegrationBindingRow, IntegrationProjectionCheckpointRow, IntegrationSourceCheckpointRow
from backend.models.domain import StateConflictError
from backend.persistence.event_repo import EventRepository
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from backend.models.events import SessionEvent


@dataclass(frozen=True, slots=True)
class IntegrationBinding:
    id: str
    project_id: str
    job_id: str | None
    adapter_id: str
    mode: str
    enabled: bool
    config: dict[str, Any]
    version: int


@dataclass(frozen=True, slots=True)
class SourceCheckpoint:
    record_count: int
    prefix_sha256: str
    canonical_sha256: str
    parser_sha256: str
    buffer: dict[str, Any]
    state: str
    version: int


@dataclass(frozen=True, slots=True)
class ProjectionCheckpoint:
    parser_revision: str
    parser_sha256: str
    canonical_sha256: str
    buffer: dict[str, Any]
    state: str
    version: int


class IntegrationRepository(BaseRepository):
    @staticmethod
    def _binding(row: IntegrationBindingRow) -> IntegrationBinding:
        return IntegrationBinding(
            row.id,
            row.project_id,
            row.job_id,
            row.adapter_id,
            row.mode,
            row.enabled,
            json.loads(row.config_json),
            row.version,
        )

    async def get(self, binding_id: str) -> IntegrationBinding | None:
        row = await self._session.get(IntegrationBindingRow, binding_id)
        return self._binding(row) if row is not None else None

    async def create_observation(
        self, binding_id: str, project_id: str, adapter_id: str, config: dict[str, Any]
    ) -> tuple[IntegrationBinding, bool]:
        now = datetime.now(UTC)
        result = await self._session.execute(
            insert(IntegrationBindingRow).values(
                id=binding_id, project_id=project_id, adapter_id=adapter_id, mode="observation",
                enabled=False, config_json=json.dumps(config, sort_keys=True), created_at=now, updated_at=now,
            ).on_conflict_do_nothing(index_elements=["id"]).returning(IntegrationBindingRow.id)
        )
        created = result.scalar_one_or_none() is not None
        binding = await self.get(binding_id)
        if binding is None:
            raise RuntimeError("Integration binding was not stored")
        return binding, created

    async def attach_job(self, binding_id: str, job_id: str) -> None:
        row = await self._session.get(IntegrationBindingRow, binding_id)
        if row is None:
            raise ValueError("Integration binding does not exist")
        if row.job_id is not None and row.job_id != job_id:
            raise ValueError("A source binding cannot be reassigned to another Job")
        row.job_id = job_id
        row.version += 1
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def list_for_project(self, project_id: str) -> list[IntegrationBinding]:
        result = await self._session.execute(
            select(IntegrationBindingRow).where(IntegrationBindingRow.project_id == project_id).limit(100)
        )
        return [self._binding(row) for row in result.scalars()]

    async def checkpoint(self, binding_id: str, source_key: str, revision: str) -> SourceCheckpoint | None:
        row = await self._session.get(IntegrationSourceCheckpointRow, (binding_id, source_key, revision))
        if row is None:
            return None
        return SourceCheckpoint(
            row.record_count, row.prefix_sha256, row.canonical_sha256, row.parser_sha256,
            json.loads(row.buffer_json), row.state, row.version,
        )

    async def has_checkpoint(self, binding_id: str, source_key: str) -> bool:
        result = await self._session.execute(
            select(IntegrationSourceCheckpointRow.binding_id).where(
                IntegrationSourceCheckpointRow.binding_id == binding_id,
                IntegrationSourceCheckpointRow.source_key == source_key,
            ).limit(1)
        )
        return result.first() is not None

    async def accepted_batch(self, receipt_id: str) -> SessionEvent | None:
        return await EventRepository(self._session).get(receipt_id)

    async def projection(self, binding_id: str) -> ProjectionCheckpoint | None:
        row = await self._session.get(IntegrationProjectionCheckpointRow, binding_id)
        if row is None:
            return None
        return ProjectionCheckpoint(
            row.parser_revision, row.parser_sha256, row.canonical_sha256,
            json.loads(row.buffer_json), row.state, row.version,
        )

    async def has_binding_checkpoints(self, binding_id: str) -> bool:
        result = await self._session.execute(
            select(IntegrationSourceCheckpointRow.binding_id).where(
                IntegrationSourceCheckpointRow.binding_id == binding_id,
            ).limit(1)
        )
        return result.first() is not None

    async def require_projection_reconciliation(
        self, binding_id: str, reason: str, *, expected_version: int,
    ) -> None:
        try:
            await self._fence_projection(binding_id, reason, expected_version=expected_version)
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise

    async def _fence_projection(
        self, binding_id: str, reason: str, *, expected_version: int,
    ) -> None:
        result = await self._session.execute(
            update(IntegrationProjectionCheckpointRow).where(
                IntegrationProjectionCheckpointRow.binding_id == binding_id,
                IntegrationProjectionCheckpointRow.version == expected_version,
                IntegrationProjectionCheckpointRow.state == "accepted",
            ).values(
                state="reconciliation_required", error_code=reason, updated_at=datetime.now(UTC),
                version=expected_version + 1,
            ).returning(IntegrationProjectionCheckpointRow.version)
        )
        if result.scalar_one_or_none() is None:
            raise StateConflictError("Binding projection changed concurrently")

    async def require_reconciliation(
        self, binding_id: str, source_key: str, revision: str, reason: str, *, expected_version: int,
        projection_version: int | None = None,
    ) -> None:
        try:
            result = await self._session.execute(
                update(IntegrationSourceCheckpointRow).where(
                    IntegrationSourceCheckpointRow.binding_id == binding_id,
                    IntegrationSourceCheckpointRow.source_key == source_key,
                    IntegrationSourceCheckpointRow.parser_revision == revision,
                    IntegrationSourceCheckpointRow.version == expected_version,
                    IntegrationSourceCheckpointRow.state == "accepted",
                ).values(
                    state="reconciliation_required", error_code=reason, updated_at=datetime.now(UTC),
                    version=expected_version + 1,
                ).returning(IntegrationSourceCheckpointRow.version)
            )
            if result.scalar_one_or_none() is None:
                raise StateConflictError("Source checkpoint changed concurrently")
            if projection_version is not None:
                await self._fence_projection(binding_id, reason, expected_version=projection_version)
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise

    async def commit_records(
        self, *, binding_id: str, source_key: str, revision: str, expected_version: int,
        record_count: int, prefix_sha256: str, canonical_sha256: str, parser_sha256: str,
        buffer: dict[str, Any], events: list[SessionEvent],
        projection_version: int, projection_parser_sha256: str,
        projection_canonical_sha256: str, projection_buffer: dict[str, Any],
    ) -> int:
        encoded_projection = json.dumps(projection_buffer, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if len(encoded_projection.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("Binding projection exceeds its 8 MiB bound")
        values = {
            "record_count": record_count, "prefix_sha256": prefix_sha256,
            "canonical_sha256": canonical_sha256, "parser_sha256": parser_sha256,
            "buffer_json": json.dumps(buffer, sort_keys=True), "state": "accepted", "error_code": None,
            "version": expected_version + 1, "updated_at": datetime.now(UTC),
        }
        try:
            projection_values = {
                "parser_revision": revision, "parser_sha256": projection_parser_sha256,
                "canonical_sha256": projection_canonical_sha256, "buffer_json": encoded_projection,
                "state": "accepted", "error_code": None,
                "version": projection_version + 1, "updated_at": datetime.now(UTC),
            }
            if projection_version == 0:
                if await self.has_binding_checkpoints(binding_id):
                    raise StateConflictError("Existing source projections require explicit reconciliation")
                projection_result = await self._session.execute(
                    insert(IntegrationProjectionCheckpointRow).values(
                        binding_id=binding_id, **projection_values,
                    ).on_conflict_do_nothing().returning(IntegrationProjectionCheckpointRow.version)
                )
            else:
                projection_result = await self._session.execute(
                    update(IntegrationProjectionCheckpointRow).where(
                        IntegrationProjectionCheckpointRow.binding_id == binding_id,
                        IntegrationProjectionCheckpointRow.version == projection_version,
                        IntegrationProjectionCheckpointRow.state == "accepted",
                    ).values(**projection_values).returning(IntegrationProjectionCheckpointRow.version)
                )
            if projection_result.scalar_one_or_none() is None:
                raise StateConflictError("Binding projection changed concurrently")
            if expected_version == 0:
                result = await self._session.execute(
                    insert(IntegrationSourceCheckpointRow).values(
                        binding_id=binding_id, source_key=source_key, parser_revision=revision, **values,
                    ).on_conflict_do_nothing().returning(IntegrationSourceCheckpointRow.version)
                )
            else:
                result = await self._session.execute(
                    update(IntegrationSourceCheckpointRow).where(
                        IntegrationSourceCheckpointRow.binding_id == binding_id,
                        IntegrationSourceCheckpointRow.source_key == source_key,
                        IntegrationSourceCheckpointRow.parser_revision == revision,
                        IntegrationSourceCheckpointRow.version == expected_version,
                        IntegrationSourceCheckpointRow.state == "accepted",
                    ).values(**values).returning(IntegrationSourceCheckpointRow.version)
                )
            accepted = result.scalar_one_or_none()
            if accepted is None:
                raise StateConflictError("Source checkpoint changed concurrently")
            event_repo = EventRepository(self._session)
            for event in events:
                await event_repo.append(event)
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise
        return int(accepted)
