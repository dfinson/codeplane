"""Integration tests for structured logging."""

import json
import logging
from collections.abc import Generator
from pathlib import Path

import pytest
import structlog

from codeplane.config.models import LoggingConfig, LogOutputConfig
from codeplane.core.logging import (
    clear_request_id,
    configure_logging,
    get_logger,
    set_request_id,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_logging() -> Generator[None, None, None]:
    """Reset logging state between tests."""
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
    clear_request_id()
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
    clear_request_id()


class TestFileOutput:
    """Test logging to file destinations."""

    def test_given_file_output_when_log_then_writes_to_file(self, tmp_path: Path) -> None:
        """Logs are written to configured file path."""
        # Given
        log_file = tmp_path / "app.log"
        config = LoggingConfig(
            level="INFO",
            outputs=[LogOutputConfig(format="json", destination=str(log_file))],
        )
        configure_logging(config=config)
        logger = get_logger("filetest")

        # When
        set_request_id("req-123")
        logger.info("test event", user="alice")

        # Then
        assert log_file.exists()
        content = log_file.read_text()
        data = json.loads(content.strip().split("\n")[-1])
        assert data["event"] == "test event"
        assert data["user"] == "alice"
        assert data["request_id"] == "req-123"
        assert data["level"] == "info"


class TestMultiOutput:
    """Test multiple simultaneous log outputs."""

    def test_given_multiple_outputs_when_log_then_filters_by_level(self, tmp_path: Path) -> None:
        """Each output respects its own level filter.

        NOTE: Console output is suppressed by design (ConsoleSuppressingFilter).
        This test verifies multi-output filtering via file destinations only.
        """
        # Given
        debug_file = tmp_path / "debug.log"
        error_file = tmp_path / "error.log"
        warning_file = tmp_path / "warning.log"
        config = LoggingConfig(
            level="DEBUG",
            outputs=[
                LogOutputConfig(format="json", destination=str(debug_file), level="DEBUG"),
                LogOutputConfig(format="json", destination=str(error_file), level="ERROR"),
                LogOutputConfig(format="json", destination=str(warning_file), level="WARNING"),
            ],
        )
        configure_logging(config=config)
        logger = get_logger()

        # When
        logger.debug("debug msg")
        logger.info("info msg")
        logger.warning("warn msg")
        logger.error("error msg")

        # Then - debug file has all
        debug_content = debug_file.read_text()
        assert "debug msg" in debug_content
        assert "info msg" in debug_content
        assert "error msg" in debug_content

        # Then - error file has only error
        error_content = error_file.read_text()
        assert "debug msg" not in error_content
        assert "info msg" not in error_content
        assert "error msg" in error_content

        # Then - warning file has warning+
        warning_content = warning_file.read_text()
        assert "debug msg" not in warning_content
        assert "info msg" not in warning_content
        assert "warn msg" in warning_content
        assert "error msg" in warning_content


class TestRequestCorrelation:
    """Test request ID correlation across log entries."""

    def test_given_request_id_when_multiple_logs_then_all_have_id(self, tmp_path: Path) -> None:
        """Request ID is preserved across multiple log calls."""
        # Given
        log_file = tmp_path / "trace.log"
        config = LoggingConfig(
            level="DEBUG",
            outputs=[LogOutputConfig(format="json", destination=str(log_file))],
        )
        configure_logging(config=config)
        logger = get_logger()
        request_id = set_request_id("trace-abc")

        # When
        logger.info("step 1")
        logger.info("step 2")
        logger.info("step 3")

        # Then
        lines = log_file.read_text().strip().split("\n")
        for line in lines:
            data = json.loads(line)
            assert data["request_id"] == request_id
