"""Durable peer delivery receipts. Unknown attempts are never replayed."""

from sqlalchemy import or_, select, update

from backend.models.api_schemas import PeerMessageResponse
from backend.models.db import PeerMessageRow
from backend.persistence.repository import BaseRepository


def _response(row: PeerMessageRow) -> PeerMessageResponse:
    return PeerMessageResponse.model_validate(row, from_attributes=True)


class PeerMessageRepository(BaseRepository):
    async def get(self, message_id: str) -> PeerMessageResponse | None:
        row = await self._session.get(PeerMessageRow, message_id)
        return _response(row) if row else None

    async def create(self, receipt: PeerMessageResponse) -> None:
        self._session.add(PeerMessageRow(**receipt.model_dump()))
        await self._session.flush()

    async def finish(self, receipt: PeerMessageResponse) -> None:
        await self._session.execute(
            update(PeerMessageRow)
            .where(PeerMessageRow.message_id == receipt.message_id)
            .values(status=receipt.status, detail=receipt.detail)
        )

    async def for_job(self, job_id: str, offset: int = 0, limit: int = 51) -> list[PeerMessageResponse]:
        rows = await self._session.execute(
            select(PeerMessageRow)
            .where(or_(PeerMessageRow.sender_job_id == job_id, PeerMessageRow.recipient_job_id == job_id))
            .order_by(PeerMessageRow.created_at.desc(), PeerMessageRow.message_id)
            .offset(offset)
            .limit(limit)
        )
        return [_response(row) for row in rows.scalars()]
