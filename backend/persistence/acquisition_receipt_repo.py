"""Bounded read views over existing atomic acquisition receipts, not a second ledger."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from backend.models.domain import StateConflictError
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_RECEIPTS = """
FROM events AS event, json_each(event.raw_event, '$.acquisition.state.records') AS record
WHERE event.job_id = :job_id
  AND event.kind = 'integration.batch.accepted'
  AND json_extract(event.event_metadata, '$.integration_binding_id') = :binding_id
  AND json_extract(event.event_metadata, '$.source_framework') IS NULL
  AND json_extract(event.raw_event, '$.contract') = 'codeplane-source-batch-v1'
  AND json_extract(event.raw_event, '$.source_key') = :source_key
  AND json_extract(event.raw_event, '$.acquisition.source_identity_sha256') = :source_identity
  AND json_extract(event.raw_event, '$.acquisition.state.contract')
      IN ('native-acquisition-records-v1', 'native-acquisition-records-v2')
"""


class AcquisitionReceiptRepository(BaseRepository):
    def __init__(
        self, session: AsyncSession, *, job_id: str, binding_id: str, source_key: str, source_identity: str
    ) -> None:
        super().__init__(session)
        self._scope = {
            "job_id": job_id,
            "binding_id": binding_id,
            "source_key": source_key,
            "source_identity": source_identity,
        }

    async def identity_count(self) -> int:
        result = await self._session.execute(
            text("SELECT COUNT(DISTINCT json_extract(record.value, '$.identity')) " + _RECEIPTS), self._scope
        )
        return int(result.scalar_one())

    async def lookup(self, identities: list[str]) -> dict[str, dict[str, Any]]:
        """Return only requested page identities, including old cumulative v1 receipts."""
        if not identities:
            return {}
        if len(identities) > 1000:
            raise ValueError("Acquisition receipt lookup exceeds one bounded page")
        query = (
            """
SELECT DISTINCT json_extract(record.value, '$.identity') AS identity,
                json_extract(record.value, '$.sequence') AS sequence,
                json_extract(record.value, '$.sha256') AS sha256
"""
            + _RECEIPTS
            + """
  AND json_extract(record.value, '$.identity') IN (SELECT value FROM json_each(:identities))
"""
        )
        result = await self._session.stream(
            text(query), {**self._scope, "identities": json.dumps(identities)}, execution_options={"yield_per": 64}
        )
        found: dict[str, dict[str, Any]] = {}
        try:
            async for row in result:
                identity, sequence, digest = row
                if not isinstance(identity, str) or not isinstance(digest, str):
                    raise StateConflictError("Acquisition receipt identity history is malformed")
                item = {"identity": identity, "sequence": sequence, "sha256": digest}
                if identity in found and found[identity] != item:
                    raise StateConflictError("Acquisition receipt identity history is inconsistent")
                found[identity] = item
        finally:
            await result.close()
        return found
