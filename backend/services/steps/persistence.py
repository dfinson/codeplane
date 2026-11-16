"""EventBus subscriber that persists step lifecycle events to the database."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, cast

from backend.models.db import StepRow
from backend.models.events import (
    EventKind,
    SessionEvent,
    StepCompletedPayloadDict,
    StepStartedPayloadDict,
)

if TYPE_CHECKING:
    from backend.persistence.step_repo import StepRepository


class StepPersistenceSubscriber:
    """Listens for step events and persists them via StepRepository.

    Registered as an EventBus subscriber — receives ALL events.
    Filters to step_started / step_completed internally and early-returns
    on all other event kinds to minimize async overhead.
    """

    def __init__(self, step_repo: StepRepository) -> None:
        self._step_repo = step_repo

    async def __call__(self, event: SessionEvent) -> None:
        """EventBus entry point — dispatches to kind-specific handlers."""
        if event.kind == EventKind.step_started:
            await self._on_step_started(event)
        elif event.kind == EventKind.step_completed:
            await self._on_step_completed(event)
        # All other event kinds: early return (no-op)

    async def _on_step_started(self, event: SessionEvent) -> None:
        p = cast("StepStartedPayloadDict", event.payload)
        source_start = p.get("source_started_at")
        timing_source = p.get("timing_source", "legacy_unrecorded")
        row = StepRow(
            id=p["step_id"],
            job_id=event.session_id,
            step_number=p["step_number"],
            turn_id=p.get("turn_id"),
            intent=p.get("intent", ""),
            trigger=p["trigger"],
            started_at=(
                datetime.fromisoformat(source_start) if source_start is not None else None
            ) if timing_source == "source" else event.timestamp,
            timing_source=timing_source,
        )
        await self._step_repo.create(row)

    async def _on_step_completed(self, event: SessionEvent) -> None:
        p = cast("StepCompletedPayloadDict", event.payload)
        step_id = p["step_id"]
        assert step_id is not None
        source_end = p.get("source_completed_at")
        completed_at = (
            datetime.fromisoformat(source_end) if source_end is not None else None
        ) if p.get("timing_source") == "source" else event.timestamp

        # Derive title from files_written or agent_message
        title = _derive_step_title(
            p.get("files_written", []),
            p.get("agent_message"),
        )

        await self._step_repo.complete(
            step_id=step_id,
            status=p["status"],
            tool_count=p.get("tool_count", 0),
            duration_ms=p.get("duration_ms"),
            agent_message=p.get("agent_message"),
            completed_at=completed_at,
            start_sha=p.get("start_sha"),
            end_sha=p.get("end_sha"),
            files_read=json.dumps(p.get("files_read", [])),
            files_written=json.dumps(p.get("files_written", [])),
            preceding_context=p.get("preceding_context"),
        )
        if title:
            await self._step_repo.set_title(step_id, title)


def _derive_step_title(
    files_written: list[str],
    agent_message: str | None,
) -> str | None:
    """Best-effort title from step completion data."""
    if files_written:
        # Use basename of first few files
        from pathlib import PurePosixPath

        names = [PurePosixPath(f).name for f in files_written[:3]]
        title = "Edit " + ", ".join(names)
        if len(files_written) > 3:
            title += f" (+{len(files_written) - 3} more)"
        return title[:60]
    if agent_message:
        # First line, truncated
        first_line = agent_message.strip().split("\n")[0]
        return first_line[:60] if first_line else None
    return None
