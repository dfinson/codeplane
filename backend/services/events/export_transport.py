"""Exact-bound optional OTLP transport over the qualified SDK wire encoders."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from concurrent.futures import CancelledError as FutureCancelledError
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

import httpx

from backend.services.adapters.contracts import AdapterReadiness
from backend.services.events.export_status import ExportDropReason

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from concurrent.futures import Future

    from backend.services.events.export_binding import ExportDestination

BACKOFF = (1.0, 2.0, 4.0, 8.0)
REQUEST_TIMEOUT = 5.0
TOTAL_TIMEOUT = 30.0


def export_readiness(protocol: str) -> AdapterReadiness:
    if protocol not in {"http/protobuf", "grpc"}:
        return AdapterReadiness(False, "unsupported_export_protocol")
    packages = ["opentelemetry-sdk", "opentelemetry-exporter-otlp-proto-http"]
    if protocol == "grpc":
        packages = ["opentelemetry-sdk", "opentelemetry-exporter-otlp-proto-grpc"]
    for package in packages:
        try:
            installed = version(package)
        except PackageNotFoundError:
            return AdapterReadiness(False, "missing_export_dependency")
        if installed != "1.45.2":
            return AdapterReadiness(False, "unsupported_export_dependency", installed)
    return AdapterReadiness(True, installed_version="1.45.2")


@dataclass(frozen=True, slots=True)
class Delivery:
    success: bool
    retryable: bool = False
    reason: ExportDropReason | None = None
    retry_after: float | None = None
    rejected: int = 0


def retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            instant = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        seconds = (instant - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds) if math.isfinite(seconds) else None


class BoundedDelivery:
    """Transport retry policy only; it has no execution or approval callback."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        stop: threading.Event | None = None,
    ) -> None:
        self.clock = clock
        self.stop = stop if stop is not None else threading.Event()

    def send(self, request: Callable[[float], Delivery]) -> Delivery:
        deadline = self.clock() + TOTAL_TIMEOUT
        for attempt in range(len(BACKOFF) + 1):
            remaining = deadline - self.clock()
            if self.stop.is_set():
                return Delivery(False, reason=ExportDropReason.shutdown)
            if remaining <= 0:
                return Delivery(False, reason=ExportDropReason.retry_exhausted)
            result = request(min(REQUEST_TIMEOUT, remaining))
            if result.success or not result.retryable:
                return result
            if attempt == len(BACKOFF):
                break
            delay = result.retry_after if result.retry_after is not None else BACKOFF[attempt]
            if delay >= deadline - self.clock():
                break
            if self.stop.wait(delay):
                return Delivery(False, reason=ExportDropReason.shutdown)
        return Delivery(False, reason=ExportDropReason.retry_exhausted)


class OtlpWire:
    def __init__(self, destination: ExportDestination, *, signal: str = "traces") -> None:
        readiness = export_readiness(destination.protocol)
        if not readiness.ready:
            raise RuntimeError(readiness.reason)
        self.destination = destination
        self.signal = signal
        self.delivery = BoundedDelivery()
        self._loop = asyncio.get_running_loop()
        self._pending: set[Future[Delivery]] = set()
        self._pending_lock = threading.Lock()
        self._client: httpx.AsyncClient | None = None
        self._channel: Any = None
        self._stub: Any = None
        if destination.protocol == "http/protobuf":
            if signal != "traces":
                raise ValueError("HTTP binding is a trace-only destination")
            self._client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        else:
            grpc = import_module("grpc")
            url = httpx.URL(destination.endpoint)
            host = f"[{url.host}]" if ":" in url.host else url.host
            authority = f"{host}:{url.port or (443 if url.scheme == 'https' else 80)}"
            options = (("grpc.enable_retries", 0),)
            self._channel = (
                grpc.secure_channel(authority, grpc.ssl_channel_credentials(), options=options)
                if url.scheme == "https"
                else grpc.insecure_channel(authority, options=options)
            )
            service = "trace" if signal == "traces" else "metrics"
            module = import_module(f"opentelemetry.proto.collector.{service}.v1.{service}_service_pb2_grpc")
            stub = module.TraceServiceStub if signal == "traces" else module.MetricsServiceStub
            self._stub = stub(self._channel)

    def export_spans(self, spans: Sequence[Any]) -> Delivery:
        encoder = import_module("opentelemetry.exporter.otlp.proto.common.trace_encoder")
        message = encoder.encode_spans(spans)
        return self._send(message)

    def export_metrics(self, metrics: Any) -> Delivery:
        encoder = import_module("opentelemetry.exporter.otlp.proto.common.metrics_encoder")
        return self._send(encoder.encode_metrics(metrics))

    def _send(self, message: Any) -> Delivery:
        if self.destination.protocol == "http/protobuf":
            body = message.SerializeToString()
            return self.delivery.send(lambda timeout: self._http(body, timeout))
        return self.delivery.send(lambda timeout: self._grpc(message, timeout))

    def _http(self, body: bytes, timeout: float) -> Delivery:
        with self._pending_lock:
            if self.delivery.stop.is_set():
                return Delivery(False, reason=ExportDropReason.shutdown)
            future = asyncio.run_coroutine_threadsafe(self._http_request(body, timeout), self._loop)
            self._pending.add(future)
        try:
            return future.result()
        except FutureCancelledError:
            return Delivery(False, reason=ExportDropReason.shutdown)
        finally:
            with self._pending_lock:
                self._pending.discard(future)

    async def _http_request(self, body: bytes, timeout: float) -> Delivery:
        assert self._client is not None
        try:
            async with (
                asyncio.timeout(timeout),
                self._client.stream(
                    "POST",
                    self.destination.endpoint,
                    content=body,
                    headers={**self.destination.headers, "content-type": "application/x-protobuf"},
                    timeout=timeout,
                ) as response,
            ):
                status = response.status_code
                if status in (401, 403):
                    return Delivery(False, reason=ExportDropReason.authentication)
                if status == 429 or 500 <= status <= 599 or status == 408:
                    return Delivery(False, retryable=True, retry_after=retry_after(response.headers.get("retry-after")))
                if not 200 <= status < 300:
                    return Delivery(False, reason=ExportDropReason.configuration)
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    if len(content) + len(chunk) > 8192:
                        return Delivery(False, reason=ExportDropReason.configuration)
                    content.extend(chunk)
                response_type = import_module(
                    "opentelemetry.proto.collector.trace.v1.trace_service_pb2"
                ).ExportTraceServiceResponse
                try:
                    parsed = response_type.FromString(bytes(content))
                except import_module("google.protobuf.message").DecodeError:
                    return Delivery(False, reason=ExportDropReason.configuration)
                return Delivery(True, rejected=parsed.partial_success.rejected_spans)
        except (httpx.TransportError, TimeoutError):
            return Delivery(False, retryable=True)

    def _grpc(self, message: Any, timeout: float) -> Delivery:
        grpc = import_module("grpc")
        try:
            response = self._stub.Export(message, timeout=timeout, metadata=tuple(self.destination.headers.items()))
            rejected = (
                response.partial_success.rejected_spans
                if self.signal == "traces"
                else response.partial_success.rejected_data_points
            )
            return Delivery(True, rejected=rejected)
        except grpc.RpcError as exc:
            if self.delivery.stop.is_set():
                return Delivery(False, reason=ExportDropReason.shutdown)
            if exc.code() in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED):
                return Delivery(False, reason=ExportDropReason.authentication)
            if exc.code() in (
                grpc.StatusCode.UNAVAILABLE,
                grpc.StatusCode.DEADLINE_EXCEEDED,
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                grpc.StatusCode.ABORTED,
                grpc.StatusCode.INTERNAL,
                grpc.StatusCode.UNKNOWN,
            ):
                return Delivery(False, retryable=True)
            return Delivery(False, reason=ExportDropReason.configuration)

    def shutdown(self) -> None:
        self.delivery.stop.set()
        with self._pending_lock:
            for future in tuple(self._pending):
                future.cancel()
        if self._channel is not None:
            self._channel.close()

    async def aclose(self) -> None:
        self.shutdown()
        if self._client is not None:
            await self._client.aclose()
