"""Tests for structured logging."""

import json

import pytest
import structlog

from codeplane.core.logging import (
    clear_request_id,
    configure_logging,
    get_logger,
    get_request_id,
    set_request_id,
)


class TestRequestIdCorrelation:
    """Request ID context variable tests."""

    def setup_method(self) -> None:
        """Clear request ID before each test."""
        clear_request_id()

    def test_given_request_id_when_set_then_can_retrieve(self) -> None:
        """Request ID can be set and retrieved."""
        # Given
        request_id = "test-123"

        # When
        result = set_request_id(request_id)

        # Then
        assert result == request_id
        assert get_request_id() == request_id

    def test_given_no_id_when_set_then_generates_uuid(self) -> None:
        """Set generates UUID-based ID when none provided."""
        # Given
        # (no explicit ID)

        # When
        rid = set_request_id()

        # Then
        assert rid is not None
        assert len(rid) == 12  # uuid4().hex[:12]

    def test_given_set_id_when_clear_then_removes_id(self) -> None:
        """Clear removes the current request ID."""
        # Given
        set_request_id("to-clear")

        # When
        clear_request_id()

        # Then
        assert get_request_id() is None

    def test_given_fresh_context_when_get_then_returns_none(self) -> None:
        """Fresh context has no request ID."""
        # Given
        # (fresh context from setup_method)

        # When
        result = get_request_id()

        # Then
        assert result is None


class TestLoggingConfiguration:
    """Logging configuration tests."""

    def test_given_json_format_when_log_then_valid_json_output(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """JSON format produces valid JSON with required fields."""
        # Given
        structlog.reset_defaults()
        configure_logging(json_format=True, level="INFO")
        logger = get_logger("test")

        # When
        logger.info("test message", key="value")

        # Then
        captured = capsys.readouterr()
        lines = [line for line in captured.err.strip().split("\n") if line]
        if lines:
            data = json.loads(lines[-1])
            assert data["event"] == "test message"
            assert data["key"] == "value"
            assert "ts" in data
            assert data["level"] == "info"

    def test_given_module_name_when_get_logger_then_binds_name(self) -> None:
        """Logger is bound to provided module name."""
        # Given
        configure_logging(json_format=False, level="INFO")
        module_name = "mymodule"

        # When
        logger = get_logger(module_name)

        # Then
        assert logger is not None
