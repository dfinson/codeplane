"""Project-scoped identity evidence, never an execution or observation Job."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from backend.models.db import IntegrationIdentityRow
from backend.models.domain import StateConflictError
from backend.persistence.repository import BaseRepository


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    id: str
    project_id: str
    enabled: bool
    config: dict[str, Any]


class IdentityRepository(BaseRepository):
    @staticmethod
    def _record(row: IntegrationIdentityRow) -> IdentityRecord:
        if hashlib.sha256(row.document_json.encode()).hexdigest() != row.document_sha256:
            raise StateConflictError("Identity document integrity requires reconciliation")
        document = json.loads(row.document_json)
        if document.get("schema") != "codeplane-identity-v1":
            raise StateConflictError("Identity document schema requires reconciliation")
        return IdentityRecord(row.id, row.project_id, document["enabled"], document["config"])

    async def put(self, binding_id: str, project_id: str, config: dict[str, Any]) -> IdentityRecord:
        row = await self._session.get(IntegrationIdentityRow, binding_id)
        now = datetime.now(UTC)
        document = json.dumps(
            {"schema": "codeplane-identity-v1", "enabled": True, "config": config},
            sort_keys=True,
            allow_nan=False,
        )
        fingerprint = hashlib.sha256(base64.b64decode(config["public_key"], validate=True)).hexdigest()
        if row is not None:
            if row.project_id != project_id or row.adapter_id != "ai-card":
                raise StateConflictError("Identity binding belongs to another owner")
            previous = self._record(row).config
            if previous["expected_did"] != config["expected_did"] or previous["public_key"] != config["public_key"]:
                raise StateConflictError("Identity key changes require a new explicit binding")
        else:
            row = IntegrationIdentityRow(id=binding_id, project_id=project_id, adapter_id="ai-card")
            self._session.add(row)
        row.document_json = document
        row.document_sha256 = hashlib.sha256(document.encode()).hexdigest()
        row.pinned_key_fingerprint = fingerprint
        row.endpoint = config["services"][0][1] if config["services"] else ""
        row.expires_at = datetime.fromisoformat(config["card"]["expires_at"])
        row.verified_at = now
        await self._session.flush()
        return self._record(row)

    async def get(self, binding_id: str, project_id: str) -> IdentityRecord | None:
        row = await self._session.get(IntegrationIdentityRow, binding_id)
        if row is None:
            return None
        if row.project_id != project_id or row.adapter_id != "ai-card":
            raise StateConflictError("Identity binding belongs to another owner")
        return self._record(row)

    async def list_for_project(self, project_id: str) -> tuple[IdentityRecord, ...]:
        result = await self._session.execute(
            select(IntegrationIdentityRow)
            .where(
                IntegrationIdentityRow.project_id == project_id,
                IntegrationIdentityRow.adapter_id == "ai-card",
            )
            .order_by(IntegrationIdentityRow.id)
            .limit(100)
        )
        return tuple(record for row in result.scalars() if (record := self._record(row)).enabled)

    async def disable(self, binding_id: str, project_id: str) -> None:
        if await self.get(binding_id, project_id) is None:
            raise ValueError("Identity binding does not exist")
        row = await self._session.get(IntegrationIdentityRow, binding_id)
        assert row is not None
        document = json.loads(row.document_json)
        document["enabled"] = False
        row.document_json = json.dumps(document, sort_keys=True, allow_nan=False)
        row.document_sha256 = hashlib.sha256(row.document_json.encode()).hexdigest()
        await self._session.flush()
