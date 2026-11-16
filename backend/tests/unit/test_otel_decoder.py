import json
from datetime import UTC, datetime

import pytest

from backend.services.ingest.otel_decoder import otlp_spans, span_event


def test_otlp_structured_attributes_zero_usage_and_namespaced_identity() -> None:
    body = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "gen_ai.system", "value": {"stringValue": "provider"}}]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "chat",
                                "traceId": "trace",
                                "spanId": "span",
                                "parentSpanId": "parent",
                                "startTimeUnixNano": "0",
                                "endTimeUnixNano": "1000000",
                                "attributes": [
                                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "0"}},
                                    {"key": "gen_ai.response.model", "value": {"stringValue": "model"}},
                                    {
                                        "key": "structured",
                                        "value": {
                                            "arrayValue": {
                                                "values": [
                                                    {"stringValue": "one"},
                                                    {"boolValue": True},
                                                ]
                                            }
                                        },
                                    },
                                ],
                            }
                        ]
                    }
                ],
            }
        ],
    }
    span = otlp_spans(json.dumps(body).encode())[0]
    assert span["attributes"]["structured"] == ["one", True]
    event = span_event(
        span,
        job_id="job",
        binding_id="binding",
        source="otel",
        event_id="event",
        fallback_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert event.session_id == "job"
    assert event.metadata.span_id == "binding:trace:span"
    assert event.metadata.parent_id == "binding:trace:parent"
    assert event.metadata.duration_ms == 1
    assert event.payload["input_tokens"] == 0
    assert "cost_usd" not in event.payload
    assert event.timestamp.year == 1970


def test_simplified_span_without_source_ids_never_invents_global_session() -> None:
    event = span_event(
        {"name": "chat", "attributes": {"gen_ai.operation.name": "chat"}, "start_time_unix_nano": 0},
        job_id="job",
        binding_id="binding",
        source="otel",
        event_id="physical-record-id",
        fallback_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert event.session_id == "job"
    assert event.metadata.span_id is None and event.metadata.correlation_id is None
    assert event.kind == "llm.call.started"


@pytest.mark.parametrize(
    "body",
    [
        b'{"resourceSpans":[],"invalid": NaN}',
        b'{"resourceSpans":"bad"}',
        b" " * (1024 * 1024 + 1),
        json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [{}] * 1001}]}]}).encode(),
    ],
)
def test_invalid_or_oversized_whole_otlp_batch_is_rejected(body) -> None:
    with pytest.raises(ValueError):
        otlp_spans(body)
