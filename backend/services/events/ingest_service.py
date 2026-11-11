"""IngestService — operator messaging bridge for imported CLI sessions.

Routes operator messages and abort commands to the appropriate channel:
- claude_cli → ClaudeSessionStateWatcher (pending message queue for Stop hook)
- copilot_cli → SessionStateWatcher (Copilot steer API)

Session data ingestion for both SDKs is handled entirely by their respective
SessionStateWatcher implementations via file-tailing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from backend.config import load_config
from backend.models.domain import Job, JobSource
from backend.services.adapters.integration_policy import IntegrationPolicy
from backend.services.completers.copilot_steer import NativeDeliveryError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.services.completers.copilot_steer import CopilotSteerClient
    from backend.services.ingest.claude_source import ClaudeSessionStateWatcher
    from backend.services.ingest.copilot_source import SessionStateWatcher

log = structlog.get_logger()


class IngestService:
    """Routes operator messages to the appropriate session watcher."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        steer_client: CopilotSteerClient | None = None,
        claude_watcher: ClaudeSessionStateWatcher | None = None,
        session_state_watcher: SessionStateWatcher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._steer = steer_client
        self._claude_watcher = claude_watcher
        self._session_state_watcher = session_state_watcher

    async def send_operator_message(self, job_id: str, message: str) -> None:
        """Queue an operator message for delivery to the agent."""
        job = await self._get_job(job_id)
        if not job:
            raise NativeDeliveryError(f"Job {job_id} does not exist")
        IntegrationPolicy(load_config()).require_enabled(job.sdk)
        if not job.external_session_id:
            raise NativeDeliveryError("Native session identity is missing")

        if job.source == JobSource.claude_cli:
            if self._claude_watcher:
                await self._claude_watcher.send_operator_message(job_id, message)
            else:
                log.warning("operator_message_no_claude_watcher", job_id=job_id)
                raise NativeDeliveryError("Claude observation is disabled")
        elif job.source == JobSource.copilot_cli:
            if self._session_state_watcher:
                ext_id = job.external_session_id
                if ext_id:
                    await self._session_state_watcher.send_message(ext_id, message)
                    log.info("operator_message_sent_copilot", job_id=job_id)
            elif self._steer:
                ext_id = job.external_session_id
                if ext_id:
                    await self._steer.send_message(ext_id, message)
                    log.info("operator_message_sent_copilot", job_id=job_id)
            else:
                raise NativeDeliveryError("Copilot steering is unavailable")
        else:
            log.warning("operator_message_no_channel", job_id=job_id, source=job.source)
            raise NativeDeliveryError("No native message channel for this job")

    async def abort_session(self, job_id: str) -> None:
        """Abort the external session.

        The caller (cancel_job endpoint) already transitions job state to
        canceled. This method only handles external agent communication.
        """
        job = await self._get_job(job_id)
        if not job:
            raise NativeDeliveryError(f"Job {job_id} does not exist")
        IntegrationPolicy(load_config()).require_enabled(job.sdk)
        if not job.external_session_id:
            raise NativeDeliveryError("Native session identity is missing")

        if job.source == JobSource.claude_cli:
            if self._claude_watcher:
                await self._claude_watcher.abort_session(job_id)
            else:
                raise NativeDeliveryError("Claude observation is disabled")
        elif job.source == JobSource.copilot_cli:
            if self._session_state_watcher:
                ext_id = job.external_session_id
                if ext_id:
                    await self._session_state_watcher.abort_session(ext_id)
            elif self._steer:
                ext_id = job.external_session_id
                if ext_id:
                    await self._steer.abort(ext_id)
            else:
                raise NativeDeliveryError("Copilot steering is unavailable")
        else:
            raise NativeDeliveryError("No native cancellation channel for this job")

    async def confirms_live(self, job: Job) -> bool:
        if job.source != JobSource.copilot_cli or not job.external_session_id:
            return False
        if self._steer is None:
            return False
        return await self._steer.check_alive(job.external_session_id) is True

    async def _get_job(self, job_id: str) -> Job | None:
        async with self._session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            return await repo.get(job_id)
