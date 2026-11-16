"""Binding-owned observation registration; no execution or SDK construction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from backend.models.db import IntegrationBindingRow
from backend.persistence.repository import BaseRepository


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
