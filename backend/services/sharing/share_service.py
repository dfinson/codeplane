"""Durable, revocable share tokens for read-only job access."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import CodePlaneError
from backend.persistence.share_token_repo import ShareTokenRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger()

DEFAULT_TTL_SECONDS = 24 * 60 * 60


class InvalidShareTokenError(CodePlaneError):
    """Raised when a share token is unknown, expired or revoked."""


@dataclass(frozen=True)
class ShareToken:
    token: str = field(repr=False)
    job_id: str
    created_at: datetime
    expires_at: datetime


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ShareService:
    """Persist credentials before returning links; validate without a process cache."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ttl: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._repo = ShareTokenRepository(session_factory)
        self._ttl = ttl

    async def create_token(self, job_id: str) -> ShareToken:
        now = datetime.now(UTC)
        entry = ShareToken(
            token=secrets.token_urlsafe(32),
            job_id=job_id,
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl),
        )
        await self._repo.create(_token_hash(entry.token), job_id, entry.created_at, entry.expires_at)
        log.info("share_token_created", job_id=job_id)
        return entry

    async def validate(self, token: str) -> str:
        """Return the job ID only while the persisted credential remains valid."""
        job_id = await self._repo.get_valid_job(_token_hash(token), datetime.now(UTC))
        if job_id is None:
            raise InvalidShareTokenError("Invalid or expired share link")
        return job_id

    async def revoke(self, token: str, job_id: str) -> bool:
        """Delete only the named job's token; repeated revocation is harmless."""
        revoked = await self._repo.revoke(_token_hash(token), job_id)
        if revoked:
            log.info("share_token_revoked", job_id=job_id)
        return revoked
