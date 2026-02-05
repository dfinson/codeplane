"""Tests for configuration loading."""

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from pydantic import ValidationError

from codeplane.config import CodePlaneConfig, load_config
from codeplane.config.models import LoggingConfig
from codeplane.config.user_config import UserConfig, write_user_config


@pytest.fixture
def temp_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary git repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()  # Fake git dir
    yield repo


@pytest.fixture(autouse=True)
def clean_env() -> Generator[None, None, None]:
    """Remove CODEPLANE__* env vars for clean tests."""
    orig = {k: v for k, v in os.environ.items() if k.startswith("CODEPLANE__")}
    for k in orig:
        del os.environ[k]
    yield
    # Clean up any new CODEPLANE__* env vars set during the test
    current_keys = [k for k in os.environ if k.startswith("CODEPLANE__")]
    for k in current_keys:
        del os.environ[k]
    # Restore original values
    os.environ.update(orig)


class TestConfigModels:
    """Configuration model validation tests."""

    @pytest.mark.parametrize(
        "level",
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    def test_given_log_level_when_validated_then_accepts_standard_levels(self, level: str) -> None:
        """Log level accepts standard Python logging levels."""
        config = CodePlaneConfig(logging={"level": level})
        assert config.logging.level == level

    def test_given_invalid_log_level_when_validated_then_rejects(self) -> None:
        """Invalid log level is rejected."""
        with pytest.raises(ValidationError):
            CodePlaneConfig(logging={"level": "INVALID"})

    @pytest.mark.parametrize(
        ("port", "valid"),
        [
            (0, True),
            (8080, True),
            (65535, True),
            (-1, False),  # Invalid: below range
            (65536, False),  # Invalid: above range
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
            config = CodePlaneConfig(server=daemon_config)
            assert config.server.port == port
        else:
            with pytest.raises(ValidationError):
                CodePlaneConfig(server=daemon_config)


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
        assert config.server.port == 7654

    def test_given_repo_config_when_load_then_overrides_defaults(self, temp_repo: Path) -> None:
        """Repo config file overrides defaults using new simplified format."""
        # Given - use new user config format
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        write_user_config(config_dir / "config.yaml", UserConfig(log_level="DEBUG"))

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.logging.level == "DEBUG"

    def test_given_env_var_when_load_then_overrides_file(self, temp_repo: Path) -> None:
        """Environment variable overrides file config."""
        # Given - use new user config format
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        write_user_config(config_dir / "config.yaml", UserConfig(log_level="DEBUG"))
        os.environ["CODEPLANE__LOGGING__LEVEL"] = "ERROR"

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.logging.level == "ERROR"

    def test_given_explicit_kwargs_when_load_then_highest_precedence(self, temp_repo: Path) -> None:
        """Explicit kwargs take highest precedence."""
        # Given
        os.environ["CODEPLANE__LOGGING__LEVEL"] = "ERROR"

        # When
        config = load_config(repo_root=temp_repo, logging=LoggingConfig(level="WARNING"))

        # Then
        assert config.logging.level == "WARNING"

    def test_given_invalid_yaml_when_load_then_uses_defaults(self, temp_repo: Path) -> None:
        """Invalid YAML in user config falls back to defaults gracefully."""
        # Given
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("invalid: yaml: content:")

        # When - should not raise, falls back to defaults
        config = load_config(repo_root=temp_repo)

        # Then - defaults used
        assert config.logging.level == "INFO"
        assert config.server.port == 7654

    def test_given_invalid_value_when_load_then_uses_defaults(self, temp_repo: Path) -> None:
        """Invalid config value falls back to defaults gracefully."""
        # Given - invalid port in user config format
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("port: invalid_string\n")

        # When - should not raise, falls back to defaults
        config = load_config(repo_root=temp_repo)

        # Then - defaults used
        assert config.server.port == 7654


class TestUserConfig:
    """Tests for the new simplified user config format."""

    def test_given_user_config_with_port_when_load_then_maps_to_server_port(
        self, temp_repo: Path
    ) -> None:
        """User config port maps to server.port in full config."""
        # Given
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        write_user_config(config_dir / "config.yaml", UserConfig(port=9999))

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.server.port == 9999

    def test_given_user_config_with_max_file_size_when_load_then_maps_to_index(
        self, temp_repo: Path
    ) -> None:
        """User config max_file_size_mb maps to index.max_file_size_mb."""
        # Given
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        write_user_config(config_dir / "config.yaml", UserConfig(max_file_size_mb=50))

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.index.max_file_size_mb == 50

    def test_given_raw_yaml_with_user_fields_when_load_then_works(self, temp_repo: Path) -> None:
        """Raw YAML with user config fields is loaded correctly."""
        # Given
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "port: 8080\nlog_level: WARNING\nmax_file_size_mb: 25\n"
        )

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.server.port == 8080
        assert config.logging.level == "WARNING"
        assert config.index.max_file_size_mb == 25
