"""Explicit wire conversion for executions pinned to the retiring TF runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.models.session_event import SessionEvent

if TYPE_CHECKING:
    from traceforge.enricher import Enricher
    from traceforge.pipeline import EventPipeline
    from traceforge.types import SessionEvent as LegacyEvent


def _legacy(event: SessionEvent) -> LegacyEvent:
    from traceforge.types import SessionEvent as LegacyEvent

    return LegacyEvent.model_validate(event.model_dump(mode="json"))


def _canonical(event: LegacyEvent) -> SessionEvent:
    return SessionEvent.model_validate(event.model_dump(mode="json"))


class LegacyEnrichment:
    def __init__(self, enricher: Enricher) -> None:
        self._enricher = enricher

    def process(self, event: SessionEvent) -> SessionEvent | list[SessionEvent] | None:
        result = self._enricher.process(_legacy(event))
        if result is None:
            return None
        return [_canonical(item) for item in result] if isinstance(result, list) else _canonical(result)

    def flush_session(self, job_id: str) -> list[SessionEvent]:
        return [_canonical(event) for event in self._enricher.flush_session(job_id)]

    def flush(self) -> list[SessionEvent]:
        return [_canonical(event) for event in self._enricher.flush()]


class LegacyTitles:
    def __init__(self, pipeline: EventPipeline) -> None:
        self._pipeline = pipeline

    async def push(self, event: SessionEvent) -> None:
        await self._pipeline.push(_legacy(event))

    async def finalize_session(self, job_id: str) -> None:
        await self._pipeline.finalize_session(job_id)

    async def close(self) -> None:
        await self._pipeline.close()
