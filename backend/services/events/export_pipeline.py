"""Optional SDK batching of sanitized, already committed canonical evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from typing import TYPE_CHECKING, Any

import structlog

from backend.services.events.export_metadata import ExportDisclosureError, export_metadata, validate_projection
from backend.services.events.export_status import ExportDropReason, ExportHealth
from backend.services.events.export_transport import OtlpWire

if TYPE_CHECKING:
    from collections import deque
    from collections.abc import Callable, Sequence

    from backend.models.session_event import SessionEvent
    from backend.models.usage import UsageAmount
    from backend.services.events.export_binding import ExportBinding

    class _BatchProcessor:
        _queue: deque[Any]
        _worker_thread: threading.Thread
        _worker_awaken: threading.Event
        _shutdown: bool
        _shutdown_timeout_exceeded: bool

        def __init__(self, *args: Any) -> None: ...
        def worker(self) -> None: ...
        def emit(self, value: Any) -> None: ...
        def shutdown(self, timeout_millis: int) -> None: ...


log = structlog.get_logger()
MAX_QUEUE = 2048
BATCH_SIZE = 128
SCHEDULE_MS = 5000
SHUTDOWN_MS = 2000


@dataclass(frozen=True, slots=True)
class ExportAdmission:
    status: str
    reason: ExportDropReason | None = None


class _QueueMetrics:
    def __init__(self, health: ExportHealth) -> None:
        self.health = health

    def register_queue_size(self, probe: Callable[[], int]) -> None:
        self.health.set_queue_probe(probe)

    def finish_items(self, count: int) -> None:
        pass

    def drop_items(self, count: int, error_type: str = "queue_full") -> None:
        reason = ExportDropReason.shutdown if error_type == "already_shutdown" else ExportDropReason.queue_full
        self.health.record_drop(reason, count)


class MetadataExportPipeline:
    """Owned by the existing telemetry owner, not another provider or execution queue."""

    def __init__(self, binding: ExportBinding) -> None:
        self.binding = binding
        self.health = ExportHealth(enabled=True)
        self._lock = threading.RLock()
        self._active = True
        self._accepting = True
        self._stop_reason = ExportDropReason.shutdown
        self._wire = OtlpWire(binding.trace)
        try:
            self._metrics_wire = OtlpWire(binding.metrics, signal="metrics") if binding.metrics is not None else None
        except Exception:
            self._wire.shutdown()
            raise
        sdk = import_module("opentelemetry.sdk._shared_internal")
        if not TYPE_CHECKING:
            _BatchProcessor = sdk.BatchProcessor  # noqa: N806 - qualified SDK base.
        owner = self

        class Processor(_BatchProcessor):
            def worker(self) -> None:
                try:
                    super().worker()
                finally:
                    with owner._lock:
                        remaining = len(self._queue)
                        self._queue.clear()
                        if remaining:
                            owner.health.record_drop(owner._stop_reason, remaining)

        self._processor = Processor(self, SCHEDULE_MS, BATCH_SIZE, 5000, MAX_QUEUE, "Span", _QueueMetrics(self.health))

    def enqueue(
        self,
        event: SessionEvent,
        *,
        integration: str,
        provider: str,
        role: str,
        usage: Sequence[UsageAmount] = (),
    ) -> ExportAdmission:
        with self._lock:
            if not self._active or not self._accepting or not self.health.snapshot().enabled:
                self.health.record_drop(ExportDropReason.disabled)
                return ExportAdmission("disabled", ExportDropReason.disabled)
            try:
                projection = export_metadata(
                    event,
                    identity_key=self.binding.identity_key,
                    integration=integration,
                    provider=provider,
                    role=role,
                    approved_models=self.binding.approved_models,
                    usage=usage,
                )
                span = self._span(event.timestamp, projection)
            except (ExportDisclosureError, ValueError, TypeError, OverflowError):
                self.health.record_drop(ExportDropReason.metadata_withheld)
                log.warning("optional_export_withheld", reason=ExportDropReason.metadata_withheld.value)
                return ExportAdmission("withheld", ExportDropReason.metadata_withheld)
            except Exception:
                self._fail(ExportDropReason.configuration, 1)
                return ExportAdmission("disabled", ExportDropReason.configuration)
            self._processor.emit(span)
            return ExportAdmission("queued")

    def enqueue_projection(self, projection: dict[str, object]) -> ExportAdmission:
        with self._lock:
            if not self._active or not self._accepting or not self.health.snapshot().enabled:
                self.health.record_drop(ExportDropReason.disabled)
                return ExportAdmission("disabled", ExportDropReason.disabled)
            try:
                safe = validate_projection(projection, self.binding.approved_models)
                span = self._span(datetime.now(UTC), safe)
            except (ValueError, TypeError, OverflowError):
                self.health.record_drop(ExportDropReason.metadata_withheld)
                return ExportAdmission("withheld", ExportDropReason.metadata_withheld)
            except Exception:
                self._fail(ExportDropReason.configuration, 1)
                return ExportAdmission("disabled", ExportDropReason.configuration)
            self._processor.emit(span)
            return ExportAdmission("queued")

    @staticmethod
    def _attributes(projection: dict[str, object]) -> dict[str, Any]:
        attrs = {}
        for name, value in projection.items():
            if name == "usage":
                assert isinstance(value, list)
                for index, amount in enumerate(value):
                    for field, item in amount.items():
                        if item is not None:
                            attrs[f"codeplane.usage.{index}.{field}"] = item
            elif value is not None:
                attrs[f"codeplane.{name}"] = value
        return attrs

    def _span(self, timestamp: datetime, projection: dict[str, object]) -> Any:
        trace = import_module("opentelemetry.trace")
        sdk = import_module("opentelemetry.sdk.trace")
        resources = import_module("opentelemetry.sdk.resources")
        scope = import_module("opentelemetry.sdk.util.instrumentation")
        correlation = json.dumps(
            [projection.get(key) for key in ("job_id", "source_id", "execution_id", "source_trace_id")],
            separators=(",", ":"),
        )
        trace_id = int(hashlib.sha256(correlation.encode()).hexdigest()[:32], 16)
        span_id = int(str(projection.get("span_id") or projection["event_id"])[:16], 16)
        if not trace_id or not span_id:
            raise ExportDisclosureError("Invalid projected trace identity")
        context = trace.SpanContext(trace_id, span_id, False, trace.TraceFlags.SAMPLED)
        parent_id = projection.get("parent_span_id")
        parent = (
            trace.SpanContext(trace_id, int(str(parent_id)[:16], 16), False, trace.TraceFlags.SAMPLED)
            if parent_id
            else None
        )
        instant = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp
        end = int(instant.timestamp() * 1_000_000_000)
        duration = projection.get("duration_ms")
        if duration is not None and not isinstance(duration, (int, float)):
            raise ExportDisclosureError("Unsupported duration representation")
        start = end - int(duration * 1_000_000) if duration is not None else end
        if not 0 <= start <= end <= 2**64 - 1:
            raise ExportDisclosureError("Unsupported export timestamp")
        return sdk.ReadableSpan(
            name="codeplane.event",
            context=context,
            parent=parent,
            attributes=self._attributes(projection),
            start_time=start,
            end_time=end,
            resource=resources.Resource({"service.name": "codeplane"}),
            instrumentation_scope=scope.InstrumentationScope("codeplane.metadata", "1"),
        )

    def export(self, batch: list[Any]) -> None:
        with self._lock:
            active = self._active
        if not active or not self.health.snapshot().enabled:
            self.health.record_drop(ExportDropReason.disabled, len(batch))
            return
        try:
            outcome = self._wire.export_spans(batch)
        except Exception:
            self._fail(ExportDropReason.configuration, len(batch))
            return
        if outcome.success and 0 <= outcome.rejected <= len(batch):
            self.health.record_exported(len(batch) - outcome.rejected)
            if outcome.rejected:
                self.health.record_drop(ExportDropReason.metadata_withheld, outcome.rejected)
            if self._metrics_wire is not None:
                self._export_metrics(batch)
        else:
            reason = outcome.reason or ExportDropReason.configuration
            self.health.record_drop(reason, len(batch))
            if reason in (ExportDropReason.authentication, ExportDropReason.configuration):
                self._fail(reason, 0)

    def _export_metrics(self, batch: list[Any]) -> None:
        assert self._metrics_wire is not None
        sdk = import_module("opentelemetry.sdk.metrics.export")
        resource_module = import_module("opentelemetry.sdk.resources")
        scope_module = import_module("opentelemetry.sdk.util.instrumentation")
        metrics = []
        for span in batch:
            attrs = dict(span.attributes)
            usage: dict[str, dict[str, Any]] = {}
            for name, value in attrs.items():
                if name.startswith("codeplane.usage."):
                    _, _, index, field = name.split(".", 3)
                    usage.setdefault(index, {})[field] = value
            for fields in usage.values():
                unit = fields["unit"]
                if unit == "USD" and (fields.get("verified") is not True or fields.get("currency") != "USD"):
                    self.health.record_drop(ExportDropReason.metadata_withheld)
                    continue
                amount = Decimal(fields["amount"])
                if not amount.is_finite() or amount < 0 or amount > Decimal(2**63 - 1):
                    self.health.record_drop(ExportDropReason.metadata_withheld)
                    continue
                numeric = int(amount) if amount == amount.to_integral_value() else float(amount)
                if not math.isfinite(numeric) or (isinstance(numeric, int) and not -(2**63) <= numeric < 2**63):
                    self.health.record_drop(ExportDropReason.metadata_withheld)
                    continue
                attributes = {key: value for key, value in attrs.items() if not key.startswith("codeplane.usage.")}
                attributes.update({f"codeplane.usage.{key}": value for key, value in fields.items() if key != "amount"})
                metrics.append(
                    sdk.Metric(
                        f"codeplane.usage.{unit}",
                        "Source-reported usage per canonical event",
                        unit,
                        sdk.Gauge([sdk.NumberDataPoint(attributes, span.start_time, span.end_time, numeric)]),
                    )
                )
        if not metrics:
            return
        data = sdk.MetricsData(
            [
                sdk.ResourceMetrics(
                    resource_module.Resource({"service.name": "codeplane"}),
                    [sdk.ScopeMetrics(scope_module.InstrumentationScope("codeplane.metadata", "1"), metrics, "")],
                    "",
                )
            ]
        )
        try:
            result = self._metrics_wire.export_metrics(data)
        except Exception:
            self.health.record_drop(ExportDropReason.configuration, len(metrics))
            self._fail(ExportDropReason.configuration, 0)
            return
        if not result.success:
            reason = result.reason or ExportDropReason.configuration
            self.health.record_drop(reason, len(metrics))
            if reason in (ExportDropReason.authentication, ExportDropReason.configuration):
                self._fail(reason, 0)
        elif not 0 <= result.rejected <= len(metrics):
            self._fail(ExportDropReason.configuration, len(metrics))
        elif result.rejected:
            self.health.record_drop(ExportDropReason.metadata_withheld, result.rejected)

    def _fail(self, reason: ExportDropReason, count: int) -> None:
        with self._lock:
            self._active = False
            self._accepting = False
            self._stop_reason = reason
            self.health.record_drop(reason, count, disable=True)
            self._processor._shutdown = True
            self._processor._shutdown_timeout_exceeded = True
            self._processor._worker_awaken.set()
        self._wire.shutdown()
        if self._metrics_wire is not None:
            self._metrics_wire.shutdown()
        log.error("optional_export_disabled", reason=reason.value)

    def shutdown(self, timeout_millis: float = SHUTDOWN_MS) -> None:
        # Called by the SDK processor too; only the owner initiates queue drain.
        self._wire.shutdown()
        if self._metrics_wire is not None:
            self._metrics_wire.shutdown()

    def close(self) -> None:
        with self._lock:
            self._accepting = False
        started = time.monotonic()
        self._processor.shutdown(timeout_millis=SHUTDOWN_MS)
        remaining = max(0.0, 2.0 - (time.monotonic() - started))
        if self._processor._worker_thread is not threading.current_thread():
            self._processor._worker_thread.join(remaining)
        with self._lock:
            self._active = False
        self.health.record_drop(ExportDropReason.shutdown, 0, disable=True)

    def disable(self, reason: ExportDropReason = ExportDropReason.disabled) -> None:
        self._fail(reason, 0)

    async def aclose(self) -> None:
        await asyncio.to_thread(self.close)
        await self._wire.aclose()
        if self._metrics_wire is not None:
            await self._metrics_wire.aclose()
