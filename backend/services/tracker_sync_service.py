"""Scheduled and manual tracker synchronization (Story 3.3, AD-7)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from backend.persistence.credential_repo import CredentialRepository
from backend.persistence.tracker_summary_repo import TrackerSummaryRepository
from backend.services.tracker_adapter import (
    TrackerAdapterError,
    TrackerAdapterInterface,
    TrackerReferenceError,
    build_tracker_adapters,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger()
TRACKER_POLL_INTERVAL_SECONDS = 60.0


class TrackerLinkNotFoundError(Exception):
    """Raised when a TrackerLink is not part of the requested Project."""


class TrackerSyncError(Exception):
    """Raised when a requested TrackerLink cannot be synchronized."""


class TrackerLinkValidationError(TrackerSyncError):
    """Raised when a credential or external reference is invalid before attachment."""


class TrackerSyncService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        adapters: dict[str, TrackerAdapterInterface] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._client: httpx.AsyncClient | None = None
        if adapters is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=5.0))
            adapters = build_tracker_adapters(self._client)
        self._adapters = adapters
        self._wake = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._link_locks: dict[str, asyncio.Lock] = {}

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._poll_loop(), name="tracker-sync")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            await self._task
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def refresh_all(self) -> None:
        async with self._session_factory() as session:
            targets = await TrackerSummaryRepository(session).list_targets()
        for target in targets:
            try:
                await self.refresh_link(
                    project_id=target["project_id"],
                    link_id=target["link_id"],
                )
            except TrackerSyncError:
                log.warning(
                    "tracker_sync.link_failed",
                    tracker_link_id=target["link_id"],
                    project_id=target["project_id"],
                )

    async def test_link(self, *, credential_id: str, external_ref: str) -> None:
        """Validate provider-specific reference syntax and connectivity before save."""
        async with self._session_factory() as session:
            credential = await CredentialRepository(session).get(credential_id)
            token = await CredentialRepository(session).resolve_secret(credential_id)
        if credential is None or token is None:
            raise TrackerSyncError(f"Credential '{credential_id}' could not be resolved")
        adapter = self._adapters.get(credential["provider"])
        if adapter is None:
            raise TrackerLinkValidationError(f"Unsupported tracker provider: {credential['provider']}")
        try:
            await adapter.test_connection(
                base_url=credential["base_url"],
                external_ref=external_ref,
                token=token,
                email=credential["email"],
            )
        except TrackerReferenceError as exc:
            raise TrackerLinkValidationError(str(exc)) from exc
        except TrackerAdapterError as exc:
            raise TrackerSyncError(str(exc)) from exc
        except Exception as exc:
            raise TrackerSyncError("Tracker provider request failed") from exc

    async def refresh_link(self, *, project_id: str, link_id: str) -> dict[str, Any]:
        lock = self._link_locks.setdefault(link_id, asyncio.Lock())
        async with lock:
            async with self._session_factory() as session:
                target = await TrackerSummaryRepository(session).get_target(
                    project_id=project_id,
                    link_id=link_id,
                )
                if target is None:
                    raise TrackerLinkNotFoundError(f"TrackerLink '{link_id}' does not exist in Project '{project_id}'")
                token = await CredentialRepository(session).resolve_secret(target["credential_id"])

            adapter = self._adapters.get(target["provider"])
            if token is None:
                return await self._fail(link_id, "Tracker credential could not be resolved")
            if adapter is None:
                return await self._fail(link_id, f"Unsupported tracker provider: {target['provider']}")

            try:
                tickets = await adapter.fetch_tickets(
                    base_url=target["base_url"],
                    external_ref=target["external_ref"],
                    token=token,
                    email=target["email"],
                )
            except TrackerAdapterError as exc:
                return await self._fail(link_id, str(exc))
            except Exception as exc:
                log.exception(
                    "tracker_sync.unexpected_provider_error",
                    tracker_link_id=link_id,
                    provider=target["provider"],
                )
                return await self._fail(link_id, "Tracker provider request failed", cause=exc)

            async with self._session_factory() as session:
                summary = await TrackerSummaryRepository(session).record_success(link_id, tickets)
                await session.commit()
            log.info(
                "tracker_sync.completed",
                tracker_link_id=link_id,
                project_id=project_id,
                provider=target["provider"],
                ticket_count=len(tickets),
            )
            return summary

    async def _fail(
        self,
        link_id: str,
        message: str,
        *,
        cause: Exception | None = None,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            await TrackerSummaryRepository(session).record_error(link_id, message)
            await session.commit()
        error = TrackerSyncError(message)
        if cause is not None:
            raise error from cause
        raise error

    async def _poll_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=TRACKER_POLL_INTERVAL_SECONDS,
                )
            except TimeoutError:
                if self._stopping:
                    break
                await self.refresh_all()
            else:
                self._wake.clear()
