"""Imported CLI observation, without changing native execution/control ownership.

Fresh files use the standalone decoder and version-2 acquisition checkpoints.
Version-1 checkpoints retain their explicit optional TraceForge implementation.
Both preserve native-v1 event IDs. record_count counts physical lines (including
blanks), decoded_event_count/tail_offset count decoder outputs, and
emitted_event_count counts persisted source events after pairing. Storage cursors
remain database-local and are never interpreted as any of those counts.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import uuid
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from backend.models.domain import JobState
from backend.models.events import TRANSCRIPT_KINDS, EventKind, new_event
from backend.services.ingest.jsonl_source import MAX_RECORD_BYTES, NativeJsonlSource
from backend.services.ingest.mapping import NativeMappingDecoder, resolve_mapping_path
from backend.services.ingest.otel_decoder import decode_json_body
from backend.services.runtime.execution_control import ExecutionControl

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.session_event import SessionEvent
    from backend.services.events.event_processor import EventProcessor
    from backend.services.runtime import RuntimeService

log = structlog.get_logger()


def _canonical_record(raw: dict[str, Any]) -> bytes:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _advance_prefix(prefix: bytes, canonical: bytes) -> bytes:
    return hashlib.sha256(prefix + len(canonical).to_bytes(8, "big") + canonical).digest()


class _NativeIdentity:
    """Fresh standalone source, retaining the published native-v1 identity seed."""

    def __init__(self, framework: str, external_id: str, job_id: str, anchor: datetime) -> None:
        path = resolve_mapping_path(framework)
        if path is None:
            raise ValueError(f"No native event mapping for framework {framework}")
        self.decoder = NativeMappingDecoder(
            path,
            job_id=job_id,
            external_session_id=external_id,
            anchor=anchor,
            framework_id=framework,
            legacy_identity=True,
        )
        self.mapping_digest = self.decoder.mapping_digest
        self.anchor = self.decoder.anchor

    def events(self, raw: dict[str, Any], ordinal: int) -> Iterator[SessionEvent]:
        for event in self.decoder.decode(raw, ordinal):
            yield event.model_copy(update={"raw_event": raw})


class _LegacyNativeIdentity:
    def __init__(self, framework: str, external_id: str, job_id: str, anchor: datetime) -> None:
        try:
            from traceforge.adapters.mapped_json import FrameworkMapping, MappedJsonAdapter
        except ModuleNotFoundError as exc:
            if exc.name != "traceforge":
                raise
            raise RuntimeError(
                "Legacy native checkpoint requires codeplane[legacy-traceforge]; "
                "standalone checkpoint conversion requires explicit reconciliation"
            ) from exc
        from traceforge.config.mappings import resolve_mapping_path
        from traceforge.preprocessors import get_preprocessor

        resolved_mapping = resolve_mapping_path(framework)
        if resolved_mapping is None:
            raise ValueError(f"No native event mapping for framework {framework}")
        mapping_path = Path(resolved_mapping)
        self.mapping = FrameworkMapping.model_validate(yaml.safe_load(mapping_path.read_text(encoding="utf-8")))
        self.adapter = MappedJsonAdapter(self.mapping, session_id=external_id)
        preprocessor = get_preprocessor(self.mapping.preprocessor) if self.mapping.preprocessor else None
        source = inspect.getsourcefile(preprocessor) if preprocessor else None
        identity = {
            "distribution_version": version("traceforge-toolkit"),
            "mapping_file_sha256": hashlib.sha256(mapping_path.read_bytes()).hexdigest(),
            "preprocessor_file_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest() if source else None,
        }
        self.mapping_digest = hashlib.sha256(_canonical_record(identity)).hexdigest()
        self.framework = framework
        self.external_id = external_id
        self.job_id = job_id
        self.anchor = anchor.replace(tzinfo=UTC) if anchor.tzinfo is None else anchor
        self.references: OrderedDict[str, str] = OrderedDict()
        self.window = self.mapping.get_motivation_config().source_window
        self.source_kinds = {kind for item in self.mapping.get_motivation_config().sources for kind in item.events}
        self.turn = 0

    def events(self, raw: dict[str, Any], ordinal: int) -> Iterator[SessionEvent]:
        digest = hashlib.sha256(_canonical_record(raw)).hexdigest()
        for index, event in enumerate(self.adapter.parse_dict(raw)):
            identity = json.dumps(
                ["codeplane-native-v1", self.framework, self.external_id, ordinal, index, digest],
                separators=(",", ":"),
            )
            stable_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
            if event.metadata.raw_kind in self.source_kinds:
                self.references[event.id] = stable_id
                while len(self.references) > self.window:
                    self.references.popitem(last=False)
            motivation = event.metadata.motivation
            if motivation:
                motivation = motivation.model_copy(
                    update={"source_event_ids": tuple(self.references[item] for item in motivation.source_event_ids)}
                )
            metadata = event.metadata.model_copy(update={"run_id": self.external_id, "motivation": motivation})
            payload = dict(event.payload)
            payload["_codeplane_source"] = {
                "contract": "codeplane-native-v1",
                "framework": self.framework,
                "session_id": self.external_id,
                "record_index": ordinal,
                "output_index": index,
                "record_sha256": digest,
                "vendor_event_id": raw.get("id"),
            }
            if not payload.get("turn_id"):
                payload["turn_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.job_id}:native-turn:{self.turn}"))
            if event.kind == EventKind.message_assistant:
                self.turn += 1
            timestamp_value: Any = raw
            for part in (self.mapping.timestamp_field or "").split("."):
                timestamp_value = timestamp_value.get(part) if isinstance(timestamp_value, dict) else None
            timestamp = (
                event.timestamp if timestamp_value is not None else self.anchor + timedelta(microseconds=ordinal)
            )
            from backend.models.session_event import SessionEvent

            yield SessionEvent.model_validate(
                event.model_copy(
                    update={
                        "id": stable_id,
                        "session_id": self.job_id,
                        "metadata": metadata,
                        "payload": payload,
                        "timestamp": timestamp,
                    }
                ).model_dump(mode="json")
            )


def _verify_source_prefix(path: Path, count: int, expected: str) -> None:
    prefix = bytes(32)
    seen = 0
    with path.open("rb") as source:
        while seen < count:
            line = source.readline(MAX_RECORD_BYTES + 2)
            if not line:
                break
            if len(line) > MAX_RECORD_BYTES:
                raise ValueError("Native source record exceeds 1 MiB")
            raw = decode_json_body(line) if line.strip() else None
            if line.strip() and not isinstance(raw, dict):
                raise RuntimeError("Native source contains a non-object JSON record")
            canonical = _canonical_record(raw) if isinstance(raw, dict) else b""
            prefix = _advance_prefix(prefix, canonical)
            seen += 1
    if seen != count or prefix.hex() != expected:
        raise RuntimeError("Native source was truncated or rewritten before its accepted checkpoint")


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
        self._source_locks: dict[str, asyncio.Lock] = {}
        self._finalization_control = ExecutionControl()

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
        """Validate prior progress, reconstruct state and durably accept new records."""
        lock = self._source_locks.setdefault(job_id, asyncio.Lock())
        if lock.locked():
            return
        try:
            async with lock:
                await self._tail_source(session_id, job_id, jsonl_path, initial_skip_count, finalize_on_raw)
        finally:
            self._source_locks.pop(job_id, None)

    async def _tail_source(
        self,
        session_id: str,
        job_id: str,
        jsonl_path: Path,
        initial_skip_count: int,
        finalize_on_raw: Callable[[dict[str, Any]], tuple[bool, str | None]] | None,
    ) -> None:
        from backend.persistence.database import serialized_write
        from backend.persistence.event_repo import EventRepository
        from backend.persistence.job_repo import JobRepository

        decoded = 0
        self._emitted_counts[job_id] = decoded
        terminal_reason: str | None = None
        terminal_seen = False
        try:
            async with self._session_factory() as session:
                repo = JobRepository(session)
                job = await repo.get(job_id)
                if job is None:
                    raise RuntimeError("Native job no longer exists")
                checkpoint = await repo.get_ingest_checkpoint(job_id)
                prior = await EventRepository(session).list_by_job(job_id, list(TRANSCRIPT_KINDS), limit=1)
            if job.external_session_id != session_id or job.source != f"{self._mapping_name}_cli":
                raise RuntimeError("Native source does not belong to this Job")
            revision = checkpoint.get("version") if checkpoint else 2
            if revision not in (1, 2):
                raise RuntimeError("Unsupported native checkpoint version requires reconciliation")
            normalizer = (
                _LegacyNativeIdentity(self._mapping_name, session_id, job_id, job.created_at)
                if revision == 1
                else _NativeIdentity(self._mapping_name, session_id, job_id, job.created_at)
            )
            accepted = int(checkpoint["record_count"]) if checkpoint else 0
            if checkpoint:
                if (
                    checkpoint.get("mapping_sha256") != normalizer.mapping_digest
                    or checkpoint.get("external_session_id") != session_id
                    or (revision == 2 and checkpoint.get("framework") != self._mapping_name)
                    or (revision == 2 and checkpoint.get("timestamp_anchor") != normalizer.anchor.isoformat())
                    or (revision == 2 and checkpoint.get("output_contract") != "codeplane-native-v1")
                ):
                    raise RuntimeError("Native checkpoint mapping or session identity changed")
                await asyncio.to_thread(_verify_source_prefix, jsonl_path, accepted, checkpoint["prefix_sha256"])
            elif initial_skip_count or job.tail_offset or prior:
                raise RuntimeError("Legacy native history has no provable source checkpoint")
            if checkpoint:
                offset = 0
                while True:
                    async with self._session_factory() as session:
                        history = await EventRepository(session).list_all_events_by_job(
                            job_id, limit=500, offset=offset
                        )
                    self._event_processor.restore_projection(job_id, history)
                    if len(history) < 500:
                        break
                    offset += len(history)
            prefix = bytes(32)
            ordinal = 0
            source_checkpoint = checkpoint["source"] if checkpoint and revision == 2 else None
            async with NativeJsonlSource(jsonl_path, checkpoint=source_checkpoint) as source:
                async for line in source:
                    reconstruct = ordinal < accepted
                    raw = decode_json_body(line) if line.strip() else None
                    if line.strip() and not isinstance(raw, dict):
                        raise RuntimeError("Native source contains a non-object JSON record")
                    if raw is not None:
                        if finalize_on_raw is not None:
                            terminal_seen, terminal_reason = finalize_on_raw(raw)
                        events = tuple(normalizer.events(raw, ordinal))
                        for event in events:
                            if reconstruct:
                                await self._event_processor.process_event(job_id, event, replay=True)
                            else:
                                await self._handle_tf_event(job_id, event)
                            decoded += 1
                            self._emitted_counts[job_id] = decoded
                            if event.kind == EventKind.session_ended:
                                terminal_seen = True
                    prefix = _advance_prefix(prefix, _canonical_record(raw) if raw is not None else b"")
                    ordinal += 1
                    await source.verify()
                    if (
                        reconstruct
                        and ordinal == accepted
                        and revision == 2
                        and checkpoint is not None
                        and decoded != checkpoint["decoded_event_count"]
                    ):
                        raise RuntimeError("Native decoded event count changed before its accepted checkpoint")
                    if not reconstruct:
                        checkpoint = {
                            "version": revision,
                            "framework": self._mapping_name,
                            "external_session_id": session_id,
                            "record_count": ordinal,
                            "prefix_sha256": prefix.hex(),
                            "mapping_sha256": normalizer.mapping_digest,
                            "timestamp_anchor": normalizer.anchor.isoformat(),
                            "output_contract": "codeplane-native-v1",
                        }
                        if revision == 2:
                            checkpoint.update(source=source.checkpoint(), decoded_event_count=decoded)
                        async with serialized_write(self._session_factory) as session:
                            if revision == 2:
                                checkpoint["emitted_event_count"] = await EventRepository(session).count_native_events(
                                    job_id, self._mapping_name, session_id
                                )
                            await JobRepository(session).update_ingest_checkpoint(job_id, checkpoint, decoded)
                    if terminal_seen and ordinal >= accepted:
                        await self._finalize_session(job_id, error_reason=terminal_reason)
                        if revision == 2:
                            assert checkpoint is not None
                            async with serialized_write(self._session_factory) as session:
                                checkpoint["emitted_event_count"] = await EventRepository(session).count_native_events(
                                    job_id, self._mapping_name, session_id
                                )
                                await JobRepository(session).update_ingest_checkpoint(job_id, checkpoint, decoded)
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("native_observation_failed", job_id=job_id, error=str(exc), exc_info=True)
            await self._publish_lifecycle(
                job_id,
                EventKind.log_line_emitted,
                {
                    "level": "error",
                    "message": f"Native observation stopped: {exc}",
                    "context": {"observation_status": "failed"},
                },
            )

    @staticmethod
    def _load_json_payload(payload: str | bytes) -> dict[str, Any] | None:
        try:
            raw = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.debug("ingest_invalid_json")
            return None
        return raw if isinstance(raw, dict) else None

    async def _handle_tf_event(self, job_id: str, event: SessionEvent) -> None:
        if event.session_id != job_id:
            event = event.model_copy(
                update={
                    "session_id": job_id,
                    "metadata": event.metadata.model_copy(update={"run_id": event.session_id}),
                }
            )
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
        async with self._finalization_control.hold(job_id):
            await self._finalize_locked(job_id, error_reason=error_reason)

    async def _finalize_locked(self, job_id: str, *, error_reason: str | None = None) -> None:
        if job_id in self._finalized_jobs:
            return
        now = datetime.now(UTC)
        new_state = JobState.failed if error_reason else JobState.review
        try:
            from backend.persistence.database import serialized_write
            from backend.persistence.job_repo import JobRepository
            from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

            async with serialized_write(self._session_factory) as session:
                repo = JobRepository(session)
                job = await repo.get(job_id)
                if job is not None and job.state in (JobState.canceled, JobState.completed):
                    new_state = job.state
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
        self._finalized_jobs.add(job_id)
        self._cleanup_job(job_id)

    def _cleanup_job(self, job_id: str) -> None:
        current = asyncio.current_task()
        for sid, owner in list(self._session_to_job.items()):
            if owner != job_id:
                continue
            self._session_to_job.pop(sid, None)
            for task in (self._tail_tasks.get(sid), self._liveness_tasks.get(sid)):
                if task is not None and task is not current:
                    task.cancel()
        self._job_worktrees.pop(job_id, None)
        self._job_base_refs.pop(job_id, None)
        self._prompt_captured.discard(job_id)
        self._emitted_counts.pop(job_id, None)
        self._event_processor.cleanup(job_id)
