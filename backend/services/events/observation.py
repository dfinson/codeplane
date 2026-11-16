"""Publication boundary for retained, non-executing observation history."""

from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent

OBSERVATION_NAMESPACE = "observation-source"
OBSERVATION_NOTIFICATIONS = TRANSCRIPT_KINDS | {
    EventKind.log_line_emitted, EventKind.turn_summary, EventKind.step_started, EventKind.step_completed,
}


def is_observation_history(event: SessionEvent) -> bool:
    namespace = event.metadata.namespace
    return bool(event.metadata.integration_binding_id and namespace and namespace[0] == OBSERVATION_NAMESPACE)


def allows_observation_notification(event: SessionEvent) -> bool:
    if is_observation_history(event):
        return event.kind in OBSERVATION_NOTIFICATIONS
    return True
