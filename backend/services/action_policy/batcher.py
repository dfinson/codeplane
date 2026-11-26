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


@dataclass(frozen=True)
class GateAction:
    """A single gate-tier action pending operator approval."""

    id: str
    action: Action
    classification: Classification
    checkpoint_ref: str
    metadata: EventMetadata | None = None
    approval_id: str | None = None
    policy_scope: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Batch:
    """A group of gate actions accumulated within the batch window."""

    id: str
    job_id: str
    actions: list[GateAction] = field(default_factory=list)
    displayed: tuple[GateAction, ...] | None = None
    active_ids: set[str] = field(default_factory=set)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    _timer: asyncio.TimerHandle | None = field(default=None, repr=False)
    _future: asyncio.Future[BatchResult] | None = field(default=None, repr=False)
    _publication: asyncio.Task[None] | None = field(default=None, repr=False)
    _resolution_published: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class BatchResult:
    """Resolution of a batch by the operator."""

    resolution: BatchResolution
    approved_ids: frozenset[str] = field(default_factory=frozenset)
    trust_grant_id: str | None = None
    trusted: bool = False


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
        policy_scope: str = "",
    ) -> BatchResult:
        """Submit a gate action and block until the operator resolves the batch."""
        gate_action = GateAction(
            id=action_id or uuid.uuid4().hex,
            action=action,
            classification=classification,
            checkpoint_ref=checkpoint_ref,
            metadata=metadata,
            approval_id=approval_id,
            policy_scope=policy_scope,
        )

        batch = self._get_or_create_batch(job_id)
        batch.actions.append(gate_action)
        batch.active_ids.add(gate_action.id)

        if batch._future is None:
            loop = asyncio.get_running_loop()
            batch._future = loop.create_future()
            self._pending_futures[batch.id] = batch._future

        try:
            self._reset_timer(batch)
            result = await asyncio.shield(batch._future)
            if result.trusted and batch.displayed is not None and not batch._resolution_published:
                from backend.models.events import EventKind, new_event

                batch._resolution_published = True
                await self._event_bus.publish(
                    new_event(
                        job_id,
                        EventKind.batch_approval_resolved,
                        {"batch_id": batch.id, "resolution": result.resolution},
                    )
                )
            return result
        finally:
            if batch.id in self._batches:
                batch.active_ids.discard(gate_action.id)
                if batch.displayed is None:
                    batch.actions.remove(gate_action)
                if not batch.active_ids:
                    self._remove_batch(batch)
                    from backend.models.events import EventKind, new_event

                    if batch.displayed is not None:
                        await self._event_bus.publish(
                            new_event(
                                job_id,
                                EventKind.batch_approval_resolved,
                                {"batch_id": batch.id, "resolution": "canceled"},
                            )
                        )

    def resolve_batch(
        self,
        batch_id: str,
        resolution: BatchResolution,
        approved_ids: set[str] | None = None,
        trust_grant_id: str | None = None,
    ) -> bool:
        """Resolve a batch, unblocking the waiting coroutine."""
        batch = self._batches.get(batch_id)
        if batch is None or batch.displayed is None:
            return False

        displayed_ids = frozenset(action.id for action in batch.displayed)
        if approved_ids and not approved_ids <= displayed_ids:
            log.warning("batch_resolution_outside_displayed_snapshot", batch_id=batch_id)
            return False
        selected = (
            displayed_ids
            if resolution == BatchResolution.approved
            else frozenset(approved_ids or ())
            if resolution == BatchResolution.partial
            else frozenset()
        )
        self._resolve(batch, BatchResult(resolution, selected, trust_grant_id))
        return True

    def _resolve(self, batch: Batch, result: BatchResult) -> None:
        future = self._pending_futures.pop(batch.id, None)
        if future and not future.done():
            future.set_result(result)
        self._remove_batch(batch)
        log.info("batch_resolved", batch_id=batch.id, resolution=result.resolution)

    def _remove_batch(self, batch: Batch) -> None:
        self._batches.pop(batch.id, None)
        if batch._timer:
            batch._timer.cancel()
        if batch._publication is not None and batch._publication is not asyncio.current_task():
            batch._publication.cancel()
        future = self._pending_futures.pop(batch.id, None)
        if future is not None and not future.done():
            future.cancel()

    def resolve_trusted_job(self, job_id: str, policy_scope: str, source_id: str) -> None:
        from backend.services.action_policy.classifier import WAIVABLE_REASON_CODES

        for batch in self.get_pending_batches(job_id):
            selected = frozenset(
                action.id
                for action in batch.actions
                if action.policy_scope == policy_scope and action.classification.reason_code in WAIVABLE_REASON_CODES
            )
            if selected:
                resolution = (
                    BatchResolution.approved if len(selected) == len(batch.actions) else BatchResolution.partial
                )
                self._resolve(batch, BatchResult(resolution, selected, source_id, trusted=True))

    def get_batch(self, batch_id: str) -> Batch | None:
        return self._batches.get(batch_id)

    def get_pending_batches(self, job_id: str | None = None) -> list[Batch]:
        return [batch for batch in self._batches.values() if job_id is None or batch.job_id == job_id]

    def cleanup_job(self, job_id: str) -> None:
        """Cancel any pending batches for a job."""
        for batch in self.get_pending_batches(job_id):
            self._remove_batch(batch)

    def _get_or_create_batch(self, job_id: str) -> Batch:
        for batch in self.get_pending_batches(job_id):
            if batch.displayed is None:
                return batch

        batch = Batch(id=uuid.uuid4().hex, job_id=job_id)
        self._batches[batch.id] = batch
        return batch

    def _reset_timer(self, batch: Batch) -> None:
        if batch._timer:
            batch._timer.cancel()

        def publish() -> None:
            batch._publication = asyncio.create_task(self._on_window_close(batch))

            def finished(task: asyncio.Task[None]) -> None:
                if not task.cancelled() and task.exception() is not None:
                    log.error("batch_publication_failed", batch_id=batch.id, reason=str(task.exception()))

            batch._publication.add_done_callback(finished)

        batch._timer = asyncio.get_running_loop().call_later(self._batch_window, publish)

    async def _on_window_close(self, batch: Batch) -> None:
        """Emit a batch_ready event when the accumulation window closes."""
        from backend.models.events import EventKind, new_event

        if batch.id not in self._batches or batch.displayed is not None:
            return  # Already resolved

        batch.displayed = tuple(batch.actions)
        summary = self._summarize(batch)
        await self._event_bus.publish(
            new_event(
                session_id=batch.job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.batch_approval_requested,
                payload={
                    "batch_id": batch.id,
                    "batch_size": len(batch.displayed),
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
                        for a in batch.displayed
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
