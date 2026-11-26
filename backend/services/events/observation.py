"""Publication boundary for retained, non-executing observation history."""

from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent
from backend.services.action_policy.credential_filter import filter_credentials

OBSERVATION_NAMESPACE = "observation-source"
OBSERVATION_NOTIFICATIONS = TRANSCRIPT_KINDS | {
    EventKind.log_line_emitted,
    EventKind.turn_summary,
    EventKind.step_started,
    EventKind.step_completed,
}


def is_observation_history(event: SessionEvent) -> bool:
    namespace = event.metadata.namespace
    return bool(event.metadata.integration_binding_id and namespace and namespace[0] == OBSERVATION_NAMESPACE)


def allows_observation_notification(event: SessionEvent) -> bool:
    if is_observation_history(event):
        return event.kind in OBSERVATION_NOTIFICATIONS
    return True


def has_source_grouping(event: SessionEvent) -> bool:
    grouping = event.payload.get("_codeplane_grouping")
    return (
        is_observation_history(event) and isinstance(grouping, dict) and grouping.get("version") == "cp-otel-groups-v1"
    )


def observed_operation_id(event: SessionEvent) -> str | None:
    observation = event.payload.get("_codeplane_observation")
    value = observation.get("operation_id") if isinstance(observation, dict) else None
    return value if has_source_grouping(event) and isinstance(value, str) and value else None


def is_observation_revision(event: SessionEvent) -> bool:
    observation = event.payload.get("_codeplane_observation")
    return (
        observed_operation_id(event) is not None
        and isinstance(observation, dict)
        and isinstance(observation.get("revision_of"), str)
    )


def observation_label(event: SessionEvent) -> str:
    if str(event.kind).startswith("tool."):
        prefix, detail = "Observed tool", event.payload.get("tool_name")
    elif str(event.kind).startswith("llm."):
        prefix, detail = "Observed model", event.payload.get("model", event.payload.get("requested_model"))
    else:
        prefix, detail = "Observed operation", event.payload.get("span_name")
    line = detail.split("\n", 1)[0][:4096] if isinstance(detail, str) else ""
    sanitized = " ".join(filter_credentials(line).text.split())[:60]
    return f"{prefix}: {sanitized}" if sanitized else f"{prefix} operation"
