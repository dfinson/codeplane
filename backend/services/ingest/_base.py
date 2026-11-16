"""Shared scaffolding for imported CLI ingestion sources."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, Any

import structlog
from traceforge.adapters.mapped_json import MappedJsonAdapter
from traceforge.config.mappings import resolve_mapping_path
from traceforge.sources.file_watch import FileWatchSource

from backend.models.domain import JobState
from backend.models.events import EventKind, new_event

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from traceforge.types import SessionEvent

    from backend.services.events.event_processor import EventProcessor
    from backend.services.runtime import RuntimeService

log = structlog.get_logger()

_PERSIST_EVERY_EVENTS = 64


def repo_basename(repo_path: str) -> str:
    """Return the last path segment for POSIX or Windows-style path strings."""
    return PureWindowsPath(repo_path.rstrip("/\\")).name


class TraceForgeIngestBase:
    """Common task, event-count resume, prompt, and lifecycle helpers."""

    _watcher_log_prefix = "ingest"

    def __init__(
        self,
        event_processor: EventProcessor,
        runtime_service: RuntimeService,
        session_factory: async_sessionmaker[AsyncSession],
        mapping_name: str,
    ) -> None:
        self._event_processor = event_processor
        self._runtime = runtime_service
        self._session_factory = session_factory
        self._mapping_name = mapping_name
        self._tracked_sessions: set[str] = set()
        self._session_to_job: dict[str, str] = {}
        self._tail_tasks: dict[str, asyncio.Task[Any]] = {}
        self._liveness_tasks: dict[str, asyncio.Task[Any]] = {}
        self._discovery_task: asyncio.Task[Any] | None = None
        self._running = False
        self._job_worktrees: dict[str, str] = {}
        self._job_base_refs: dict[str, str] = {}
        self._prompt_captured: set[str] = set()
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._emitted_counts: dict[str, int] = {}
        self._finalized_jobs: set[str] = set()

    async def _stop_common(self) -> None:
        self._running = False
        if self._discovery_task:
            self._discovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._discovery_task
            self._discovery_task = None
        for task in [*self._tail_tasks.values(), *self._liveness_tasks.values()]:
            task.cancel()
        tasks = [*self._tail_tasks.values(), *self._liveness_tasks.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tail_tasks.clear()
        self._liveness_tasks.clear()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)

    def _fire_bg(self, coro: Coroutine[Any, Any, Any], *, name: str) -> None:
        task: asyncio.Task[Any] = asyncio.create_task(coro, name=name)
        self._bg_tasks.add(task)
        task.add_done_callback(lambda done: self._bg_tasks.discard(done))

    def _make_tail_pop_callback(self, sid: str) -> Callable[[object], None]:
        def _cb(_task: object) -> None:
            self._tail_tasks.pop(sid, None)

        return _cb

    def _make_liveness_pop_callback(self, sid: str) -> Callable[[object], None]:
        def _cb(_task: object) -> None:
            self._liveness_tasks.pop(sid, None)

        return _cb

    async def _tail_traceforge_events(
        self,
        session_id: str,
        job_id: str,
        jsonl_path: Path,
        *,
        initial_skip_count: int = 0,
        finalize_on_raw: Callable[[dict[str, Any]], tuple[bool, str | None]] | None = None,
    ) -> None:
        """Tail JSONL from the beginning and skip already-emitted TF events on reattach."""
        adapter = MappedJsonAdapter.from_yaml(str(resolve_mapping_path(self._mapping_name)), session_id=session_id)
        skipped = 0
        emitted = initial_skip_count
        self._emitted_counts[job_id] = emitted
        terminal_reason: str | None = None
        terminal_seen = False
        try:
            async with FileWatchSource(jsonl_path, name=session_id, start_at="beginning") as source:
                async for record in source:
                    raw = self._load_json_payload(record.payload)
                    if raw is None:
                        continue
                    if finalize_on_raw is not None:
                        terminal_seen, terminal_reason = finalize_on_raw(raw)
                    for event in adapter.parse_dict(raw):
                        if skipped < initial_skip_count:
                            skipped += 1
                            continue
                        await self._handle_tf_event(job_id, event)
                        emitted += 1
                        self._emitted_counts[job_id] = emitted
                        if emitted % _PERSIST_EVERY_EVENTS == 0:
                            self._fire_bg(
                                self._persist_emitted_count(job_id, emitted),
                                name=f"{self._mapping_name}-count-{job_id[:8]}",
                            )
                        if event.kind == EventKind.session_error:
                            terminal_seen = True
                            terminal_reason = self._event_error_reason(event)
                        elif event.kind == EventKind.session_ended:
                            terminal_seen = True
                    if terminal_seen:
                        await self._persist_emitted_count(job_id, emitted)
                        await self._finalize_session(job_id, error_reason=terminal_reason)
                        return
        except asyncio.CancelledError:
            await self._persist_emitted_count(job_id, self._emitted_counts.get(job_id, emitted))
            raise
        except Exception:
            log.debug("%s_tail_error", self._watcher_log_prefix, job_id=job_id, exc_info=True)

    @staticmethod
    def _load_json_payload(payload: str | bytes) -> dict[str, Any] | None:
        try:
            raw = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.debug("ingest_invalid_json")
            return None
        return raw if isinstance(raw, dict) else None

    async def _handle_tf_event(self, job_id: str, event: SessionEvent) -> None:
        if event.kind == EventKind.message_user:
            content = str(event.payload.get("content") or event.payload.get("text") or "")
            if job_id not in self._prompt_captured and content.strip():
                self._prompt_captured.add(job_id)
                self._fire_bg(self._set_job_prompt(job_id, content), name=f"{self._mapping_name}-prompt-{job_id[:8]}")
        await self._event_processor.process_event(
            job_id,
            event,
            self._job_worktrees.get(job_id),
            self._job_base_refs.get(job_id),
        )

    @staticmethod
    def _event_error_reason(event: SessionEvent) -> str:
        return str(event.payload.get("message") or event.payload.get("error") or "session error")

    async def _persist_emitted_count(self, job_id: str, count: int) -> None:
        try:
            from backend.persistence.database import serialized_write
            from backend.persistence.job_repo import JobRepository

            async with serialized_write(self._session_factory) as session:
                await JobRepository(session).update_tail_offset(job_id, count)
        except Exception:
            log.debug("ingest_count_persist_failed", job_id=job_id, exc_info=True)

    async def _set_job_prompt(self, job_id: str, content: str) -> None:
        try:
            from backend.persistence.database import serialized_write
            from backend.persistence.job_repo import JobRepository

            async with serialized_write(self._session_factory) as session:
                await JobRepository(session).update_prompt(job_id, content)
        except Exception:
            log.debug("ingest_prompt_update_failed", job_id=job_id, exc_info=True)

    async def _publish_lifecycle(self, job_id: str, kind: EventKind, payload: dict[str, Any]) -> None:
        await self._event_processor.process_event(job_id, new_event(job_id, kind, payload))

    async def _finalize_session(self, job_id: str, *, error_reason: str | None = None) -> None:
        if job_id in self._finalized_jobs:
            return
        self._finalized_jobs.add(job_id)
        now = datetime.now(UTC)
        new_state = JobState.failed if error_reason else JobState.review
        try:
            from backend.persistence.database import serialized_write
            from backend.persistence.job_repo import JobRepository
            from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

            async with serialized_write(self._session_factory) as session:
                repo = JobRepository(session)
                job = await repo.get(job_id)
                await repo.update_state(
                    job_id,
                    new_state,
                    updated_at=now,
                    completed_at=now,
                    failure_reason=error_reason,
                )
                duration_ms = 0
                if job and job.created_at:
                    created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=UTC)
                    duration_ms = max(int((now - created).total_seconds() * 1000), 0)
                await TelemetrySummaryRepository(session).finalize(
                    job_id,
                    status=str(new_state),
                    duration_ms=duration_ms,
                )
        except Exception:
            log.warning("%s_finalize_failed", self._watcher_log_prefix, job_id=job_id, exc_info=True)
            return

        await self._publish_lifecycle(job_id, EventKind.job_state_changed, {"state": new_state, "new_state": new_state})
        if new_state == JobState.review:
            await self._publish_lifecycle(job_id, EventKind.job_review, {"resolution": "unresolved"})
        await self._runtime.finalize_external_session(
            job_id,
            worktree_path=self._job_worktrees.get(job_id),
            base_ref=self._job_base_refs.get(job_id),
            error_reason=error_reason,
        )
        self._cleanup_job(job_id)

    def _cleanup_job(self, job_id: str) -> None:
        self._job_worktrees.pop(job_id, None)
        self._job_base_refs.pop(job_id, None)
        self._prompt_captured.discard(job_id)
        self._emitted_counts.pop(job_id, None)
        self._event_processor.cleanup(job_id)
