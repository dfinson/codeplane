"""Metadata-only disclosure boundary, independent of optional exporter packages."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from typing import TYPE_CHECKING

from backend.models.events import EventKind
from backend.services.action_policy.credential_filter import filter_credentials

if TYPE_CHECKING:
    from collections.abc import Sequence

    from backend.models.session_event import SessionEvent
    from backend.models.usage import UsageAmount

_KNOWN_KINDS = frozenset(kind.value for kind in EventKind)
_ROLES = frozenset({"primary", "scout", "monitor", "assessment", "title", "utility"})
_IDENTIFIERS = frozenset(
    {
        "job_id",
        "event_id",
        "execution_id",
        "source_id",
        "source_run_id",
        "source_trace_id",
        "turn_id",
        "tool_call_id",
        "span_id",
        "parent_span_id",
        "parent_event_id",
    }
)
_VERSIONS = frozenset({"schema_version", "policy_version", "rule_version", "grammar_version", "title_version"})
_COUNTS = frozenset({"tool_count", "input_tokens", "output_tokens"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")
_VERSION = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9._-]*[-_@:])?[vV]?[0-9]+(?:\.[0-9]+)*(?:[-+][A-Za-z0-9][A-Za-z0-9._-]*)?"
    r"|(?:sha256:)?[a-f0-9]{40,64}"
)
_MODEL_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
_FILE_SUFFIX = re.compile(
    r"\.(?:csv|tsv|txt|md|pem|key|jsonl?|ya?ml|toml|ini|py|js|ts|sh|ps1|db|sqlite|log|html|pdf)$", re.I
)


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
    if (
        re.search(r"[A-Za-z][A-Za-z0-9+.-]*://", value)
        or value.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:[\\/]", value)
    ):
        raise ExportDisclosureError("Address or path metadata withheld")
    return value


def _opaque(key: bytes, namespace: str, value: str) -> str:
    try:
        encoded = f"{namespace}:{value}".encode()
    except UnicodeError as exc:
        raise ExportDisclosureError("Export identity encoding failed") from exc
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def _identifier(value: str) -> str:
    if _IDENTIFIER.fullmatch(_string(value)) is None:
        raise ExportDisclosureError("Metadata requires a categorical identifier")
    return value


def _version(value: str) -> str:
    if _VERSION.fullmatch(_string(value)) is None:
        raise ExportDisclosureError("Metadata requires a version identifier")
    return value


def _model(value: str) -> str:
    _string(value)
    parts = value.split("/")
    if any(_MODEL_SEGMENT.fullmatch(part) is None for part in parts) or _FILE_SUFFIX.search(parts[-1]):
        raise ExportDisclosureError("Metadata requires a model identifier")
    resource = (
        len(parts) == 4
        and parts[0] in {"publishers", "accounts"}
        and parts[2] == "models"
        or len(parts) == 6
        and parts[0] == "projects"
        and parts[2] == "locations"
        and parts[4] in {"models", "endpoints"}
        or len(parts) == 8
        and parts[0] == "projects"
        and parts[2] == "locations"
        and parts[4] == "publishers"
        and parts[6] == "models"
        or len(parts) in {3, 4}
        and parts[0] in {"hf.co", "huggingface.co"}
    )
    if len(parts) > 2 and not resource:
        raise ExportDisclosureError("Unsupported model resource identifier")
    return value


def export_metadata(
    event: SessionEvent,
    *,
    identity_key: bytes,
    integration: str,
    provider: str,
    role: str,
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
    trace_id = event.payload.get("trace_id") or (metadata.correlation_id if metadata.span_id is not None else None)
    identifiers = {
        "execution_id": metadata.execution_token or metadata.execution_epoch,
        "source_id": metadata.integration_binding_id,
        "source_run_id": metadata.run_id,
        "source_trace_id": trace_id,
        "turn_id": metadata.turn_id or event.payload.get("turn_id"),
        "tool_call_id": event.payload.get("tool_call_id"),
        "span_id": metadata.span_id,
        "parent_span_id" if metadata.span_id else "parent_event_id": metadata.parent_id,
    }
    if any(value is not None and not isinstance(value, str) for value in identifiers.values()):
        raise ExportDisclosureError("Unsupported export identity representation")
    provided_model = event.payload.get("model")
    selected_input = {
        "job_id": event.session_id,
        "event_id": event.id,
        "identifiers": identifiers,
        "integration": integration,
        "provider": provider,
        "role": role,
        "versions": versions,
        "model": provided_model if isinstance(provided_model, str) and provided_model in approved_models else None,
        "usage": [
            {
                "amount": str(item.amount),
                "unit": item.unit,
                "model": item.model,
                "producer": item.producer,
                "role": item.role,
                "verified": item.verified,
                "currency": item.currency,
            }
            for item in usage
        ],
    }
    try:
        input_size = len(json.dumps(selected_input, ensure_ascii=False, allow_nan=False).encode())
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ExportDisclosureError("Unsupported export representation") from exc
    if input_size > 32 * 1024:
        raise ExportDisclosureError("Export inspection input exceeds 32 KiB")
    output: dict[str, object] = {
        "schema": "cp-export-v1",
        "integration": _identifier(integration),
        "provider": _identifier(provider),
        "role": role,
        "kind": event.kind if event.kind in _KNOWN_KINDS else "integration.event",
        "job_id": _opaque(identity_key, "job", event.session_id),
        "event_id": _opaque(identity_key, "event", event.id),
    }
    owner_scope = [
        event.session_id,
        metadata.integration_binding_id,
        metadata.execution_token or metadata.execution_epoch,
    ]
    source_scope = [*owner_scope, metadata.run_id, trace_id]
    for field, value in identifiers.items():
        if isinstance(value, str) and value:
            namespace = "span" if "span" in field else "event" if field == "parent_event_id" else field
            scope = owner_scope if field == "source_run_id" else source_scope
            scoped = (
                json.dumps([scope, value], separators=(",", ":"))
                if field
                in {
                    "source_run_id",
                    "source_trace_id",
                    "tool_call_id",
                    "span_id",
                    "parent_span_id",
                }
                else value
            )
            output[field] = _opaque(identity_key, namespace, scoped)
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
            if value > 2**63 - 1:
                raise ExportDisclosureError("Export count exceeds the OTLP integer representation")
            output[field] = value
    for field, value in (versions or {}).items():
        if field not in _VERSIONS:
            raise ExportDisclosureError("Unapproved version metadata field")
        output[field] = _version(value)
    model = event.payload.get("model")
    if isinstance(model, str) and model in approved_models:
        output["model"] = _model(model)
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
                "model": _model(item.model) if item.model is not None else None,
                "producer": _identifier(item.producer),
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
    if len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode()) > 8 * 1024:
        raise ExportDisclosureError("Export projection exceeds 8 KiB")
    return output


def validate_projection(projection: dict[str, object], approved_models: frozenset[str]) -> dict[str, object]:
    """Validate an already keyed native sink projection before any SDK object exists."""
    from backend.models.usage import UsageAmount

    try:
        encoded = json.dumps(projection, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ExportDisclosureError("Unsupported export projection") from exc
    if len(encoded) > 8192:
        raise ExportDisclosureError("Export projection exceeds 8 KiB")
    allowed = (
        _IDENTIFIERS
        | _VERSIONS
        | _COUNTS
        | {
            "schema",
            "integration",
            "provider",
            "role",
            "kind",
            "model",
            "outcome",
            "duration_ms",
            "usage",
        }
    )
    if projection.keys() - allowed or any(
        field not in projection for field in ("schema", "integration", "provider", "role", "kind", "job_id", "event_id")
    ):
        raise ExportDisclosureError("Unapproved or incomplete export projection")
    if (
        projection["schema"] != "cp-export-v1"
        or not isinstance(projection["role"], str)
        or projection["role"] not in _ROLES
        or not isinstance(projection["kind"], str)
        or projection["kind"] not in _KNOWN_KINDS | {"integration.event"}
    ):
        raise ExportDisclosureError("Unapproved export projection schema or role")
    for key, value in projection.items():
        if key in _IDENTIFIERS:
            if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
                raise ExportDisclosureError("Export identity was not locally keyed")
        elif key in _COUNTS:
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 2**63:
                raise ExportDisclosureError("Invalid projected count")
        elif key == "duration_ms":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ExportDisclosureError("Invalid projected duration")
        elif key == "usage":
            if not isinstance(value, list):
                raise ExportDisclosureError("Invalid projected usage")
            for item in value:
                fields = {"amount", "unit", "model", "producer", "role", "verified", "currency"}
                if not isinstance(item, dict) or set(item) != fields:
                    raise ExportDisclosureError("Unapproved projected usage field")
                amount = UsageAmount.model_validate(
                    {
                        **item,
                        "job_id": None,
                        "execution_epoch": None,
                        "source": "sanitized-canonical-event",
                    }
                )
                if amount.model is not None and amount.model not in approved_models:
                    raise ExportDisclosureError("Unapproved projected usage model")
                _identifier(amount.producer)
                if amount.model is not None:
                    _model(amount.model)
                for part in item.values():
                    if isinstance(part, str):
                        _string(part)
        else:
            if not isinstance(value, str):
                raise ExportDisclosureError("Invalid projected metadata")
            if key in {"integration", "provider"}:
                _identifier(value)
            elif key in _VERSIONS:
                _version(value)
            elif key == "model":
                _model(value)
            else:
                _string(value)
    if "model" in projection and projection["model"] not in approved_models:
        raise ExportDisclosureError("Unapproved projected model")
    if "outcome" in projection and projection["outcome"] not in {"succeeded", "failed"}:
        raise ExportDisclosureError("Unapproved projected outcome")
    restored: dict[str, object] = json.loads(encoded)
    return restored
