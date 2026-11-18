"""Deterministic version-one activity projection for independently owned streams."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent, is_admission_receipt, new_event
from backend.services.action_policy.credential_filter import filter_credentials
from backend.services.events.observation import has_source_grouping, is_observation_revision, observation_label

if TYPE_CHECKING:
    from datetime import datetime

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
        source = event.metadata.attempt_id or (
            f"binding:{event.metadata.integration_binding_id}" if event.metadata.integration_binding_id else ""
        )
        return (event.session_id, event.metadata.execution_epoch or "", source)

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "key": list(key),
                "id": value.id,
                "label": value.label,
                "plan_step_id": value.plan_step_id,
                "source_event": value.source_event.model_dump(mode="json"),
            }
            for key, value in self._active.items()
        ]

    def restore(self, entries: list[dict[str, object]]) -> None:
        restored: dict[tuple[str, str, str], _Activity] = {}
        for entry in entries:
            event = SessionEvent.model_validate(entry["source_event"])
            key = self._key(event)
            activity_id, label = entry.get("id"), entry.get("label")
            if entry.get("key") != list(key) or not isinstance(activity_id, str) or not isinstance(label, str):
                raise ValueError("Invalid activity projection checkpoint")
            plan = entry.get("plan_step_id")
            if plan is not None and not isinstance(plan, str):
                raise ValueError("Invalid activity plan identity")
            restored[key] = _Activity(activity_id, label, plan, event)
        self._active = restored

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
        if is_observation_revision(event) or (event.kind not in TRANSCRIPT_KINDS and not has_source_grouping(event)):
            return
        key = self._key(event)
        current = self._active.get(key)
        plan_id = event.payload.get("plan_step_id")
        plan_id = plan_id if isinstance(plan_id, str) and plan_id.strip() else None
        new_boundary = (
            current is None
            or event.kind == EventKind.message_user
            or (plan_id is not None and current.plan_step_id is not None and plan_id != current.plan_step_id)
            or (
                event.metadata.integration_binding_id is not None
                and current is not None
                and bool(event.payload.get("turn_id"))
                and event.payload.get("turn_id") != current.source_event.payload.get("turn_id")
            )
        )
        if current is not None and plan_id is not None and current.plan_step_id is None:
            current.plan_step_id = plan_id
        if not new_boundary:
            return
        label = (
            observation_label(event)
            if has_source_grouping(event)
            else self._label(
                event.payload.get("content")
                if event.kind == EventKind.message_user
                else event.payload.get("plan_label"),
                "Work in progress",
            )
        )
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.version}:{key}:{event.id}"))
        activity = _Activity(identity, label, plan_id, event)
        if not replay:
            if current is not None:
                await self._close_activity(current, event.timestamp)
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
                        "grouping": event.payload.get("_codeplane_grouping"),
                    },
                    metadata=event.metadata,
                    event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:activity:1")),
                    timestamp=event.timestamp,
                )
            )
        self._active[key] = activity

    async def _close_activity(self, activity: _Activity, timestamp: datetime) -> None:
        source = activity.source_event
        await self._event_bus.publish(
            new_event(
                source.session_id,
                EventKind.turn_summary,
                {
                    "title": activity.label,
                    "activity_id": activity.id,
                    "activity_label": activity.label,
                    "activity_status": "closed",
                    "is_new_activity": False,
                    "plan_item_id": activity.plan_step_id,
                    "grouping_version": self.version,
                    "version": 2,
                    "step_id": source.payload.get("step_id"),
                    "turn_id": source.payload.get("turn_id"),
                    "grouping": source.payload.get("_codeplane_grouping"),
                },
                metadata=source.metadata,
                event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{activity.id}:activity:2")),
                timestamp=timestamp,
            )
        )

    async def close(self, job_id: str, timestamp: datetime) -> None:
        for key in tuple(self._active):
            if key[0] == job_id:
                await self._close_activity(self._active[key], timestamp)
                self._active.pop(key)

    def cleanup(self, job_id: str, *, attempt_id: str | None = None, execution_epoch: str | None = None) -> None:
        for key in tuple(self._active):
            if key[0] == job_id and (attempt_id is None or key[1:] == (execution_epoch, attempt_id)):
                self._active.pop(key)
