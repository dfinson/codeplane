from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.models.events import SessionEvent


class EventEnrichment(Protocol):
    def process(self, event: SessionEvent) -> SessionEvent | list[SessionEvent] | None: ...

    def flush_session(self, job_id: str) -> list[SessionEvent]: ...

    def flush(self) -> list[SessionEvent]: ...


class TitlePipeline(Protocol):
    async def push(self, event: SessionEvent) -> None: ...

    async def finalize_session(self, job_id: str) -> None: ...

    async def close(self) -> None: ...
