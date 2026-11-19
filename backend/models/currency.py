"""Nullable monetary arithmetic for read projections, never native-unit conversion."""

import json
import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import structlog
from pydantic import ValidationError

from backend.models.usage import UsageAmount

log = structlog.get_logger()
_NUMBER = re.compile(r" *-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)? *\Z")


def qualified_usage(entries: Iterable[object]) -> tuple[UsageAmount, ...]:
    """Qualify one source event/completion, deduplicating only its measurement keys.

    Wire booleans and complete numeric strings are strict even though the shared
    UsageAmount model permits coercion for other callers. Contradictory copies
    make this event unqualified, not an arbitrary first/last-wins total.
    """
    unique: dict[tuple[object, ...], UsageAmount] = {}
    try:
        for entry in entries:
            raw = entry.model_dump(mode="json") if isinstance(entry, UsageAmount) else entry
            if not isinstance(raw, dict):
                raise ValueError("invalid_measurement")
            for name in ("producer", "source"):
                value = raw.get(name)
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)
                ):
                    raise ValueError("invalid_source")
            if type(raw.get("verified", False)) is not bool:
                raise ValueError("invalid_verified_flag")
            amount = raw.get("amount")
            if type(amount) not in (str, int, float) or not _NUMBER.fullmatch(str(amount)):
                raise ValueError("invalid_scalar_amount")
            usage = UsageAmount.model_validate(raw)
            if usage.amount >= Decimal("1e308") or (usage.amount != 0 and float(usage.amount) == 0):
                raise ValueError("unrepresentable_amount")
            key = (
                usage.unit,
                usage.role,
                usage.producer,
                usage.source,
                usage.model,
                usage.job_id,
                usage.execution_epoch,
            )
            previous = unique.get(key)
            if previous is not None and previous != usage:
                raise ValueError("conflicting_measurement")
            unique[key] = usage
    except (ValueError, ValidationError, TypeError):
        log.warning("currency_usage_unqualified", reason="invalid_or_conflicting_source_measurement")
        return ()
    return tuple(unique.values())


def event_usage(
    kind: str, job_id: str | None, payload: dict[str, Any], metadata: dict[str, Any]
) -> tuple[UsageAmount, ...]:
    """The shared source/provenance boundary for SQL and Python read projections."""
    namespace = metadata.get("namespace") or []
    if not isinstance(namespace, (list, tuple)):
        log.warning("currency_usage_unqualified", reason="invalid_source_namespace")
        return ()
    namespace = list(namespace)
    producer = metadata.get("source_framework")
    if not isinstance(producer, str):
        return ()
    if kind == "telemetry.usage":
        if producer not in {"copilot", "claude"} or namespace[:1] == ["observation-source"]:
            return ()
        entries = payload.get("native_usage", [])
    else:
        expected = {"execution.native.usage": "native-execution", "utility.completion.completed": "utility"}.get(kind)
        if expected is None or producer != "codeplane" or list(namespace) != ["control", expected]:
            return ()
        entries = payload.get("usage", [])
    if not isinstance(entries, list):
        log.warning("currency_usage_unqualified", reason="invalid_measurement_array")
        return ()
    usage = qualified_usage(entries)
    if any(
        item.job_id != job_id
        or item.execution_epoch != metadata.get("execution_epoch")
        or (kind == "telemetry.usage" and item.producer != producer)
        for item in usage
    ):
        log.warning("currency_usage_unqualified", reason="source_identity_mismatch")
        return ()
    return usage


def event_usage_json(kind: str, job_id: str | None, payload_json: str, metadata_json: str | None) -> str:
    """Deterministic SQLite read function, using exactly the Python qualifier."""
    try:
        payload = json.loads(payload_json)
        metadata = json.loads(metadata_json or "{}")
        if not isinstance(payload, dict) or not isinstance(metadata, dict):
            raise ValueError("invalid_event_envelope")
    except (ValueError, TypeError):
        log.warning("currency_usage_unqualified", reason="invalid_event_envelope")
        return "[]"
    return json.dumps([item.model_dump(mode="json") for item in event_usage(kind, job_id, payload, metadata)])


def span_usd(job_id: str, session_kind: str, attrs_json: str) -> float | None:
    """Revalidate retained span evidence; a legacy provenance label alone is insufficient."""
    try:
        attrs = json.loads(attrs_json)
        if not isinstance(attrs, dict):
            raise ValueError("invalid_span_evidence")
        entries = attrs.get("native_usage", [])
        if not isinstance(entries, list):
            raise ValueError("invalid_span_usage")
    except (ValueError, TypeError):
        log.warning("currency_usage_unqualified", reason="invalid_span_evidence")
        return None
    usage = qualified_usage(entries)
    if any(
        item.job_id != job_id
        or ("job" if item.role == "primary" else "preflight" if item.role == "scout" else item.role) != session_kind
        for item in usage
    ):
        log.warning("currency_usage_unqualified", reason="span_identity_mismatch")
        return None
    amount = verified_usd(usage)
    return float(amount) if amount is not None else None


def verified_usd(usage: Iterable[UsageAmount]) -> Decimal | None:
    amounts = [
        item.amount
        for item in qualified_usage(usage)
        if item.unit == "USD" and item.verified and item.currency == "USD"
    ]
    return sum(amounts, Decimal(0)) if amounts else None


def optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float, Decimal, str)) else None


def sum_known(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def divide_known(value: float | None, divisor: float | int) -> float | None:
    return value / divisor if value is not None and divisor > 0 else None
