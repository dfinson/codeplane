"""Canonical event pairing, scoped transcript projection, diffs and publication.

The production root supplies application-owned pairing and deterministic activity
projection. Historical implementations can still be supplied through the explicit
enrichment/title contracts without changing the canonical event model.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.models.events import (
    TRANSCRIPT_KINDS,
    TRANSCRIPT_STREAMING_KINDS,
    EventKind,
    SessionEvent,
    is_admission_receipt,
)
from backend.models.session_event import EventMetadata
from backend.services.events.activity_projection import ActivityProjection
from backend.services.events.tool_pairing import ToolPairing

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.services.artifacts.diff_service import DiffService
    from backend.services.events.contracts import EventEnrichment, TitlePipeline
    from backend.services.events.event_bus import EventBus
    from backend.services.steps.tracker import StepTracker, _StepState
    from backend.services.trail import TrailService

log = structlog.get_logger()

# report_intent is an internal Copilot marker tool — it never mutates the
# worktree, so it must not trigger a diff recalculation.
_DIFF_SKIP_TOOL = "report_intent"
RECONSTRUCTING_EVENTS: ContextVar[bool] = ContextVar("reconstructing_native_events", default=False)


class EventProcessor:
    """Event processing shared between managed and imported sessions.

    Callers provide the per-job context (worktree_path, base_ref) and this
    class applies the configured projections to each ``SessionEvent``.
    """

    def __init__(
        self,
        event_bus: EventBus,
        diff_service: DiffService | None = None,
        step_tracker: StepTracker | None = None,
        trail_service: TrailService | None = None,
        *,
        enricher: EventEnrichment | None = None,
        title_pipeline: TitlePipeline | None = None,
        persisted: Callable[[SessionEvent], Awaitable[bool]] | None = None,
        activity_projection: ActivityProjection | None = None,
        retain_source_events: bool = False,
        fail_on_enrichment_error: bool = False,
    ) -> None:
        self._event_bus = event_bus
        self._diff_service = diff_service
        self._step_tracker = step_tracker
        self._trail_service = trail_service
        self._enricher = enricher
        self._title_pipeline = title_pipeline
        self._persisted = persisted
        # Synthesized turn_id per job for producers that don't provide one
        # (imported CLI sessions). Rotated on completed agent messages.
        self._turn_ids: dict[str, str] = {}
        self._attempt_processors: dict[tuple[str, str, str], EventProcessor] = {}
        self._attempt_workspaces: dict[tuple[str, str, str], tuple[str, str | None]] = {}
        self._attempt_scope: tuple[str, str, str] | None = None
        self._activity_projection = activity_projection
        self._retain_source_events = retain_source_events
        self._fail_on_enrichment_error = fail_on_enrichment_error

    def register_attempt_worktree(
        self,
        job_id: str,
        attempt_id: str,
        execution_epoch: str,
        worktree_path: str,
        *,
        managed: bool = False,
        base_ref: str | None = None,
    ) -> None:
        if not attempt_id or not execution_epoch:
            raise ValueError("Attempt registration requires explicit attempt and execution epoch")
        key = (job_id, execution_epoch, attempt_id)
        if key in self._attempt_processors:
            if self._attempt_workspaces[key] != (worktree_path, base_ref):
                raise ValueError("Attempt workspace cannot change during execution")
            return
        metadata = EventMetadata(attempt_id=attempt_id, execution_epoch=execution_epoch)
        processor = EventProcessor(
            self._event_bus,
            self._diff_service.scoped(metadata, read_only=not managed) if self._diff_service is not None else None,
            self._step_tracker,
            enricher=ToolPairing(),
            persisted=self._persisted,
        )
        processor._attempt_scope = key
        processor._activity_projection = ActivityProjection(self._event_bus)
        self._attempt_processors[key] = processor
        self._attempt_workspaces[key] = (worktree_path, base_ref)
        if self._step_tracker is not None:
            self._step_tracker.register_worktree(
                job_id,
                worktree_path,
                managed=managed,
                attempt_id=attempt_id,
                execution_epoch=execution_epoch,
            )

    def _current_step(self, job_id: str, event: SessionEvent) -> _StepState | None:
        assert self._step_tracker is not None
        if event.metadata.attempt_id is None:
            return self._step_tracker.current_step(job_id)
        return self._step_tracker.current_step(
            job_id,
            attempt_id=event.metadata.attempt_id,
            execution_epoch=event.metadata.execution_epoch,
        )

    def register_worktree(self, job_id: str, worktree_path: str) -> None:
        """Register worktree for step tracker SHA capture."""
        if self._step_tracker is not None:
            self._step_tracker.register_worktree(job_id, worktree_path)

    def restore_projection(self, job_id: str, events: list[SessionEvent]) -> None:
        if self._step_tracker is not None:
            for event in events:
                self._step_tracker.restore_event(job_id, event)

    async def process_event(
        self,
        job_id: str,
        event: SessionEvent,
        worktree_path: str | None = None,
        base_ref: str | None = None,
        *,
        replay: bool = False,
    ) -> SessionEvent | None:
        """Process a TF-native ``SessionEvent`` through the standard pipeline.

        Returns the published event, or ``None`` if the event was consumed
        internally (e.g. a ``file.edited`` diff trigger) and nothing was emitted.
        """
        if is_admission_receipt(event):
            if not replay:
                await self._event_bus.publish(event)
                return event
            return None
        if self._attempt_scope is None and event.metadata.attempt_id is not None:
            key = (job_id, event.metadata.execution_epoch or "", event.metadata.attempt_id)
            processor = self._attempt_processors.get(key)
            if processor is None:
                raise ValueError("Attempt event has no registered execution workspace")
            workspace, reference = self._attempt_workspaces[key]
            return await processor.process_event(job_id, event, workspace, reference, replay=replay)
        kind = event.kind
        if replay:
            await self._reconstruct(event)
            return None
        diff_eligible = self._diff_service is not None and bool(worktree_path) and bool(base_ref)

        # Diff recalculation on native file-edit events — consumed, not published
        # (the FE receives file changes via the synthesized diff.updated event).
        if kind == EventKind.file_edited:
            if diff_eligible:
                assert self._diff_service is not None and worktree_path and base_ref
                await self._diff_service.on_worktree_file_modified(job_id, worktree_path, base_ref)
            if diff_eligible or not self._retain_source_events:
                return None

        if self._retain_source_events and kind == EventKind.tool_call_started:
            if self._step_tracker is not None:
                await self._step_tracker.on_transcript_event(job_id, event)
                current = self._current_step(job_id, event)
                if current is not None:
                    event.payload["step_id"] = current.step_id
                    event.payload["step_number"] = current.step_number
            if self._activity_projection is not None:
                await self._activity_projection.push(event)

        # ── TraceForge enrichment ──
        # Run the TF enricher before any CodePlane annotation so that
        # classification, visibility, phases, duration_ms, and risk are
        # stamped on the event metadata.  The enricher is stateful (tool
        # start/complete pairing), so it may buffer a tool_start (returns
        # None) or flush orphans alongside a new event (returns list).
        if self._enricher is not None:
            try:
                enriched = self._enricher.process(event)
            except Exception:
                if self._fail_on_enrichment_error:
                    raise
                log.warning(
                    "tf_enricher_failed",
                    event_id=event.id,
                    kind=kind,
                    exc_info=True,
                )
                enriched = event

            if enriched is None:
                # Tool start buffered for pairing — nothing to publish yet.
                return None

            if isinstance(enriched, list):
                # Orphan(s) flushed alongside a new event — publish all.
                last: SessionEvent | None = None
                for e in enriched:
                    last = await self._process_enriched(job_id, e, worktree_path, base_ref, diff_eligible)
                return last

            event = enriched

        if (
            self._persisted is not None
            and "_codeplane_source" in event.payload
            and event.kind != EventKind.message_delta
            and await self._persisted(event)
        ):
            if self._title_pipeline is not None and event.kind in TRANSCRIPT_KINDS:
                await self._title_pipeline.push(event)
            return event

        # Diff recalculation on tool completions (skip internal marker tools)
        if (
            diff_eligible
            and kind in (EventKind.tool_call_completed, EventKind.tool_call_failed)
            and event.payload.get("tool_name") != _DIFF_SKIP_TOOL
        ):
            assert self._diff_service is not None and worktree_path and base_ref
            await self._diff_service.on_worktree_file_modified(job_id, worktree_path, base_ref)

        is_transcript = kind in TRANSCRIPT_KINDS

        # Ensure transcript events carry a turn_id for step tracking. Managed
        # adapters already include one; imported sessions don't — synthesize here
        # and rotate on each completed agent message (turn boundary).
        if is_transcript:
            payload = event.payload
            if not payload.get("turn_id"):
                tid = self._turn_ids.get(job_id)
                if not tid:
                    tid = str(uuid.uuid4())
                    self._turn_ids[job_id] = tid
                payload["turn_id"] = tid
            if kind == EventKind.message_assistant:
                self._turn_ids[job_id] = str(uuid.uuid4())

        # Step tracking — annotate non-streaming transcript events with step
        # boundaries. Streaming partials (deltas, tool output chunks) are skipped.
        if is_transcript and kind not in TRANSCRIPT_STREAMING_KINDS and self._step_tracker is not None:
            await self._step_tracker.on_transcript_event(job_id, event)
            current = self._current_step(job_id, event)
            if current:
                event.payload["step_number"] = current.step_number
                if self._attempt_scope is not None or self._trail_service is None:
                    event.payload["step_id"] = current.step_id
            # TrailService is the sole step_id authority (ps-* IDs)
            if self._trail_service is not None:
                plan_step_id = self._trail_service.get_active_plan_step_id(job_id)
                if plan_step_id:
                    event.payload["step_id"] = plan_step_id

        await self._event_bus.publish(event)
        if self._activity_projection is not None:
            await self._activity_projection.push(event)

        # Feed the title pipeline (boundary + title inference) for transcript
        # events.  TitleUpdate callbacks are converted to turn_summary events
        # and published to the event bus by the pipeline's sink.
        if is_transcript and self._title_pipeline is not None:
            try:
                await self._title_pipeline.push(event)
            except Exception:
                log.warning(
                    "title_pipeline_push_failed",
                    event_id=event.id,
                    exc_info=True,
                )

        return event

    async def _process_enriched(
        self,
        job_id: str,
        event: SessionEvent,
        worktree_path: str | None,
        base_ref: str | None,
        diff_eligible: bool,
    ) -> SessionEvent | None:
        """Publish one already-enriched event through the rest of the pipeline.

        Shared by the list-of-orphans branch so each flushed event gets the
        same diff / turn_id / step-tracking annotation as a normal event.
        """
        kind = event.kind
        if (
            self._persisted is not None
            and "_codeplane_source" in event.payload
            and event.kind != EventKind.message_delta
            and await self._persisted(event)
        ):
            if self._title_pipeline is not None and kind in TRANSCRIPT_KINDS:
                await self._title_pipeline.push(event)
            return event

        if (
            diff_eligible
            and kind in (EventKind.tool_call_completed, EventKind.tool_call_failed)
            and event.payload.get("tool_name") != _DIFF_SKIP_TOOL
        ):
            assert self._diff_service is not None and worktree_path and base_ref
            await self._diff_service.on_worktree_file_modified(job_id, worktree_path, base_ref)

        is_transcript = kind in TRANSCRIPT_KINDS

        if is_transcript:
            payload = event.payload
            if not payload.get("turn_id"):
                tid = self._turn_ids.get(job_id)
                if not tid:
                    tid = str(uuid.uuid4())
                    self._turn_ids[job_id] = tid
                payload["turn_id"] = tid
            if kind == EventKind.message_assistant:
                self._turn_ids[job_id] = str(uuid.uuid4())

        if is_transcript and kind not in TRANSCRIPT_STREAMING_KINDS and self._step_tracker is not None:
            await self._step_tracker.on_transcript_event(job_id, event)
            current = self._current_step(job_id, event)
            if current:
                event.payload["step_number"] = current.step_number
                if self._attempt_scope is not None or self._trail_service is None:
                    event.payload["step_id"] = current.step_id
            if self._trail_service is not None:
                plan_step_id = self._trail_service.get_active_plan_step_id(job_id)
                if plan_step_id:
                    event.payload["step_id"] = plan_step_id

        await self._event_bus.publish(event)
        if self._activity_projection is not None:
            await self._activity_projection.push(event)

        if is_transcript and self._title_pipeline is not None:
            try:
                await self._title_pipeline.push(event)
            except Exception:
                log.warning(
                    "title_pipeline_push_failed",
                    event_id=event.id,
                    exc_info=True,
                )

        return event

    async def _reconstruct(self, event: SessionEvent) -> None:
        enriched = self._enricher.process(event) if self._enricher is not None else event
        if enriched is None:
            return
        for item in enriched if isinstance(enriched, list) else [enriched]:
            if self._activity_projection is not None:
                await self._activity_projection.push(item, replay=True)
            if self._title_pipeline is not None and item.kind in TRANSCRIPT_KINDS:
                token = RECONSTRUCTING_EVENTS.set(True)
                try:
                    await self._title_pipeline.push(item)
                finally:
                    RECONSTRUCTING_EVENTS.reset(token)

    async def on_job_terminal(self, job_id: str, outcome: str) -> None:
        """Notify step tracker and finalize this job's session through TraceForge.

        Called from both managed and imported terminal paths. Uses the public
        per-session APIs added in TraceForge 0.1.5:
        - ``Enricher.flush_session(session_id)`` — drains orphan tool-starts
        - ``EventPipeline.finalize_session(session_id)`` — emits final activity
          titles for this session without affecting concurrent sessions.
        """
        # Flush before closing steps, otherwise an orphan start can reopen one.
        if self._enricher is not None:
            for orphan in self._enricher.flush_session(job_id):
                await self._process_enriched(job_id, orphan, None, None, False)
        if self._attempt_scope is not None and self._step_tracker is not None:
            _, epoch, attempt = self._attempt_scope
            await self._step_tracker.on_attempt_terminal(job_id, attempt, epoch, outcome)
        elif self._step_tracker is not None:
            await self._step_tracker.on_job_terminal(job_id, outcome)
        for key, processor in tuple(self._attempt_processors.items()):
            if key[0] == job_id:
                await processor.on_job_terminal(job_id, outcome)
        if self._activity_projection is not None and not self._retain_source_events:
            await self._activity_projection.close(job_id, datetime.now(UTC))
        # Finalize this session's title streams (per-session, not global)
        if self._title_pipeline is not None:
            try:
                await self._title_pipeline.finalize_session(job_id)
            except Exception:
                log.warning(
                    "title_pipeline_finalize_failed",
                    job_id=job_id,
                    exc_info=True,
                )

    async def shutdown(self) -> None:
        """Global teardown — flush all pipeline state after runtime stops pushing.

        Safe to call only when NO more events will be pushed (app shutdown).
        Drains the enricher's remaining buffered orphan tool-starts and closes
        the title pipeline (which flushes any sessions not yet finalized).
        """
        for processor in tuple(self._attempt_processors.values()):
            await processor.shutdown()
        if self._enricher is not None:
            for orphan in self._enricher.flush():
                await self._event_bus.publish(orphan)
        if self._title_pipeline is not None:
            try:
                await self._title_pipeline.close()
            except Exception:
                log.warning("title_pipeline_close_failed", exc_info=True)

    def discard_source_reconstruction(self, job_id: str) -> None:
        """Drop failed source pairing/projection state without publishing orphans."""
        if self._enricher is not None:
            self._enricher.flush_session(job_id)
        self.cleanup(job_id)

    def cleanup(self, job_id: str) -> None:
        """Clean up per-job tracking state."""
        self._turn_ids.pop(job_id, None)
        for key in tuple(self._attempt_processors):
            if key[0] == job_id:
                self._attempt_processors.pop(key).cleanup(job_id)
                self._attempt_workspaces.pop(key, None)
        if self._activity_projection is not None:
            self._activity_projection.cleanup(job_id)
        if self._step_tracker is not None and self._attempt_scope is not None:
            _, epoch, attempt = self._attempt_scope
            self._step_tracker.cleanup_attempt(job_id, attempt, epoch)
        elif self._step_tracker is not None:
            self._step_tracker.cleanup(job_id)
        if self._diff_service is not None:
            self._diff_service.cleanup(job_id)

    async def on_attempt_terminal(self, job_id: str, attempt_id: str, execution_epoch: str, outcome: str) -> None:
        key = (job_id, execution_epoch, attempt_id)
        processor = self._attempt_processors.get(key)
        if processor is not None:
            await processor.on_job_terminal(job_id, outcome)
            processor.cleanup(job_id)
            self._attempt_processors.pop(key, None)
            self._attempt_workspaces.pop(key, None)
