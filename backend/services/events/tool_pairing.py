"""Bounded, application-owned tool lifecycle pairing without policy side effects."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC

from backend.models.events import EventKind, SessionEvent
from backend.models.session_event import EventMetadata
from backend.services.tool_formatters import format_tool_display


@dataclass(frozen=True, slots=True)
class PairingCheckpoint:
    starts: tuple[SessionEvent, ...]


class ToolPairing:
    def __init__(self, *, max_pending: int = 1024) -> None:
        if not 1 <= max_pending <= 10_000:
            raise ValueError("Tool pairing capacity must be between 1 and 10000")
        self._limit = max_pending
        self._starts: OrderedDict[tuple[str, str], SessionEvent] = OrderedDict()

    @staticmethod
    def _key(event: SessionEvent, call_id: str) -> tuple[str, str]:
        metadata = event.metadata
        namespace = metadata.namespace or ()
        if namespace and namespace[0] == "observation-source":
            namespace = namespace[2:]
        scope = json.dumps([
            metadata.integration_binding_id, metadata.run_id, metadata.execution_epoch,
            metadata.attempt_id, metadata.execution_token, namespace, call_id,
        ], separators=(",", ":"))
        return event.session_id, scope

    def process(self, event: SessionEvent) -> SessionEvent | list[SessionEvent] | None:
        call_id = event.payload.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id.strip():
            if event.kind in (EventKind.tool_call_started, EventKind.tool_call_completed, EventKind.tool_call_failed):
                return self._orphan(event, "missing_identity")
            return event
        key = self._key(event, call_id)
        if event.kind == EventKind.tool_call_started:
            previous = self._starts.get(key)
            if previous is not None:
                if previous.id == event.id:
                    return None
                # Identity collisions are visible, never paired by tool text.
                self._starts.pop(key)
                return [self._orphan(previous, "duplicate_start"), self._orphan(event, "duplicate_start")]
            self._starts[key] = event
            if len(self._starts) > self._limit:
                _, oldest = self._starts.popitem(last=False)
                return self._orphan(oldest, "pairing_capacity")
            return None
        if event.kind not in (EventKind.tool_call_completed, EventKind.tool_call_failed):
            return event
        start = self._starts.pop(key, None)
        if start is None:
            return self._orphan(event, "missing_start")
        if start.metadata.attempt_id != event.metadata.attempt_id or (
            start.metadata.execution_epoch != event.metadata.execution_epoch
        ):
            return [self._orphan(start, "correlation_mismatch"), self._orphan(event, "correlation_mismatch")]
        payload = dict(start.payload)
        payload.update(event.payload)
        duration = event.metadata.duration_ms
        fallback_time = any(
            isinstance(source := item.payload.get("_codeplane_source"), dict) and source.get("timestamp_fallback")
            for item in (start, event)
        )
        if duration is None and not fallback_time:
            finished_at = event.timestamp if event.timestamp.tzinfo is not None else event.timestamp.replace(tzinfo=UTC)
            started_at = start.timestamp if start.timestamp.tzinfo is not None else start.timestamp.replace(tzinfo=UTC)
            elapsed = (finished_at - started_at).total_seconds() * 1000
            if elapsed >= 0:
                duration = elapsed
        updates = start.metadata.model_dump()
        updates.update(event.metadata.model_dump(exclude_unset=True))
        updates["duration_ms"] = duration
        if event.metadata.motivation is None:
            updates["motivation"] = start.metadata.motivation
        name = payload.get("tool_name")
        arguments = payload.get("arguments")
        if isinstance(name, str):
            result = payload.get("result")
            updates["tool_display"] = event.metadata.tool_display or format_tool_display(
                name,
                arguments if isinstance(arguments, str) else json.dumps(arguments),
                tool_result=result if isinstance(result, str) else None,
                tool_success=event.kind != EventKind.tool_call_failed and payload.get("success") is not False,
            )
        return event.model_copy(update={"payload": payload, "metadata": EventMetadata.model_validate(updates)})

    @staticmethod
    def _orphan(event: SessionEvent, reason: str) -> SessionEvent:
        payload = dict(event.payload)
        payload["_codeplane_pairing"] = {"status": "unresolved", "reason": reason}
        return event.model_copy(update={"payload": payload})

    def flush_session(self, job_id: str) -> list[SessionEvent]:
        return [
            self._orphan(self._starts.pop(key), "missing_completion") for key in tuple(self._starts) if key[0] == job_id
        ]

    def flush(self) -> list[SessionEvent]:
        pending = [self._orphan(event, "missing_completion") for event in self._starts.values()]
        self._starts.clear()
        return pending

    def checkpoint(self) -> PairingCheckpoint:
        return PairingCheckpoint(tuple(self._starts.values()))

    def restore(self, checkpoint: PairingCheckpoint) -> None:
        if len(checkpoint.starts) > self._limit:
            raise ValueError("Checkpoint exceeds pairing capacity")
        starts: OrderedDict[tuple[str, str], SessionEvent] = OrderedDict()
        for event in checkpoint.starts:
            call_id = event.payload.get("tool_call_id")
            if event.kind != EventKind.tool_call_started or not isinstance(call_id, str) or not call_id.strip():
                raise ValueError("Invalid pending tool checkpoint")
            key = self._key(event, call_id)
            if key in starts:
                raise ValueError("Duplicate pending tool identity")
            starts[key] = event
        self._starts = starts
