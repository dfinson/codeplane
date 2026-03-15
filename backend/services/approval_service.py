"""Approval request persistence and routing."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import Approval

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.persistence.approval_repo import ApprovalRepository

log = structlog.get_logger()


class ApprovalNotFoundError(Exception):
    """Raised when an approval request is not found."""


class ApprovalAlreadyResolvedError(Exception):
    """Raised when attempting to resolve an already-resolved approval."""


class ApprovalService:
    """Persists approval requests and routes operator decisions to the adapter.

    Holds in-memory asyncio.Future objects keyed by approval_id so the
    runtime can await the operator's decision while the SDK blocks on
    its permission callback.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._pending_futures: dict[str, asyncio.Future[str]] = {}
        self._approval_to_job: dict[str, str] = {}  # approval_id → job_id
        self._trusted_jobs: set[str] = set()  # jobs with "approve all" active

    def _make_repo(self, session: AsyncSession) -> ApprovalRepository:
        from backend.persistence.approval_repo import ApprovalRepository

        return ApprovalRepository(session)

    async def create_request(
        self,
        job_id: str,
        description: str,
        proposed_action: str | None = None,
    ) -> Approval:
        """Persist a new approval request and create an in-memory future for it."""
        approval_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        approval = Approval(
            id=approval_id,
            job_id=job_id,
            description=description,
            proposed_action=proposed_action,
            requested_at=now,
        )
        async with self._session_factory() as session:
            repo = self._make_repo(session)
            await repo.create(approval)
            await session.commit()

        # Create a future the runtime can await
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_futures[approval_id] = future
        self._approval_to_job[approval_id] = job_id

        log.info(
            "approval_created",
            approval_id=approval_id,
            job_id=job_id,
        )
        return approval

    async def resolve(self, approval_id: str, resolution: str) -> Approval:
        """Resolve an approval and unblock the waiting runtime future."""
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            repo = self._make_repo(session)
            # Atomic update: only succeeds if resolution IS NULL
            updated = await repo.resolve(approval_id, resolution, now)
            if updated is None:
                # Either not found or already resolved — check which
                existing = await repo.get(approval_id)
                if existing is None:
                    raise ApprovalNotFoundError(f"Approval {approval_id} not found")
                raise ApprovalAlreadyResolvedError(f"Approval {approval_id} already resolved as {existing.resolution}")
            await session.commit()

        # Resolve the in-memory future so the runtime unblocks
        future = self._pending_futures.pop(approval_id, None)
        self._approval_to_job.pop(approval_id, None)
        if future is not None and not future.done():
            future.set_result(resolution)

        log.info(
            "approval_resolved",
            approval_id=approval_id,
            resolution=resolution,
        )
        return updated

    async def wait_for_resolution(self, approval_id: str) -> str:
        """Block until the operator resolves the approval. Returns resolution string."""
        future = self._pending_futures.get(approval_id)
        if future is None:
            raise ApprovalNotFoundError(f"No pending future for approval {approval_id}")
        return await future

    async def list_for_job(self, job_id: str) -> list[Approval]:
        """List all approvals for a job."""
        async with self._session_factory() as session:
            repo = self._make_repo(session)
            return await repo.list_for_job(job_id)

    async def list_pending(self, job_id: str | None = None) -> list[Approval]:
        """List unresolved approvals."""
        async with self._session_factory() as session:
            repo = self._make_repo(session)
            return await repo.list_pending(job_id)

    def cleanup_job(self, job_id: str) -> None:
        """Cancel any pending futures for a job (e.g. on job cancel/fail)."""
        self._trusted_jobs.discard(job_id)
        to_remove = [
            aid
            for aid, fut in self._pending_futures.items()
            if not fut.done() and self._approval_to_job.get(aid) == job_id
        ]
        for aid in to_remove:
            fut = self._pending_futures.pop(aid, None)
            self._approval_to_job.pop(aid, None)
            if fut is not None and not fut.done():
                fut.cancel()

    def is_trusted(self, job_id: str) -> bool:
        """Return True if the operator has approved all for this job."""
        return job_id in self._trusted_jobs

    async def trust_job(self, job_id: str) -> int:
        """Mark a job as trusted and approve all its pending requests.

        Returns the number of approvals that were auto-resolved.
        """
        self._trusted_jobs.add(job_id)

        # Resolve all pending futures for this job
        resolved_count = 0
        pending_ids = [
            aid
            for aid, jid in self._approval_to_job.items()
            if jid == job_id and aid in self._pending_futures and not self._pending_futures[aid].done()
        ]
        for aid in pending_ids:
            try:
                await self.resolve(aid, "approved")
                resolved_count += 1
            except (ApprovalNotFoundError, ApprovalAlreadyResolvedError):
                pass

        log.info("job_trusted", job_id=job_id, resolved=resolved_count)
        return resolved_count
