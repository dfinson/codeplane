"""Tests for config/models.py module.

Covers:
- LogLevel type
- LogOutputConfig model
- LoggingConfig model
- ServerConfig model
- IndexConfig model
- TimeoutsConfig model
- IndexerConfig model
- LimitsConfig model
- TestingConfig model
- CodePlaneConfig root model
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeplane.config.models import (
    CodePlaneConfig,
    IndexConfig,
    IndexerConfig,
    LimitsConfig,
    LoggingConfig,
    LogOutputConfig,
    ServerConfig,
    TestingConfig,
    TimeoutsConfig,
)


class TestLogOutputConfig:
    """Tests for LogOutputConfig model."""

    def test_defaults(self) -> None:
        """Default values."""
        config = LogOutputConfig()
        assert config.format == "console"
        assert config.destination == "stderr"
        assert config.level is None

    def test_stderr_destination(self) -> None:
        """stderr is valid destination."""
        config = LogOutputConfig(destination="stderr")
        assert config.destination == "stderr"

    def test_stdout_destination(self) -> None:
        """stdout is valid destination."""
        config = LogOutputConfig(destination="stdout")
        assert config.destination == "stdout"

    def test_absolute_path_destination(self) -> None:
        """Absolute path is valid destination."""
        config = LogOutputConfig(destination="/var/log/codeplane.log")
        assert config.destination == "/var/log/codeplane.log"

    def test_relative_path_fails(self) -> None:
        """Relative path is rejected."""
        with pytest.raises(ValidationError, match="absolute path"):
            LogOutputConfig(destination="logs/app.log")

    def test_format_options(self) -> None:
        """Format can be json or console."""
        LogOutputConfig(format="json")
        LogOutputConfig(format="console")


class TestLoggingConfig:
    """Tests for LoggingConfig model."""

    def test_defaults(self) -> None:
        """Default values."""
        config = LoggingConfig()
        assert config.level == "INFO"
        assert len(config.outputs) == 1

    def test_level_options(self) -> None:
        """Log levels are valid."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = LoggingConfig(level=level)  # type: ignore[arg-type]
            assert config.level == level

    def test_invalid_level(self) -> None:
        """Invalid level is rejected."""
        with pytest.raises(ValidationError):
            LoggingConfig(level="TRACE")  # type: ignore[arg-type]


class TestServerConfig:
    """Tests for ServerConfig model."""

    def test_defaults(self) -> None:
        """Default values."""
        config = ServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 7654
        assert config.shutdown_timeout_sec == 5

    def test_valid_port(self) -> None:
        """Valid ports are accepted."""
        ServerConfig(port=0)
        ServerConfig(port=8080)
        ServerConfig(port=65535)

    def test_invalid_port_negative(self) -> None:
        """Negative port is rejected."""
        with pytest.raises(ValidationError):
            ServerConfig(port=-1)

    def test_invalid_port_too_high(self) -> None:
        """Port > 65535 is rejected."""
        with pytest.raises(ValidationError):
            ServerConfig(port=65536)


class TestIndexConfig:
    """Tests for IndexConfig model."""

    def test_defaults(self) -> None:
        """Default values."""
        config = IndexConfig()
        assert config.max_file_size_mb == 10
        assert config.index_path is None
        assert ".min.js" in config.excluded_extensions

    def test_custom_max_file_size(self) -> None:
        """Custom max file size."""
        config = IndexConfig(max_file_size_mb=50)
        assert config.max_file_size_mb == 50

    def test_custom_index_path(self) -> None:
        """Custom index path."""
        config = IndexConfig(index_path="/custom/index")
        assert config.index_path == "/custom/index"


class TestTimeoutsConfig:
    """Tests for TimeoutsConfig model."""

    def test_defaults(self) -> None:
        """Default values."""
        config = TimeoutsConfig()
        assert config.server_stop_sec == 5.0
        assert config.epoch_await_sec == 5.0
        assert config.session_idle_sec == 1800.0  # 30 minutes
        assert config.dry_run_ttl_sec == 60.0


class TestIndexerConfig:
    """Tests for IndexerConfig model."""

    def test_defaults(self) -> None:
        """Default values."""
        config = IndexerConfig()
        assert config.debounce_sec == 0.5
        assert config.max_workers == 1
        assert config.queue_max_size == 10000


class TestLimitsConfig:
    """Tests for LimitsConfig model."""

    def test_defaults(self) -> None:
        """Default values."""
        config = LimitsConfig()
        assert config.search_default == 20
        assert config.map_depth_default == 3
        assert config.git_log_default == 50


class TestTestingConfig:
    """Tests for TestingConfig model."""

    def test_defaults(self) -> None:
        """Default values."""
        config = TestingConfig()
        assert config.default_parallelism == 4
        assert config.default_timeout_sec == 300


class TestCodePlaneConfig:
    """Tests for CodePlaneConfig root model."""

    def test_defaults(self) -> None:
        """Default values for all nested configs."""
        config = CodePlaneConfig()
        assert config.logging.level == "INFO"
        assert config.server.port == 7654
        assert config.index.max_file_size_mb == 10

    def test_nested_override(self) -> None:
        """Can override nested config values."""
        config = CodePlaneConfig(
            logging=LoggingConfig(level="DEBUG"),
            server=ServerConfig(port=8080),
        )
        assert config.logging.level == "DEBUG"
        assert config.server.port == 8080

    def test_all_subconfigs_present(self) -> None:
        """All subconfigs are present."""
        config = CodePlaneConfig()
        assert hasattr(config, "logging")
        assert hasattr(config, "server")
        assert hasattr(config, "index")
        assert hasattr(config, "timeouts")
        assert hasattr(config, "indexer")
        assert hasattr(config, "limits")
        assert hasattr(config, "testing")
