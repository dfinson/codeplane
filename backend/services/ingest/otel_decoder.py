"""Bounded inbound OTLP JSON and simplified GenAI spans, independent of export."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.models.session_event import EventMetadata, SessionEvent
from backend.services.ingest.mapping import FrameworkMapping, canonical_record

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _bounded(value: Any) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 10_000 or depth > 32:
            raise ValueError("Ingest object exceeds structural decoder bounds")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def decode_json_body(body: bytes) -> Any:
    if len(body) > 1024 * 1024:
        raise ValueError("Ingest body exceeds 1 MiB")

    def invalid_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON constant: {value}")

    try:
        result = json.loads(body.decode("utf-8", errors="strict"), parse_constant=invalid_constant)
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed ingest JSON") from exc
    _bounded(result)
    return result


def _attribute_value(value: Any) -> Any:
    if not isinstance(value, dict):
        raise ValueError("OTLP attribute value must be an object")
    if len(value) != 1:
        raise ValueError("OTLP attribute value must select one typed variant")
    key, item = next(iter(value.items()))
    if key in {"stringValue", "bytesValue"} and isinstance(item, str):
        return item
    if key == "boolValue" and isinstance(item, bool):
        return item
    if key == "intValue" and isinstance(item, (str, int)) and not isinstance(item, bool):
        return int(item)
    if key == "doubleValue" and isinstance(item, (float, int)) and not isinstance(item, bool):
        return item
    if key == "arrayValue" and isinstance(item, dict) and isinstance(item.get("values", []), list):
        return [_attribute_value(child) for child in item.get("values", [])]
    if key == "kvlistValue" and isinstance(item, dict):
        return attributes(item.get("values", []))
    raise ValueError("Unsupported OTLP attribute value")


def attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, list):
        raise ValueError("Span attributes must be an object or OTLP key/value list")
    result = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or "value" not in item:
            raise ValueError("Malformed OTLP attribute")
        if item["key"] in result:
            raise ValueError("Duplicate OTLP attribute key")
        result[item["key"]] = _attribute_value(item["value"])
    return result


def otlp_spans(body: bytes) -> tuple[dict[str, Any], ...]:
    document = decode_json_body(body)
    if not isinstance(document, dict):
        raise ValueError("OTLP request must be an object")
    resources = document.get("resourceSpans", document.get("resource_spans"))
    if not isinstance(resources, list):
        raise ValueError("OTLP request requires resourceSpans")
    output = []
    output_bytes = 0
    for resource in resources:
        if not isinstance(resource, dict):
            raise ValueError("Malformed resourceSpans")
        resource_fields = resource.get("resource", {})
        if not isinstance(resource_fields, dict):
            raise ValueError("Malformed OTLP resource")
        resource_attributes = attributes(resource_fields.get("attributes", {}))
        scopes = resource.get("scopeSpans", resource.get("scope_spans", []))
        if not isinstance(scopes, list):
            raise ValueError("Malformed scopeSpans")
        for scope in scopes:
            if not isinstance(scope, dict) or not isinstance(scope.get("spans", []), list):
                raise ValueError("Malformed scope span list")
            for span in scope.get("spans", []):
                if not isinstance(span, dict):
                    raise ValueError("Span must be an object")
                normalized = {**span, "attributes": {**resource_attributes, **attributes(span.get("attributes", {}))}}
                output_bytes += len(canonical_record(normalized))
                if output_bytes > 8 * 1024 * 1024:
                    raise ValueError("OTLP request exceeds the normalized span resource budget")
                output.append(normalized)
                if len(output) > 1000:
                    raise ValueError("Ingest batch exceeds 1000 spans")
    return tuple(output)


def _nanoseconds(span: dict[str, Any], *keys: str) -> int | None:
    value = next((span[key] for key in keys if key in span), None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Span time must be integer nanoseconds")
    result = int(value)
    if result < 0:
        raise ValueError("Span time cannot be negative")
    return result


def span_event(
    span: dict[str, Any],
    *,
    job_id: str,
    binding_id: str,
    source: str,
    event_id: str,
    fallback_timestamp: datetime,
    mapping: FrameworkMapping | None = None,
) -> SessionEvent:
    _bounded(span)
    if len(canonical_record(span)) > 1024 * 1024:
        raise ValueError("Span exceeds 1 MiB")
    attrs = attributes(span.get("attributes", {}))
    trace = span.get("traceId", span.get("trace_id"))
    identity = span.get("spanId", span.get("span_id"))
    if any(value is not None and (not isinstance(value, str) or not value) for value in (trace, identity)):
        raise ValueError("Supplied trace/span identities must be nonempty strings")
    name = span.get("name")
    if not isinstance(name, str):
        raise ValueError("Span name must be a string")
    start = _nanoseconds(span, "startTimeUnixNano", "start_time_unix_nano", "start_time")
    end = _nanoseconds(span, "endTimeUnixNano", "end_time_unix_nano", "end_time")
    if start is not None and end is not None and end < start:
        raise ValueError("Span completion predates its start")
    instant = end if end is not None else start
    timestamp = _EPOCH + timedelta(microseconds=instant // 1000) if instant is not None else fallback_timestamp
    spec = mapping.spans.get(name) if mapping is not None else None
    payload = {field: attrs[key] for field, key in spec.attributes.items() if key in attrs} if spec else {}
    tool = attrs.get("gen_ai.tool.name")
    operation = attrs.get("gen_ai.operation.name")
    is_tool = bool(tool) or operation == "execute_tool"
    is_llm = operation in {"chat", "text_completion", "generate", "embeddings"}
    kind = (
        spec.kind
        if spec
        else "raw"
        if mapping is not None
        else "tool.call.completed"
        if is_tool and end is not None
        else "tool.call.started"
        if is_tool
        else "llm.call.completed"
        if is_llm and end is not None
        else "llm.call.started"
        if is_llm
        else "raw"
    )
    if mapping is None:
        payload["span_name"] = name
    elif spec is None:
        payload["original_type"] = name
    status = span.get("status", {})
    if not isinstance(status, dict):
        raise ValueError("Span status must be an object")
    if status.get("code", status.get("status_code")) in (2, "STATUS_CODE_ERROR"):
        kind = "session.error" if mapping is not None else "tool.call.failed" if tool else "llm.call.failed"
        payload["message"] = status.get("message", "Unknown error")
    if start is not None and end is not None:
        payload["duration_ms"] = (end - start) / 1_000_000
    if operation is not None:
        payload["operation"] = operation
    if tool is not None:
        payload["tool_name"] = tool
        tool_id = attrs.get("gen_ai.tool.call.id")
        if isinstance(tool_id, str) and tool_id:
            payload["tool_call_id"] = tool_id
    for field, key in (
        ("arguments", "gen_ai.tool.call.arguments"),
        ("result", "gen_ai.tool.call.result"),
        ("tool_description", "gen_ai.tool.description"),
        ("system", "gen_ai.system"),
        ("requested_model", "gen_ai.request.model"),
    ):
        if key in attrs:
            payload[field] = attrs[key]
    if is_tool:
        payload["_codeplane_observation"] = {
            "blocking": False,
            "missing_tool_arguments": "gen_ai.tool.call.arguments" not in attrs,
        }
    for field, key in (
        ("model", "gen_ai.response.model"),
        ("input_tokens", "gen_ai.usage.input_tokens"),
        ("output_tokens", "gen_ai.usage.output_tokens"),
    ):
        if key in attrs:
            payload[field] = attrs[key]
    parent = span.get("parentSpanId", span.get("parent_span_id"))
    return SessionEvent(
        id=event_id,
        kind=kind,
        session_id=job_id,
        timestamp=timestamp,
        payload=payload,
        raw_event=span if mapping is not None else None,
        metadata=EventMetadata(
            source_framework=source,
            ingestion_mode="stream",
            integration_binding_id=binding_id,
            span_id=f"{binding_id}:{trace}:{identity}" if trace and identity else None,
            parent_id=f"{binding_id}:{trace}:{parent}" if trace and isinstance(parent, str) and parent else None,
            correlation_id=f"{binding_id}:{trace}" if trace else None,
            raw_kind=name,
            duration_ms=(end - start) / 1_000_000 if start is not None and end is not None else None,
        ),
    )
