"""Tests for configuration loading."""

import os
from collections.abc import Generator
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from codeplane.config import CodePlaneConfig, load_config
from codeplane.core.errors import ConfigError


@pytest.fixture
def temp_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary git repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()  # Fake git dir
    yield repo


@pytest.fixture(autouse=True)
def clean_env() -> Generator[None, None, None]:
    """Remove CODEPLANE_* env vars for clean tests."""
    orig = {k: v for k, v in os.environ.items() if k.startswith("CODEPLANE_")}
    for k in orig:
        del os.environ[k]
    yield
    os.environ.update(orig)


class TestConfigModels:
    """Configuration model validation tests."""

    @pytest.mark.parametrize(
        ("level", "valid"),
        [
            ("DEBUG", True),
            ("INFO", True),
            ("WARN", True),
            ("ERROR", True),
            ("INVALID", False),
        ],
    )
    def test_given_log_level_when_validated_then_accepts_only_valid(
        self, level: str, valid: bool
    ) -> None:
        """Log level accepts only valid values (DEBUG, INFO, WARN, ERROR)."""
        # Given
        logging_config = {"level": level}

        # When / Then
        if valid:
            config = CodePlaneConfig(logging=logging_config)
            assert config.logging.level == level.upper()
        else:
            with pytest.raises(ValidationError):
                CodePlaneConfig(logging=logging_config)

    @pytest.mark.parametrize(
        ("port", "valid"),
        [
            (0, True),
            (8080, True),
            (65535, True),
            (-1, False),
            (65536, False),
        ],
    )
    def test_given_port_when_validated_then_accepts_only_valid_range(
        self, port: int, valid: bool
    ) -> None:
        """Port accepts only valid range (0-65535)."""
        # Given
        daemon_config = {"port": port}

        # When / Then
        if valid:
            config = CodePlaneConfig(daemon=daemon_config)
            assert config.daemon.port == port
        else:
            with pytest.raises(ValidationError):
                CodePlaneConfig(daemon=daemon_config)


class TestConfigLoading:
    """Configuration loading and precedence tests."""

    def test_given_no_config_files_when_load_then_uses_defaults(self, temp_repo: Path) -> None:
        """Defaults are used when no config files exist."""
        # Given
        repo = temp_repo  # no .codeplane/config.yaml

        # When
        config = load_config(repo_root=repo)

        # Then
        assert config.logging.level == "INFO"
        assert config.daemon.port == 0

    def test_given_repo_config_when_load_then_overrides_defaults(self, temp_repo: Path) -> None:
        """Repo config file overrides defaults."""
        # Given
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        with (config_dir / "config.yaml").open("w") as f:
            yaml.dump({"logging": {"level": "DEBUG"}}, f)

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.logging.level == "DEBUG"

    def test_given_env_var_when_load_then_overrides_file(self, temp_repo: Path) -> None:
        """Environment variable overrides file config."""
        # Given
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        with (config_dir / "config.yaml").open("w") as f:
            yaml.dump({"logging": {"level": "DEBUG"}}, f)
        os.environ["CODEPLANE_LOGGING_LEVEL"] = "ERROR"

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.logging.level == "ERROR"

    def test_given_explicit_overrides_when_load_then_highest_precedence(
        self, temp_repo: Path
    ) -> None:
        """Explicit overrides take highest precedence."""
        # Given
        os.environ["CODEPLANE_LOGGING_LEVEL"] = "ERROR"
        overrides = {"logging.level": "WARN"}

        # When
        config = load_config(repo_root=temp_repo, overrides=overrides)

        # Then
        assert config.logging.level == "WARN"

    def test_given_invalid_yaml_when_load_then_raises_config_error(self, temp_repo: Path) -> None:
        """Invalid YAML raises ConfigError with parse error code."""
        # Given
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("invalid: yaml: content:")

        # When / Then
        with pytest.raises(ConfigError) as exc_info:
            load_config(repo_root=temp_repo)
        assert exc_info.value.code.name == "CONFIG_PARSE_ERROR"

    def test_given_invalid_value_when_load_then_raises_config_error(self, temp_repo: Path) -> None:
        """Invalid config value raises ConfigError with field info."""
        # Given
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        with (config_dir / "config.yaml").open("w") as f:
            yaml.dump({"daemon": {"port": -999}}, f)

        # When / Then
        with pytest.raises(ConfigError) as exc_info:
            load_config(repo_root=temp_repo)
        assert exc_info.value.code.name == "CONFIG_INVALID_VALUE"
        assert "port" in exc_info.value.details.get("field", "")
