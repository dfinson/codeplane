"""Persistence for job-scoped read-only share credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from backend.models.db import ShareTokenRow
from backend.persistence.database import serialized_write

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ShareTokenRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, token_hash: str, job_id: str, created_at: datetime, expires_at: datetime) -> None:
        async with serialized_write(self._session_factory) as session:
            await session.execute(delete(ShareTokenRow).where(ShareTokenRow.expires_at <= created_at))
            session.add(
                ShareTokenRow(
                    token_hash=token_hash,
                    job_id=job_id,
                    created_at=created_at,
                    expires_at=expires_at,
                )
            )

    async def get_valid_job(self, token_hash: str, now: datetime) -> str | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ShareTokenRow.job_id).where(
                    ShareTokenRow.token_hash == token_hash,
                    ShareTokenRow.expires_at > now,
                )
            )
            return result.scalar_one_or_none()

    async def revoke(self, token_hash: str, job_id: str) -> bool:
        async with serialized_write(self._session_factory) as session:
            result = await session.execute(
                delete(ShareTokenRow)
                .where(ShareTokenRow.token_hash == token_hash, ShareTokenRow.job_id == job_id)
                .returning(ShareTokenRow.job_id)
            )
            return result.scalar_one_or_none() is not None
