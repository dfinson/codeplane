"""Tests for core.telemetry module.

Tests the OpenTelemetry telemetry module including no-op fallbacks,
environment variable handling, and the traced decorator.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from codeplane.core.telemetry import (
    _get_otlp_endpoint,
    _get_service_name,
    _is_telemetry_enabled,
    _NoOpCounter,
    _NoOpHistogram,
    _NoOpMeter,
    _NoOpObservable,
    _NoOpSpan,
    _NoOpTracer,
    get_meter,
    get_tracer,
    is_otel_available,
    is_telemetry_enabled,
    shutdown_telemetry,
    span_context,
    traced,
)

# =============================================================================
# Mock TelemetryConfig for testing
# =============================================================================


class MockTelemetryConfig:
    """Mock TelemetryConfig for testing."""

    def __init__(
        self,
        enabled: bool = False,
        otlp_endpoint: str | None = None,
        service_name: str = "codeplane",
    ):
        self.enabled = enabled
        self.otlp_endpoint = otlp_endpoint
        self.service_name = service_name


# =============================================================================
# Tests for _is_telemetry_enabled
# =============================================================================


class TestIsTelemetryEnabled:
    """Tests for _is_telemetry_enabled function."""

    def test_enabled_via_env_var(self) -> None:
        """Telemetry enabled when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}):
            assert _is_telemetry_enabled(None) is True

    def test_enabled_via_config(self) -> None:
        """Telemetry enabled when config.enabled is True."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear any existing OTEL env var
            os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
            config = MockTelemetryConfig(enabled=True)
            assert _is_telemetry_enabled(config) is True  # type: ignore[arg-type]

    def test_disabled_when_no_config_and_no_env(self) -> None:
        """Telemetry disabled when neither config nor env var."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
            assert _is_telemetry_enabled(None) is False

    def test_disabled_when_config_disabled(self) -> None:
        """Telemetry disabled when config.enabled is False."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
            config = MockTelemetryConfig(enabled=False)
            assert _is_telemetry_enabled(config) is False  # type: ignore[arg-type]

    def test_env_var_takes_precedence(self) -> None:
        """Env var enables telemetry even if config.enabled is False."""
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}):
            config = MockTelemetryConfig(enabled=False)
            assert _is_telemetry_enabled(config) is True  # type: ignore[arg-type]


# =============================================================================
# Tests for _get_otlp_endpoint
# =============================================================================


class TestGetOtlpEndpoint:
    """Tests for _get_otlp_endpoint function."""

    def test_env_var_takes_precedence(self) -> None:
        """Environment variable takes precedence over config."""
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://env-endpoint:4317"}):
            config = MockTelemetryConfig(otlp_endpoint="http://config-endpoint:4317")
            assert _get_otlp_endpoint(config) == "http://env-endpoint:4317"  # type: ignore[arg-type]

    def test_falls_back_to_config(self) -> None:
        """Falls back to config when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
            config = MockTelemetryConfig(otlp_endpoint="http://config-endpoint:4317")
            assert _get_otlp_endpoint(config) == "http://config-endpoint:4317"  # type: ignore[arg-type]

    def test_returns_none_when_not_set(self) -> None:
        """Returns None when neither env var nor config set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
            config = MockTelemetryConfig(otlp_endpoint=None)
            assert _get_otlp_endpoint(config) is None  # type: ignore[arg-type]

    def test_returns_none_when_config_is_none(self) -> None:
        """Returns None when config is None and env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
            assert _get_otlp_endpoint(None) is None


# =============================================================================
# Tests for _get_service_name
# =============================================================================


class TestGetServiceName:
    """Tests for _get_service_name function."""

    def test_env_var_takes_precedence(self) -> None:
        """Environment variable takes precedence over config."""
        with patch.dict(os.environ, {"OTEL_SERVICE_NAME": "my-service"}):
            config = MockTelemetryConfig(service_name="config-service")
            assert _get_service_name(config) == "my-service"  # type: ignore[arg-type]

    def test_falls_back_to_config(self) -> None:
        """Falls back to config when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OTEL_SERVICE_NAME", None)
            config = MockTelemetryConfig(service_name="config-service")
            assert _get_service_name(config) == "config-service"  # type: ignore[arg-type]

    def test_default_service_name(self) -> None:
        """Returns 'codeplane' as default when nothing set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OTEL_SERVICE_NAME", None)
            assert _get_service_name(None) == "codeplane"


# =============================================================================
# Tests for NoOp classes
# =============================================================================


class TestNoOpSpan:
    """Tests for _NoOpSpan class."""

    def test_context_manager(self) -> None:
        """NoOpSpan works as context manager."""
        span = _NoOpSpan()
        with span as s:
            assert s is span

    def test_set_attribute_no_error(self) -> None:
        """set_attribute doesn't raise."""
        span = _NoOpSpan()
        span.set_attribute("key", "value")

    def test_set_attributes_no_error(self) -> None:
        """set_attributes doesn't raise."""
        span = _NoOpSpan()
        span.set_attributes({"key1": "value1", "key2": 123})

    def test_add_event_no_error(self) -> None:
        """add_event doesn't raise."""
        span = _NoOpSpan()
        span.add_event("test_event", {"attr": "value"})

    def test_set_status_no_error(self) -> None:
        """set_status doesn't raise."""
        span = _NoOpSpan()
        span.set_status("OK")  # Any value should work

    def test_record_exception_no_error(self) -> None:
        """record_exception doesn't raise."""
        span = _NoOpSpan()
        span.record_exception(ValueError("test error"))

    def test_is_recording_returns_false(self) -> None:
        """is_recording returns False."""
        span = _NoOpSpan()
        assert span.is_recording() is False


class TestNoOpTracer:
    """Tests for _NoOpTracer class."""

    def test_start_span_returns_noop_span(self) -> None:
        """start_span returns NoOpSpan."""
        tracer = _NoOpTracer()
        span = tracer.start_span("test")
        assert isinstance(span, _NoOpSpan)

    def test_start_as_current_span_returns_noop_span(self) -> None:
        """start_as_current_span returns NoOpSpan."""
        tracer = _NoOpTracer()
        span = tracer.start_as_current_span("test")
        assert isinstance(span, _NoOpSpan)

    def test_start_as_current_span_cm(self) -> None:
        """Context manager version works."""
        tracer = _NoOpTracer()
        with tracer.start_as_current_span_cm("test") as span:
            assert isinstance(span, _NoOpSpan)


class TestNoOpMeter:
    """Tests for _NoOpMeter class."""

    def test_create_counter(self) -> None:
        """create_counter returns NoOpCounter."""
        meter = _NoOpMeter()
        counter = meter.create_counter("test_counter")
        assert isinstance(counter, _NoOpCounter)

    def test_create_up_down_counter(self) -> None:
        """create_up_down_counter returns NoOpCounter."""
        meter = _NoOpMeter()
        counter = meter.create_up_down_counter("test_counter")
        assert isinstance(counter, _NoOpCounter)

    def test_create_histogram(self) -> None:
        """create_histogram returns NoOpHistogram."""
        meter = _NoOpMeter()
        histogram = meter.create_histogram("test_histogram")
        assert isinstance(histogram, _NoOpHistogram)

    def test_create_observable_gauge(self) -> None:
        """create_observable_gauge returns NoOpObservable."""
        meter = _NoOpMeter()
        gauge = meter.create_observable_gauge("test_gauge")
        assert isinstance(gauge, _NoOpObservable)

    def test_create_observable_counter(self) -> None:
        """create_observable_counter returns NoOpObservable."""
        meter = _NoOpMeter()
        counter = meter.create_observable_counter("test_counter")
        assert isinstance(counter, _NoOpObservable)

    def test_create_observable_up_down_counter(self) -> None:
        """create_observable_up_down_counter returns NoOpObservable."""
        meter = _NoOpMeter()
        counter = meter.create_observable_up_down_counter("test_counter")
        assert isinstance(counter, _NoOpObservable)


class TestNoOpCounter:
    """Tests for _NoOpCounter class."""

    def test_add_no_error(self) -> None:
        """add doesn't raise."""
        counter = _NoOpCounter()
        counter.add(1)
        counter.add(5, {"attr": "value"})


class TestNoOpHistogram:
    """Tests for _NoOpHistogram class."""

    def test_record_no_error(self) -> None:
        """record doesn't raise."""
        histogram = _NoOpHistogram()
        histogram.record(1.5)
        histogram.record(100, {"attr": "value"})


# =============================================================================
# Tests for get_tracer / get_meter
# =============================================================================


class TestGetTracer:
    """Tests for get_tracer function."""

    def test_returns_noop_tracer_when_not_initialized(self) -> None:
        """Returns NoOpTracer when telemetry not initialized."""
        # Reset global state
        import codeplane.core.telemetry as tel

        original_tracer = tel._tracer
        try:
            tel._tracer = None
            tracer = get_tracer()
            assert isinstance(tracer, _NoOpTracer)
        finally:
            tel._tracer = original_tracer


class TestGetMeter:
    """Tests for get_meter function."""

    def test_returns_noop_meter_when_not_initialized(self) -> None:
        """Returns NoOpMeter when telemetry not initialized."""
        import codeplane.core.telemetry as tel

        original_meter = tel._meter
        try:
            tel._meter = None
            meter = get_meter()
            assert isinstance(meter, _NoOpMeter)
        finally:
            tel._meter = original_meter


# =============================================================================
# Tests for traced decorator
# =============================================================================


class TestTracedDecorator:
    """Tests for traced decorator."""

    def test_sync_function_returns_result(self) -> None:
        """Traced sync function returns correct result."""

        @traced("test_operation")
        def my_func(x: int, y: int) -> int:
            return x + y

        result = my_func(2, 3)
        assert result == 5

    def test_sync_function_preserves_exception(self) -> None:
        """Traced sync function re-raises exceptions."""

        @traced("test_operation")
        def my_func() -> None:
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            my_func()

    @pytest.mark.asyncio
    async def test_async_function_returns_result(self) -> None:
        """Traced async function returns correct result."""

        @traced("test_operation")
        async def my_async_func(x: int) -> int:
            return x * 2

        result = await my_async_func(5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_async_function_preserves_exception(self) -> None:
        """Traced async function re-raises exceptions."""

        @traced("test_operation")
        async def my_async_func() -> None:
            raise RuntimeError("async error")

        with pytest.raises(RuntimeError, match="async error"):
            await my_async_func()

    def test_uses_function_name_as_default_span_name(self) -> None:
        """When no name provided, uses function's qualified name."""

        @traced()
        def named_function() -> str:
            return "result"

        # Function should work, span name derived from function
        assert named_function() == "result"

    def test_preserves_function_metadata(self) -> None:
        """Decorated function preserves __name__ and __doc__."""

        @traced("test")
        def documented_func() -> None:
            """This is the docstring."""
            pass

        assert documented_func.__name__ == "documented_func"
        assert documented_func.__doc__ == "This is the docstring."


# =============================================================================
# Tests for span_context
# =============================================================================


class TestSpanContext:
    """Tests for span_context context manager."""

    def test_yields_noop_span_when_not_initialized(self) -> None:
        """Yields NoOpSpan when telemetry not initialized."""
        import codeplane.core.telemetry as tel

        original_tracer = tel._tracer
        try:
            tel._tracer = None
            with span_context("test") as span:
                assert isinstance(span, _NoOpSpan)
        finally:
            tel._tracer = original_tracer

    def test_propagates_exception(self) -> None:
        """Exception inside span_context is propagated."""
        with pytest.raises(ValueError, match="inner error"), span_context("test"):
            raise ValueError("inner error")

    def test_with_attributes(self) -> None:
        """Attributes can be passed to span_context."""
        with span_context("test", attributes={"key": "value"}):
            # Should not raise
            pass


# =============================================================================
# Tests for is_telemetry_enabled / is_otel_available
# =============================================================================


class TestTelemetryStatusFunctions:
    """Tests for telemetry status check functions."""

    def test_is_telemetry_enabled_when_not_initialized(self) -> None:
        """is_telemetry_enabled returns False when not initialized."""
        import codeplane.core.telemetry as tel

        original_tracer = tel._tracer
        try:
            tel._tracer = None
            assert is_telemetry_enabled() is False
        finally:
            tel._tracer = original_tracer

    def test_is_otel_available(self) -> None:
        """is_otel_available returns boolean."""
        # This depends on whether otel is installed in test env
        result = is_otel_available()
        assert isinstance(result, bool)


# =============================================================================
# Tests for shutdown_telemetry
# =============================================================================


class TestShutdownTelemetry:
    """Tests for shutdown_telemetry function."""

    def test_shutdown_when_not_initialized_no_error(self) -> None:
        """Shutdown doesn't raise when not initialized."""
        import codeplane.core.telemetry as tel

        original_initialized = tel._initialized
        try:
            tel._initialized = False
            shutdown_telemetry()  # Should not raise
        finally:
            tel._initialized = original_initialized

    def test_shutdown_clears_globals(self) -> None:
        """Shutdown clears global state."""
        import codeplane.core.telemetry as tel

        # Set up mock state
        original_state = (
            tel._tracer,
            tel._meter,
            tel._tracer_provider,
            tel._meter_provider,
            tel._initialized,
        )
        try:
            # Simulate initialized state without real providers
            tel._tracer = MagicMock()
            tel._meter = MagicMock()
            tel._tracer_provider = None  # No provider to avoid shutdown calls
            tel._meter_provider = None
            tel._initialized = True

            shutdown_telemetry()

            assert tel._tracer is None
            assert tel._meter is None
            assert tel._initialized is False
        finally:
            (
                tel._tracer,
                tel._meter,
                tel._tracer_provider,
                tel._meter_provider,
                tel._initialized,
            ) = original_state
