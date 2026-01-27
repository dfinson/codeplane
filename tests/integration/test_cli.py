"""Integration tests for CLI commands."""

import json
import logging
import os
from collections.abc import Generator
from pathlib import Path

import pytest
import structlog
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
def reset_state() -> Generator[None, None, None]:
    """Reset logging and env state between tests."""
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
    clear_request_id()
    orig = {k: v for k, v in os.environ.items() if k.startswith("CODEPLANE__")}
    for k in orig:
        del os.environ[k]
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
    clear_request_id()
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


class TestErrorPropagation:
    """Test that errors propagate properly through CLI."""

    def test_given_invalid_config_when_load_then_raises_config_error(self, temp_repo: Path) -> None:
        """Invalid config raises ConfigError with details."""
        # Given - init the repo first
        runner.invoke(cli, ["init", str(temp_repo)])

        # Given - corrupt the config
        config_path = temp_repo / ".codeplane" / "config.yaml"
        config_path.write_text("invalid: yaml: [unterminated")

        # When/Then
        with pytest.raises(ConfigError) as exc_info:
            load_config(repo_root=temp_repo)

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


class TestWorkflows:
    """Test complete user workflows through CLI."""

    def test_given_new_repo_when_init_load_log_then_works(
        self, temp_repo: Path, tmp_path: Path
    ) -> None:
        """Full workflow: init → load config → configure logging → log."""
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

        # Given - env vars set
        os.environ["CODEPLANE__LOGGING__LEVEL"] = "DEBUG"
        os.environ["CODEPLANE__DAEMON__PORT"] = "3000"

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.logging.level == "DEBUG"
        assert config.daemon.port == 3000
