"""Deterministic version-one activity projection for independently owned streams."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent, is_admission_receipt, new_event
from backend.services.action_policy.credential_filter import filter_credentials

if TYPE_CHECKING:
    from backend.services.events.event_bus import EventBus


@dataclass
class _Activity:
    id: str
    label: str
    plan_step_id: str | None
    source_event: SessionEvent


class ActivityProjection:
    version = "cp-segments-v2"

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._active: dict[tuple[str, str, str], _Activity] = {}

    @staticmethod
    def _key(event: SessionEvent) -> tuple[str, str, str]:
        return (event.session_id, event.metadata.execution_epoch or "", event.metadata.attempt_id or "")

    @staticmethod
    def _label(value: object, fallback: str) -> str:
        text = value if isinstance(value, str) else ""
        # Bound before filtering, but filter the entire selected line so a secret
        # value cannot leak through truncation of the final title.
        line = text.split("\n", 1)[0][:4096]
        return " ".join(filter_credentials(line).text.split())[:80] or fallback

    async def push(self, event: SessionEvent, *, replay: bool = False) -> None:
        if is_admission_receipt(event):
            return
        if event.kind not in TRANSCRIPT_KINDS:
            return
        if event.payload.get("admission_status") or event.payload.get("execution_status") == "unconfirmed":
            return
        key = self._key(event)
        current = self._active.get(key)
        plan_id = event.payload.get("plan_step_id")
        plan_id = plan_id if isinstance(plan_id, str) and plan_id.strip() else None
        new_boundary = (
            current is None
            or event.kind == EventKind.message_user
            or (plan_id is not None and current.plan_step_id is not None and plan_id != current.plan_step_id)
        )
        if current is not None and plan_id is not None and current.plan_step_id is None:
            current.plan_step_id = plan_id
        if not new_boundary:
            return
        label = self._label(
            event.payload.get("content") if event.kind == EventKind.message_user else event.payload.get("plan_label"),
            "Work in progress",
        )
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.version}:{key}:{event.id}"))
        activity = _Activity(identity, label, plan_id, event)
        if not replay:
            await self._event_bus.publish(
                new_event(
                    event.session_id,
                    EventKind.turn_summary,
                    {
                        "title": label,
                        "activity_id": identity,
                        "activity_label": label,
                        "activity_status": "active",
                        "is_new_activity": True,
                        "plan_item_id": plan_id,
                        "grouping_version": self.version,
                        "version": 1,
                        "step_id": event.payload.get("step_id"),
                        "turn_id": event.payload.get("turn_id"),
                    },
                    metadata=event.metadata,
                    event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:activity:1")),
                    timestamp=event.timestamp,
                )
            )
        self._active[key] = activity

    def cleanup(self, job_id: str, *, attempt_id: str | None = None, execution_epoch: str | None = None) -> None:
        for key in tuple(self._active):
            if key[0] == job_id and (attempt_id is None or key[1:] == (execution_epoch, attempt_id)):
                self._active.pop(key)
