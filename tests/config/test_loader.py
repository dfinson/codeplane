"""Tests for config/loader.py module.

Covers:
- _load_yaml() function
- _deep_merge() function
- _YamlSource settings source
- CodePlaneSettings class
- load_config() function
- get_index_paths() function
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from codeplane.config.loader import (
    GLOBAL_CONFIG_PATH,
    _deep_merge,
    _load_yaml,
    get_index_paths,
    load_config,
)
from codeplane.core.errors import ConfigError


class TestLoadYaml:
    """Tests for _load_yaml function."""

    def test_returns_empty_dict_for_missing_file(self, tmp_path: Path) -> None:
        """Returns empty dict when file doesn't exist."""
        result = _load_yaml(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_loads_valid_yaml(self, tmp_path: Path) -> None:
        """Loads valid YAML content."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("logging:\n  level: DEBUG\n")

        result = _load_yaml(yaml_file)
        assert result == {"logging": {"level": "DEBUG"}}

    def test_returns_empty_for_empty_file(self, tmp_path: Path) -> None:
        """Returns empty dict for empty file."""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")

        result = _load_yaml(yaml_file)
        assert result == {}

    def test_returns_empty_for_yaml_null(self, tmp_path: Path) -> None:
        """Returns empty dict for YAML containing null/None."""
        yaml_file = tmp_path / "null.yaml"
        yaml_file.write_text("null\n")

        result = _load_yaml(yaml_file)
        assert result == {}

    def test_raises_config_error_for_invalid_yaml(self, tmp_path: Path) -> None:
        """Raises ConfigError for invalid YAML syntax."""
        yaml_file = tmp_path / "invalid.yaml"
        yaml_file.write_text("logging:\n  level:\n    - invalid: [unclosed")

        with pytest.raises(ConfigError):
            _load_yaml(yaml_file)


class TestDeepMerge:
    """Tests for _deep_merge function."""

    def test_empty_dicts(self) -> None:
        """Merging empty dicts returns empty dict."""
        assert _deep_merge({}, {}) == {}

    def test_override_wins(self) -> None:
        """Override values replace base values."""
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert _deep_merge(base, override) == {"a": 1, "b": 3}

    def test_adds_new_keys(self) -> None:
        """Override adds new keys."""
        base = {"a": 1}
        override = {"b": 2}
        assert _deep_merge(base, override) == {"a": 1, "b": 2}

    def test_nested_merge(self) -> None:
        """Nested dicts are merged recursively."""
        base = {"logging": {"level": "INFO", "format": "json"}}
        override = {"logging": {"level": "DEBUG"}}
        result = _deep_merge(base, override)
        assert result == {"logging": {"level": "DEBUG", "format": "json"}}

    def test_override_replaces_non_dict(self) -> None:
        """Non-dict override replaces dict base."""
        base: dict[str, Any] = {"a": {"nested": 1}}
        override: dict[str, Any] = {"a": "simple"}
        assert _deep_merge(base, override) == {"a": "simple"}

    def test_does_not_mutate_base(self) -> None:
        """Base dict is not mutated."""
        base = {"a": 1}
        override = {"b": 2}
        _deep_merge(base, override)
        assert base == {"a": 1}


class TestLoadConfig:
    """Tests for load_config function."""

    def test_returns_default_config_when_no_files(self, tmp_path: Path) -> None:
        """Returns default config when no config files exist."""
        with patch.object(Path, "expanduser", return_value=tmp_path / "global.yaml"):
            config = load_config(tmp_path)
            # Should have default values
            assert config.logging.level == "INFO"

    def test_loads_repo_config(self, tmp_path: Path) -> None:
        """Loads config from repo .codeplane directory."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / "config.yaml").write_text("logging:\n  level: DEBUG\n")

        with patch("codeplane.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "none.yaml"):
            config = load_config(tmp_path)
            assert config.logging.level == "DEBUG"

    def test_env_vars_override_yaml(self, tmp_path: Path) -> None:
        """Environment variables override YAML config."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / "config.yaml").write_text("logging:\n  level: INFO\n")

        with (
            patch("codeplane.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "none.yaml"),
            patch.dict(os.environ, {"CODEPLANE__LOGGING__LEVEL": "WARNING"}),
        ):
            config = load_config(tmp_path)
            assert config.logging.level == "WARNING"

    def test_kwargs_override_all(self, tmp_path: Path) -> None:
        """Keyword arguments override everything."""
        from codeplane.config.models import LoggingConfig

        with patch("codeplane.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "none.yaml"):
            config = load_config(tmp_path, logging=LoggingConfig(level="ERROR"))
            assert config.logging.level == "ERROR"

    def test_raises_config_error_for_invalid_value(self, tmp_path: Path) -> None:
        """Raises ConfigError for invalid config values."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        # Invalid port value
        (codeplane_dir / "config.yaml").write_text("server:\n  port: -1\n")

        with (
            patch("codeplane.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "none.yaml"),
            pytest.raises(ConfigError),
        ):
            load_config(tmp_path)


class TestGetIndexPaths:
    """Tests for get_index_paths function."""

    def test_returns_default_paths(self, tmp_path: Path) -> None:
        """Returns default paths under .codeplane."""
        with patch("codeplane.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "none.yaml"):
            db_path, tantivy_path = get_index_paths(tmp_path)
            assert db_path == tmp_path / ".codeplane" / "index.db"
            assert tantivy_path == tmp_path / ".codeplane" / "tantivy"

    def test_respects_custom_index_path(self, tmp_path: Path) -> None:
        """Respects index_path setting in config."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        custom_path = tmp_path / "custom" / "index"
        (codeplane_dir / "config.yaml").write_text(f"index:\n  index_path: {custom_path}\n")

        with patch("codeplane.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "none.yaml"):
            db_path, tantivy_path = get_index_paths(tmp_path)
            assert db_path == custom_path / "index.db"
            assert tantivy_path == custom_path / "tantivy"


class TestGlobalConfigPath:
    """Tests for GLOBAL_CONFIG_PATH constant."""

    def test_is_path_object(self) -> None:
        """GLOBAL_CONFIG_PATH is a Path."""
        assert isinstance(GLOBAL_CONFIG_PATH, Path)

    def test_is_in_user_config(self) -> None:
        """Path is in user config directory."""
        # Should contain 'codeplane' somewhere in the path
        assert "codeplane" in str(GLOBAL_CONFIG_PATH)
