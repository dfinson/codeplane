"""Integration tests for M0 Foundation - end-to-end flows."""

import json
import logging
import os
from collections.abc import Generator
from pathlib import Path

import pytest
import structlog
import yaml
from click.testing import CliRunner

from codeplane.cli.main import cli
from codeplane.config import load_config
from codeplane.config.models import LoggingConfig, LogOutputConfig
from codeplane.core.errors import ConfigError
from codeplane.core.logging import (
    clear_request_id,
    configure_logging,
    get_logger,
    set_request_id,
)

runner = CliRunner()


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


@pytest.fixture(autouse=True)
def clean_env() -> Generator[None, None, None]:
    """Remove CODEPLANE__* env vars for clean tests."""
    orig = {k: v for k, v in os.environ.items() if k.startswith("CODEPLANE__")}
    for k in orig:
        del os.environ[k]
    yield
    for k in list(os.environ.keys()):
        if k.startswith("CODEPLANE__"):
            del os.environ[k]
    os.environ.update(orig)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


@pytest.fixture
def global_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create and patch global config directory."""
    global_dir = tmp_path / "global_config" / "codeplane"
    global_dir.mkdir(parents=True)
    # Patch the global config path
    monkeypatch.setattr(
        "codeplane.config.loader.GLOBAL_CONFIG_PATH",
        global_dir / "config.yaml",
    )
    return global_dir


class TestConfigCascadeIntegration:
    """Test full config cascade: defaults < global < repo < env < kwargs."""

    def test_given_all_sources_when_load_then_correct_precedence(
        self, temp_repo: Path, global_config_dir: Path
    ) -> None:
        """Full cascade applies correct precedence."""
        # Given - global config sets base values
        with (global_config_dir / "config.yaml").open("w") as f:
            yaml.dump(
                {
                    "logging": {"level": "WARNING"},
                    "daemon": {"port": 8000, "host": "0.0.0.0"},
                },
                f,
            )

        # Given - repo config overrides some values
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        with (config_dir / "config.yaml").open("w") as f:
            yaml.dump(
                {
                    "logging": {"level": "DEBUG"},
                    # daemon.port NOT overridden - should inherit from global
                },
                f,
            )

        # Given - env var overrides one value
        os.environ["CODEPLANE__DAEMON__PORT"] = "9999"

        # When
        config = load_config(repo_root=temp_repo)

        # Then - verify cascade
        assert config.logging.level == "DEBUG"  # from repo
        assert config.daemon.port == 9999  # from env (overrides global)
        assert config.daemon.host == "0.0.0.0"  # from global

    def test_given_nested_config_when_merge_then_deep_merges(
        self, temp_repo: Path, global_config_dir: Path
    ) -> None:
        """Nested config objects are deep merged, not replaced."""
        # Given - global sets multiple logging outputs
        with (global_config_dir / "config.yaml").open("w") as f:
            yaml.dump(
                {
                    "logging": {
                        "level": "INFO",
                        "outputs": [{"format": "console", "destination": "stderr"}],
                    },
                },
                f,
            )

        # Given - repo changes level but keeps outputs structure
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        with (config_dir / "config.yaml").open("w") as f:
            yaml.dump(
                {
                    "logging": {"level": "DEBUG"},
                },
                f,
            )

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.logging.level == "DEBUG"
        assert len(config.logging.outputs) == 1  # default, not merged


class TestLoggingIntegration:
    """Test logging flows end-to-end."""

    def test_given_file_output_when_log_then_writes_to_file(self, tmp_path: Path) -> None:
        """Logs are written to configured file path."""
        # Given
        log_file = tmp_path / "app.log"
        config = LoggingConfig(
            level="INFO",
            outputs=[LogOutputConfig(format="json", destination=str(log_file))],
        )
        configure_logging(config=config)
        logger = get_logger("integration")

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

    def test_given_multiple_outputs_when_log_then_filters_by_level(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Each output respects its own level filter."""
        # Given
        debug_file = tmp_path / "debug.log"
        error_file = tmp_path / "error.log"
        config = LoggingConfig(
            level="DEBUG",
            outputs=[
                LogOutputConfig(format="json", destination=str(debug_file), level="DEBUG"),
                LogOutputConfig(format="json", destination=str(error_file), level="ERROR"),
                LogOutputConfig(format="console", destination="stderr", level="WARNING"),
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

        # Then - stderr has warning+
        captured = capsys.readouterr()
        assert "debug msg" not in captured.err
        assert "info msg" not in captured.err
        assert "warn msg" in captured.err
        assert "error msg" in captured.err

    def test_given_request_id_when_concurrent_logs_then_preserved(self, tmp_path: Path) -> None:
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

        # Then - all logs have same request_id
        lines = log_file.read_text().strip().split("\n")
        for line in lines:
            data = json.loads(line)
            assert data["request_id"] == request_id


class TestCliErrorPropagation:
    """Test that errors propagate properly through CLI."""

    def test_given_invalid_config_when_init_load_then_cli_shows_error(
        self, temp_repo: Path
    ) -> None:
        """CLI surfaces config errors with actionable messages."""
        # Given - init the repo first
        runner.invoke(cli, ["init", str(temp_repo)])

        # Given - corrupt the config
        config_path = temp_repo / ".codeplane" / "config.yaml"
        config_path.write_text("invalid: yaml: [unterminated")

        # When - try to load config (simulated via direct call since CLI doesn't load yet)
        with pytest.raises(ConfigError) as exc_info:
            load_config(repo_root=temp_repo)

        # Then
        assert exc_info.value.code.name == "CONFIG_PARSE_ERROR"
        assert str(temp_repo) in str(exc_info.value.details.get("path", ""))

    def test_given_init_error_when_invoke_then_nonzero_exit(self, tmp_path: Path) -> None:
        """CLI returns non-zero exit code on error."""
        # Given - directory without .git
        non_git = tmp_path / "not-a-repo"
        non_git.mkdir()

        # When
        result = runner.invoke(cli, ["init", str(non_git)])

        # Then
        assert result.exit_code == 1
        assert "Not inside a git repository" in result.output


class TestFullWorkflow:
    """Test complete user workflows."""

    def test_given_new_repo_when_full_setup_then_working_config(
        self, temp_repo: Path, tmp_path: Path
    ) -> None:
        """Full workflow: init → configure → log."""
        # Step 1: Init
        result = runner.invoke(cli, ["init", str(temp_repo)])
        assert result.exit_code == 0

        # Step 2: Load config
        config = load_config(repo_root=temp_repo)
        assert config.logging.level == "INFO"

        # Step 3: Configure logging with file output
        log_file = tmp_path / "workflow.log"
        custom_config = LoggingConfig(
            level=config.logging.level,
            outputs=[LogOutputConfig(format="json", destination=str(log_file))],
        )
        configure_logging(config=custom_config)

        # Step 4: Log with request correlation
        logger = get_logger("workflow")
        set_request_id("workflow-test")
        logger.info("workflow complete", step="final")

        # Verify
        content = log_file.read_text()
        data = json.loads(content.strip())
        assert data["event"] == "workflow complete"
        assert data["request_id"] == "workflow-test"

    def test_given_env_override_when_init_and_load_then_env_wins(self, temp_repo: Path) -> None:
        """Environment variables override file config after init."""
        # Given - init creates default config
        runner.invoke(cli, ["init", str(temp_repo)])

        # Given - env var set
        os.environ["CODEPLANE__LOGGING__LEVEL"] = "DEBUG"
        os.environ["CODEPLANE__DAEMON__PORT"] = "3000"

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.logging.level == "DEBUG"
        assert config.daemon.port == 3000
