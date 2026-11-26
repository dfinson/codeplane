"""Optional inbound protobuf codec, independent of telemetry/export SDKs."""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

from backend.services.adapters.contracts import AdapterReadiness
from backend.services.ingest.mapping import canonical_record
from backend.services.ingest.otel_decoder import decode_json_body

QUALIFIED_CODEC = (("opentelemetry-proto", "1.45.2"), ("protobuf", "7.35.0"))


class OtlpCodecUnavailableError(RuntimeError):
    pass


def protobuf_readiness() -> AdapterReadiness:
    installed = []
    for package, qualified in QUALIFIED_CODEC:
        try:
            found = version(package)
        except PackageNotFoundError:
            return AdapterReadiness(False, f"Missing optional OTLP codec dependency: {package}")
        installed.append(found)
        if found != qualified:
            return AdapterReadiness(False, f"Unsupported {package}; qualified codec release is {qualified}", found)
    return AdapterReadiness(True, installed_version="/".join(installed))


def protobuf_to_json(body: bytes) -> bytes:
    if len(body) > 1024 * 1024:
        raise ValueError("Ingest body exceeds 1 MiB")
    readiness = protobuf_readiness()
    if not readiness.ready:
        raise OtlpCodecUnavailableError(readiness.reason)
    codec = import_module("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
    json_format = import_module("google.protobuf.json_format")
    decode_error = import_module("google.protobuf.message").DecodeError
    message = codec.ExportTraceServiceRequest()
    try:
        message.ParseFromString(body)
    except decode_error as exc:
        raise ValueError("Malformed OTLP protobuf trace request") from exc
    count = sum(len(scope.spans) for resource in message.resource_spans for scope in resource.scope_spans)
    if count > 1000:
        raise ValueError("Ingest batch exceeds 1000 spans")
    document = json_format.MessageToDict(message)
    resources = document.setdefault("resourceSpans", [])
    for native_resource, resource in zip(message.resource_spans, resources, strict=True):
        for native_scope, scope in zip(native_resource.scope_spans, resource.get("scopeSpans", []), strict=True):
            for native_span, span in zip(native_scope.spans, scope.get("spans", []), strict=True):
                # OTLP JSON explicitly uses hex here, unlike protobuf JSON's
                # generic base64 serialization of bytes fields.
                span["traceId"] = native_span.trace_id.hex()
                span["spanId"] = native_span.span_id.hex()
                if native_span.parent_span_id:
                    span["parentSpanId"] = native_span.parent_span_id.hex()
                for native_link, link in zip(native_span.links, span.get("links", []), strict=True):
                    link["traceId"] = native_link.trace_id.hex()
                    link["spanId"] = native_link.span_id.hex()
    encoded = canonical_record(document)
    decode_json_body(encoded)
    return encoded
