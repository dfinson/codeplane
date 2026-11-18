"""Stateless OTLP projection using the existing bounded GenAI span decoder."""

from __future__ import annotations

import hashlib
import inspect
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.services.ingest.mapping import DecoderState, canonical_record
from backend.services.ingest.otel_decoder import span_event

if TYPE_CHECKING:
    from datetime import datetime

    from backend.models.session_event import SessionEvent

OTEL_FORMAT = "otlp_json"
OTEL_SOURCE = "otlp-http-json"
_EMPTY_STATE = DecoderState(None, None, (), 0)


def span_receipt_id(binding_id: str, external_session_id: str, span: dict[str, Any]) -> str:
    """Content is included so later, more complete observations are not discarded."""
    digest = hashlib.sha256(canonical_record(span)).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"codeplane-otlp-span-v1:{binding_id}:{external_session_id}:{digest}"))


def validate_wire_identity(span: dict[str, Any]) -> None:
    for key, width in (("traceId", 32), ("spanId", 16)):
        value = span.get(key)
        if not isinstance(value, str) or re.fullmatch(rf"[0-9a-fA-F]{{{width}}}", value) is None or int(value, 16) == 0:
            raise ValueError(f"OTLP {key} must be a nonzero {width}-digit hexadecimal identity")
        span[key] = value.lower()
    parent = span.get("parentSpanId")
    if parent not in (None, ""):
        if not isinstance(parent, str) or re.fullmatch(r"[0-9a-fA-F]{16}", parent) is None:
            raise ValueError("OTLP parentSpanId must be a 16-digit hexadecimal identity")
        span["parentSpanId"] = parent.lower()


class OtelObservationDecoder:
    def __init__(
        self, *, job_id: str, binding_id: str, external_session_id: str, source_key: str, anchor: datetime
    ) -> None:
        self.job_id = job_id
        self.binding_id = binding_id
        self.external_session_id = external_session_id
        self.source_key = source_key
        self.anchor = anchor
        source_file = inspect.getsourcefile(span_event)
        assert source_file is not None
        self.mapping_digest = hashlib.sha256(Path(__file__).read_bytes() + Path(source_file).read_bytes()).hexdigest()

    def checkpoint(self) -> DecoderState:
        return _EMPTY_STATE

    def restore(self, state: DecoderState) -> None:
        if state != _EMPTY_STATE:
            raise ValueError("OTLP observation checkpoint contains unsupported decoder state")

    def decode(self, raw: dict[str, Any], index: int) -> tuple[SessionEvent, ...]:
        if index < 0:
            raise ValueError("Physical source record index cannot be negative")
        digest = hashlib.sha256(canonical_record(raw)).hexdigest()
        occurrence = canonical_record(
            {
                "binding": self.binding_id,
                "job": self.job_id,
                "session": self.external_session_id,
                "source": self.source_key,
                "index": index,
                "record": digest,
            }
        ).decode()
        event = span_event(
            raw,
            job_id=self.job_id,
            binding_id=self.binding_id,
            source="generic-otel",
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"codeplane-otel-observation-v1:{occurrence}")),
            fallback_timestamp=self.anchor,
        )
        has_time = any(
            raw.get(key) is not None
            for key in (
                "startTimeUnixNano",
                "endTimeUnixNano",
                "start_time_unix_nano",
                "end_time_unix_nano",
                "start_time",
                "end_time",
            )
        )
        payload = {
            **event.payload,
            "_codeplane_source": {
                "record_index": index,
                "output_index": 0,
                "source_key": self.source_key,
                "record_sha256": digest,
                "timestamp_fallback": not has_time,
            },
        }
        return (
            event.model_copy(
                update={
                    "payload": payload,
                    "raw_event": raw,
                    "metadata": event.metadata.model_copy(update={"run_id": self.external_session_id}),
                }
            ),
        )
