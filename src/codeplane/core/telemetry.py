"""OpenTelemetry telemetry module for CodePlane.

Provides tracing, metrics, and instrumentation with lazy initialization.
Only activates when:
- OTEL_EXPORTER_OTLP_ENDPOINT env var is set, OR
- telemetry.enabled=true in CodePlane config

Usage:
    from codeplane.core.telemetry import init_telemetry, traced, get_tracer, get_meter

    # Initialize at startup (typically in daemon entry point)
    init_telemetry(config)

    # Decorate functions for automatic tracing
    @traced("my_operation")
    def my_function():
        ...

    # Or use tracer directly
    tracer = get_tracer()
    with tracer.start_as_current_span("my_span"):
        ...

    # Metrics
    meter = get_meter()
    counter = meter.create_counter("my_counter")
    counter.add(1)

    # Cleanup at shutdown
    shutdown_telemetry()
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

if TYPE_CHECKING:
    from codeplane.config.models import TelemetryConfig

logger = logging.getLogger(__name__)

# Type variables for decorator
P = ParamSpec("P")
R = TypeVar("R")

# Global state - None means not initialized, will use no-ops
_tracer: Any = None
_meter: Any = None
_tracer_provider: Any = None
_meter_provider: Any = None
_initialized: bool = False

# Track whether OpenTelemetry is available
_otel_available: bool = False

# Try to import OpenTelemetry - gracefully handle if not installed
try:
    from opentelemetry import metrics as otel_metrics
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _otel_available = True
except ImportError:
    logger.debug("OpenTelemetry SDK not installed - telemetry will be disabled")
    _otel_available = False


def _is_telemetry_enabled(config: TelemetryConfig | None) -> bool:
    """Check if telemetry should be enabled based on env vars and config.

    Telemetry is enabled if:
    1. OTEL_EXPORTER_OTLP_ENDPOINT env var is set, OR
    2. config.telemetry.enabled is True
    """
    # Check standard OTEL env var first
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return True

    # Check config if provided
    return bool(config is not None and config.enabled)


def _get_otlp_endpoint(config: TelemetryConfig | None) -> str | None:
    """Get OTLP endpoint from env var or config."""
    # Standard OTEL env var takes precedence
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        return endpoint

    # Fall back to config
    if config is not None and config.otlp_endpoint:
        return config.otlp_endpoint

    return None


def _get_service_name(config: TelemetryConfig | None) -> str:
    """Get service name from env var or config."""
    # Standard OTEL env var takes precedence
    service_name = os.environ.get("OTEL_SERVICE_NAME")
    if service_name:
        return service_name

    # Fall back to config
    if config is not None:
        return config.service_name

    return "codeplane"


def init_telemetry(config: TelemetryConfig | None = None) -> bool:
    """Initialize OpenTelemetry tracing and metrics.

    Args:
        config: TelemetryConfig from CodePlane config. If None, only env vars are checked.

    Returns:
        True if telemetry was initialized, False if disabled or unavailable.

    This function is idempotent - calling it multiple times has no effect after
    the first successful initialization.
    """
    global _tracer, _meter, _tracer_provider, _meter_provider, _initialized

    if _initialized:
        logger.debug("Telemetry already initialized")
        return _tracer is not None

    _initialized = True

    if not _otel_available:
        logger.info("OpenTelemetry not available - telemetry disabled")
        return False

    if not _is_telemetry_enabled(config):
        logger.debug(
            "Telemetry not enabled (set OTEL_EXPORTER_OTLP_ENDPOINT or telemetry.enabled=true)"
        )
        return False

    endpoint = _get_otlp_endpoint(config)
    if not endpoint:
        logger.warning("Telemetry enabled but no OTLP endpoint configured - telemetry disabled")
        return False

    service_name = _get_service_name(config)

    try:
        # Import OTLP exporters (may fail if grpc extras not installed)
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        # Create resource with service info
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": _get_version(),
            }
        )

        # Set up tracing
        _tracer_provider = TracerProvider(resource=resource)
        span_exporter = OTLPSpanExporter(
            endpoint=endpoint, insecure=_is_insecure_endpoint(endpoint)
        )
        _tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        otel_trace.set_tracer_provider(_tracer_provider)
        _tracer = _tracer_provider.get_tracer("codeplane")

        # Set up metrics
        metric_exporter = OTLPMetricExporter(
            endpoint=endpoint, insecure=_is_insecure_endpoint(endpoint)
        )
        metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60000)
        _meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        otel_metrics.set_meter_provider(_meter_provider)
        _meter = _meter_provider.get_meter("codeplane")

        # Auto-instrument SQLAlchemy if available
        _instrument_sqlalchemy()

        logger.info(f"Telemetry initialized: endpoint={endpoint}, service={service_name}")
        return True

    except ImportError as e:
        logger.warning(
            f"Failed to import OTLP exporters (install opentelemetry-exporter-otlp-proto-grpc): {e}"
        )
        return False
    except Exception as e:
        logger.error(f"Failed to initialize telemetry: {e}")
        return False


def _get_version() -> str:
    """Get CodePlane version for resource attributes."""
    try:
        from importlib.metadata import version

        return version("codeplane")
    except Exception:
        return "unknown"


def _is_insecure_endpoint(endpoint: str) -> bool:
    """Determine if endpoint should use insecure connection."""
    return endpoint.startswith("http://")


def _instrument_sqlalchemy() -> None:
    """Auto-instrument SQLAlchemy if the instrumentation package is available."""
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
        logger.debug("SQLAlchemy auto-instrumentation enabled")
    except ImportError:
        logger.debug(
            "SQLAlchemy instrumentation not available (install opentelemetry-instrumentation-sqlalchemy)"
        )
    except Exception as e:
        logger.warning(f"Failed to instrument SQLAlchemy: {e}")


def shutdown_telemetry() -> None:
    """Shutdown telemetry providers and flush pending data.

    Call this during application shutdown to ensure all spans and metrics
    are exported before the process exits.
    """
    global _tracer, _meter, _tracer_provider, _meter_provider, _initialized

    if not _initialized:
        return

    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
            logger.debug("Tracer provider shut down")
        except Exception as e:
            logger.warning(f"Error shutting down tracer provider: {e}")

    if _meter_provider is not None:
        try:
            _meter_provider.shutdown()
            logger.debug("Meter provider shut down")
        except Exception as e:
            logger.warning(f"Error shutting down meter provider: {e}")

    _tracer = None
    _meter = None
    _tracer_provider = None
    _meter_provider = None
    _initialized = False


class _NoOpSpan:
    """No-op span for when telemetry is disabled."""

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def is_recording(self) -> bool:
        return False


class _NoOpTracer:
    """No-op tracer for when telemetry is disabled."""

    def start_span(self, _name: str, **_kwargs: Any) -> _NoOpSpan:
        return _NoOpSpan()

    def start_as_current_span(self, _name: str, **_kwargs: Any) -> _NoOpSpan:
        return _NoOpSpan()

    @contextmanager
    def start_as_current_span_cm(self, _name: str, **_kwargs: Any) -> Any:
        """Context manager version of start_as_current_span."""
        yield _NoOpSpan()


class _NoOpMeter:
    """No-op meter for when telemetry is disabled."""

    def create_counter(self, _name: str, **_kwargs: Any) -> _NoOpCounter:
        return _NoOpCounter()

    def create_up_down_counter(self, _name: str, **_kwargs: Any) -> _NoOpCounter:
        return _NoOpCounter()

    def create_histogram(self, _name: str, **_kwargs: Any) -> _NoOpHistogram:
        return _NoOpHistogram()

    def create_observable_gauge(
        self, _name: str, _callbacks: Any = None, **_kwargs: Any
    ) -> _NoOpObservable:
        return _NoOpObservable()

    def create_observable_counter(
        self, _name: str, _callbacks: Any = None, **_kwargs: Any
    ) -> _NoOpObservable:
        return _NoOpObservable()

    def create_observable_up_down_counter(
        self, _name: str, _callbacks: Any = None, **_kwargs: Any
    ) -> _NoOpObservable:
        return _NoOpObservable()


class _NoOpCounter:
    """No-op counter instrument."""

    def add(self, amount: int | float, attributes: dict[str, Any] | None = None) -> None:
        pass


class _NoOpHistogram:
    """No-op histogram instrument."""

    def record(self, amount: int | float, attributes: dict[str, Any] | None = None) -> None:
        pass


class _NoOpObservable:
    """No-op observable instrument."""

    pass


# Singleton no-op instances
_noop_tracer = _NoOpTracer()
_noop_meter = _NoOpMeter()


def get_tracer() -> Any:
    """Get the global tracer instance.

    Returns the real tracer if telemetry is initialized and enabled,
    otherwise returns a no-op tracer that silently ignores all operations.

    Returns:
        Tracer instance (real or no-op).
    """
    if _tracer is not None:
        return _tracer
    return _noop_tracer


def get_meter() -> Any:
    """Get the global meter instance.

    Returns the real meter if telemetry is initialized and enabled,
    otherwise returns a no-op meter that silently ignores all operations.

    Returns:
        Meter instance (real or no-op).
    """
    if _meter is not None:
        return _meter
    return _noop_meter


def traced(
    name: str | None = None,
    *,
    attributes: dict[str, Any] | None = None,
    record_exception: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace function execution.

    Works with both sync and async functions. Creates a span for each
    function invocation with the function name (or custom name) as the span name.

    Args:
        name: Custom span name. Defaults to function's qualified name.
        attributes: Static attributes to add to every span.
        record_exception: Whether to record exceptions on the span. Defaults to True.

    Returns:
        Decorated function.

    Example:
        @traced("process_file")
        def process_file(path: str) -> None:
            ...

        @traced()  # Uses function name
        async def async_operation() -> None:
            ...
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        span_name = name or f"{func.__module__}.{func.__qualname__}"
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                tracer = get_tracer()

                # Handle no-op tracer
                if isinstance(tracer, _NoOpTracer):
                    return await func(*args, **kwargs)  # type: ignore[misc, no-any-return]

                with tracer.start_as_current_span(span_name) as span:
                    if attributes:
                        span.set_attributes(attributes)
                    try:
                        return await func(*args, **kwargs)  # type: ignore[misc, no-any-return]
                    except Exception as e:
                        if record_exception:
                            span.record_exception(e)
                            _set_error_status(span)
                        raise

            return async_wrapper  # type: ignore[return-value]
        else:

            @functools.wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                tracer = get_tracer()

                # Handle no-op tracer
                if isinstance(tracer, _NoOpTracer):
                    return func(*args, **kwargs)

                with tracer.start_as_current_span(span_name) as span:
                    if attributes:
                        span.set_attributes(attributes)
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        if record_exception:
                            span.record_exception(e)
                            _set_error_status(span)
                        raise

            return sync_wrapper

    return decorator


def _set_error_status(span: Any) -> None:
    """Set span status to error."""
    if not _otel_available:
        return

    try:
        from opentelemetry.trace import StatusCode

        span.set_status(otel_trace.Status(StatusCode.ERROR))
    except Exception:
        pass  # Ignore errors in error handling


@contextmanager
def span_context(
    name: str,
    attributes: dict[str, Any] | None = None,
    record_exception: bool = True,
) -> Any:
    """Context manager for creating a span.

    Provides a convenient way to create spans without the decorator pattern.

    Args:
        name: Span name.
        attributes: Attributes to add to the span.
        record_exception: Whether to record exceptions. Defaults to True.

    Yields:
        The span (or no-op span if telemetry is disabled).

    Example:
        with span_context("my_operation", {"key": "value"}) as span:
            span.set_attribute("result", result)
            ...
    """
    tracer = get_tracer()

    # Handle no-op tracer
    if isinstance(tracer, _NoOpTracer):
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(name) as span:
        if attributes:
            span.set_attributes(attributes)
        try:
            yield span
        except Exception as e:
            if record_exception:
                span.record_exception(e)
                _set_error_status(span)
            raise


def is_telemetry_enabled() -> bool:
    """Check if telemetry is currently active.

    Returns:
        True if telemetry was successfully initialized and is active.
    """
    return _tracer is not None


def is_otel_available() -> bool:
    """Check if OpenTelemetry SDK is installed.

    Returns:
        True if OpenTelemetry packages are available for import.
    """
    return _otel_available
