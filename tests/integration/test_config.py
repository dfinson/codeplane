"""Integration tests for configuration loading."""

import os
from collections.abc import Generator
from pathlib import Path

import pytest
import yaml

from codeplane.config import load_config

pytestmark = pytest.mark.integration


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
    monkeypatch.setattr(
        "codeplane.config.loader.GLOBAL_CONFIG_PATH",
        global_dir / "config.yaml",
    )
    return global_dir


class TestConfigCascade:
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
            yaml.dump({"logging": {"level": "DEBUG"}}, f)

        # Given - env var overrides one value
        os.environ["CODEPLANE__DAEMON__PORT"] = "9999"

        # When
        config = load_config(repo_root=temp_repo)

        # Then - verify cascade
        assert config.logging.level == "DEBUG"  # from repo
        assert config.server.port == 9999  # from env (overrides global)
        assert config.server.host == "0.0.0.0"  # from global

    def test_given_nested_config_when_merge_then_deep_merges(
        self, temp_repo: Path, global_config_dir: Path
    ) -> None:
        """Nested config objects are deep merged, not replaced."""
        # Given - global sets logging config
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

        # Given - repo changes level only
        config_dir = temp_repo / ".codeplane"
        config_dir.mkdir()
        with (config_dir / "config.yaml").open("w") as f:
            yaml.dump({"logging": {"level": "DEBUG"}}, f)

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.logging.level == "DEBUG"
        assert len(config.logging.outputs) == 1
