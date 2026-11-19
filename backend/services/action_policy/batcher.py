"""Approval batcher — accumulates gate actions within a time window."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from backend.models.session_event import EventMetadata
    from backend.services.action_policy.classifier import Action, Classification
    from backend.services.events.event_bus import EventBus

log = structlog.get_logger()


class BatchResolution(StrEnum):
    approved = "approved"
    rejected = "rejected"
    partial = "partial"


@dataclass
class GateAction:
    """A single gate-tier action pending operator approval."""

    id: str
    action: Action
    classification: Classification
    checkpoint_ref: str
    metadata: EventMetadata | None = None
    approval_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Batch:
    """A group of gate actions accumulated within the batch window."""

    id: str
    job_id: str
    actions: list[GateAction] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    _timer: asyncio.TimerHandle | None = field(default=None, repr=False)
    _future: asyncio.Future[BatchResult] | None = field(default=None, repr=False)


@dataclass
class BatchResult:
    """Resolution of a batch by the operator."""

    resolution: BatchResolution
    approved_ids: set[str] = field(default_factory=set)
    trust_grant_id: str | None = None


class ApprovalBatcher:
    """Accumulates gate-tier actions within a time window, emits batch events.

    The operator resolves batches through the API, which unblocks the waiting
    coroutine via the batch's asyncio.Future.
    """

    def __init__(
        self,
        event_bus: EventBus,
        batch_window_seconds: float = 5.0,
    ) -> None:
        self._event_bus = event_bus
        self._batch_window = batch_window_seconds
        self._batches: dict[str, Batch] = {}  # batch_id → Batch
        self._job_batches: dict[str, str] = {}  # job_id → active batch_id
        self._pending_futures: dict[str, asyncio.Future[BatchResult]] = {}

    def set_batch_window(self, seconds: float) -> None:
        """Update the batch window for future batches."""
        self._batch_window = seconds

    async def submit_and_wait(
        self,
        job_id: str,
        action: Action,
        classification: Classification,
        checkpoint_ref: str,
        *,
        action_id: str | None = None,
        metadata: EventMetadata | None = None,
        approval_id: str | None = None,
    ) -> BatchResult:
        """Submit a gate action and block until the operator resolves the batch."""
        gate_action = GateAction(
            id=action_id or uuid.uuid4().hex,
            action=action,
            classification=classification,
            checkpoint_ref=checkpoint_ref,
            metadata=metadata,
            approval_id=approval_id,
        )

        batch = self._get_or_create_batch(job_id)
        batch.actions.append(gate_action)

        if batch._future is None:
            loop = asyncio.get_running_loop()
            batch._future = loop.create_future()
            self._pending_futures[batch.id] = batch._future

        try:
            # Serial SDK callbacks cannot supply another action while waiting.
            if len(batch.actions) == 1:
                await self._on_window_close(batch)
            else:
                self._reset_timer(batch)
            return await asyncio.shield(batch._future)
        finally:
            if batch.id in self._batches:
                batch.actions.remove(gate_action)
                if not batch.actions:
                    self.cleanup_job(job_id)
                    from backend.models.events import EventKind, new_event

                    await self._event_bus.publish(
                        new_event(
                            job_id, EventKind.batch_approval_resolved, {"batch_id": batch.id, "resolution": "canceled"}
                        )
                    )
                else:
                    await self._on_window_close(batch)

    def resolve_batch(
        self,
        batch_id: str,
        resolution: BatchResolution,
        approved_ids: set[str] | None = None,
        trust_grant_id: str | None = None,
    ) -> bool:
        """Resolve a batch, unblocking the waiting coroutine."""
        batch = self._batches.get(batch_id)
        if batch is None:
            return False

        result = BatchResult(
            resolution=resolution,
            approved_ids=approved_ids or set(),
            trust_grant_id=trust_grant_id,
        )

        future = self._pending_futures.pop(batch_id, None)
        if future and not future.done():
            future.set_result(result)

        # Cleanup
        self._job_batches.pop(batch.job_id, None)
        self._batches.pop(batch_id, None)
        if batch._timer:
            batch._timer.cancel()

        log.info("batch_resolved", batch_id=batch_id, resolution=resolution)
        return True

    def get_batch(self, batch_id: str) -> Batch | None:
        return self._batches.get(batch_id)

    def get_pending_batches(self, job_id: str | None = None) -> list[Batch]:
        if job_id:
            bid = self._job_batches.get(job_id)
            if bid and bid in self._batches:
                return [self._batches[bid]]
            return []
        return list(self._batches.values())

    def cleanup_job(self, job_id: str) -> None:
        """Cancel any pending batches for a job."""
        bid = self._job_batches.pop(job_id, None)
        if bid:
            batch = self._batches.pop(bid, None)
            if batch:
                if batch._timer:
                    batch._timer.cancel()
                future = self._pending_futures.pop(bid, None)
                if future and not future.done():
                    future.cancel()

    def _get_or_create_batch(self, job_id: str) -> Batch:
        existing_id = self._job_batches.get(job_id)
        if existing_id and existing_id in self._batches:
            return self._batches[existing_id]

        batch = Batch(id=uuid.uuid4().hex, job_id=job_id)
        self._batches[batch.id] = batch
        self._job_batches[job_id] = batch.id
        return batch

    def _reset_timer(self, batch: Batch) -> None:
        if batch._timer:
            batch._timer.cancel()
        try:
            loop = asyncio.get_running_loop()
            batch._timer = loop.call_later(
                self._batch_window,
                lambda: asyncio.ensure_future(self._on_window_close(batch)),
            )
        except RuntimeError:
            # No event loop — timer won't fire, batch will be emitted on next submit
            pass

    async def _on_window_close(self, batch: Batch) -> None:
        """Emit a batch_ready event when the accumulation window closes."""
        from backend.models.events import EventKind, new_event

        if batch.id not in self._batches:
            return  # Already resolved

        summary = self._summarize(batch)
        await self._event_bus.publish(
            new_event(
                session_id=batch.job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.batch_approval_requested,
                payload={
                    "batch_id": batch.id,
                    "batch_size": len(batch.actions),
                    "summary": summary,
                    "actions": [
                        {
                            "id": a.id,
                            "kind": a.action.kind,
                            "recommendedAction": a.classification.recommended_action.value,
                            "reasonCode": a.classification.reason_code,
                            "reason": a.classification.reason,
                            "riskScore": a.classification.risk_score,
                            "riskBand": a.classification.risk_band,
                            "effect": a.classification.effect,
                            "checkpoint_ref": a.checkpoint_ref,
                            "description": _action_description(a.action),
                            "approval_id": a.approval_id,
                            "metadata": a.metadata.model_dump(mode="json") if a.metadata is not None else None,
                        }
                        for a in batch.actions
                    ],
                },
            )
        )
        log.info("batch_ready", batch_id=batch.id, size=len(batch.actions), summary=summary)

    @staticmethod
    def _summarize(batch: Batch) -> str:
        n = len(batch.actions)
        if n == 1:
            return _action_description(batch.actions[0].action)
        kinds = {a.action.kind for a in batch.actions}
        return f"{n} actions ({', '.join(sorted(kinds))})"


def _action_description(action: Action) -> str:
    """Human-readable description of an action."""
    if action.command:
        return action.command
    if action.tool_name and action.path:
        return f"{action.tool_name}: {action.path}"
    if action.tool_name:
        return f"Tool: {action.tool_name}"
    if action.mcp_tool:
        return f"MCP: {action.mcp_server}/{action.mcp_tool}"
    if action.path:
        return f"File: {action.path}"
    return str(action.kind)
