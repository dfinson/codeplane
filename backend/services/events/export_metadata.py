"""Metadata-only disclosure boundary, independent of optional exporter packages."""

from __future__ import annotations

import hashlib
import hmac
import math
from typing import TYPE_CHECKING, Literal

from backend.models.events import EventKind
from backend.services.action_policy.credential_filter import filter_credentials

if TYPE_CHECKING:
    from collections.abc import Sequence

    from backend.models.session_event import SessionEvent
    from backend.models.usage import UsageAmount

_KNOWN_KINDS = frozenset(kind.value for kind in EventKind)
_ROLES = frozenset({"primary", "scout", "monitor", "assessment", "title", "utility"})


class ExportDisclosureError(ValueError):
    """The export was withheld without exposing the offending content."""


def _string(value: str) -> str:
    try:
        filtered = filter_credentials(value)
    except (ValueError, UnicodeError) as exc:
        raise ExportDisclosureError("Export string filtering failed") from exc
    if filtered.redactions:
        raise ExportDisclosureError("Credential-shaped metadata withheld")
    if len(value) > 128:
        raise ExportDisclosureError("Metadata string exceeds its disclosure bound")
    return value


def _opaque(key: bytes, namespace: str, value: str) -> str:
    try:
        encoded = f"{namespace}:{value}".encode()
    except UnicodeError as exc:
        raise ExportDisclosureError("Export identity encoding failed") from exc
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def export_metadata(
    event: SessionEvent,
    *,
    identity_key: bytes,
    integration: str,
    provider: str,
    role: Literal["primary", "scout", "monitor", "assessment", "title", "utility"],
    approved_models: frozenset[str],
    usage: Sequence[UsageAmount] = (),
    versions: dict[str, str] | None = None,
) -> dict[str, object]:
    """Project an explicit allowlist; raw event text never enters the export queue."""
    if len(identity_key) < 32:
        raise ExportDisclosureError("Export identities require a protected 256-bit key")
    if role not in _ROLES:
        raise ExportDisclosureError("Unapproved export role")
    if len(usage) > 1000:
        raise ExportDisclosureError("Usage metadata exceeds its disclosure bound")
    metadata = event.metadata
    output: dict[str, object] = {
        "schema": "cp-export-v1",
        "integration": _string(integration),
        "provider": _string(provider),
        "role": role,
        "kind": event.kind if event.kind in _KNOWN_KINDS else "integration.event",
        "job_id": _opaque(identity_key, "job", event.session_id),
        "event_id": _opaque(identity_key, "event", event.id),
    }
    identifiers = {
        "execution_id": metadata.execution_token or metadata.execution_epoch,
        "source_id": metadata.integration_binding_id,
        "turn_id": metadata.turn_id or event.payload.get("turn_id"),
        "tool_call_id": event.payload.get("tool_call_id"),
        "span_id": metadata.span_id,
        "parent_span_id" if metadata.span_id else "parent_event_id": metadata.parent_id,
    }
    for field, value in identifiers.items():
        if isinstance(value, str) and value:
            namespace = "span" if "span" in field else "event" if field == "parent_event_id" else field
            output[field] = _opaque(identity_key, namespace, value)
    if metadata.duration_ms is not None:
        if not math.isfinite(metadata.duration_ms) or metadata.duration_ms < 0:
            raise ExportDisclosureError("Invalid event duration")
        output["duration_ms"] = metadata.duration_ms
    success = event.payload.get("success")
    if isinstance(success, bool):
        output["outcome"] = "succeeded" if success else "failed"
    for field in ("tool_count", "input_tokens", "output_tokens"):
        value = event.payload.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            output[field] = value
    allowed_versions = {"schema_version", "policy_version", "rule_version", "grammar_version", "title_version"}
    for field, value in (versions or {}).items():
        if field not in allowed_versions:
            raise ExportDisclosureError("Unapproved version metadata field")
        output[field] = _string(value)
    model = event.payload.get("model")
    if isinstance(model, str) and model in approved_models:
        output["model"] = _string(model)
    values = []
    for item in usage:
        if item.role not in _ROLES:
            raise ExportDisclosureError("Unapproved usage role")
        if item.model is not None and item.model not in approved_models:
            raise ExportDisclosureError("Usage model is not approved for export")
        values.append(
            {
                "amount": str(item.amount),
                "unit": _string(item.unit),
                "model": _string(item.model) if item.model is not None else None,
                "producer": _string(item.producer),
                "role": item.role,
                "verified": item.verified,
                "currency": item.currency,
            }
        )
    if values:
        output["usage"] = values
    pending: list[object] = [output]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            _string(value)
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return output
