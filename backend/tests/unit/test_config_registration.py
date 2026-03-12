"""Tests for configuration registration and persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.config import (
    TowerConfig,
    load_config,
    register_repo,
    save_config,
    unregister_repo,
)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.yaml"


@pytest.fixture
def config() -> TowerConfig:
    return TowerConfig(repos=[])


class TestSaveConfig:
    def test_save_and_reload(self, config: TowerConfig, config_path: Path) -> None:
        config.repos = ["/repos/a", "/repos/b"]
        save_config(config, config_path)
        assert config_path.exists()

        with open(config_path) as f:
            raw = yaml.safe_load(f)
        assert raw["repos"] == ["/repos/a", "/repos/b"]

    def test_save_creates_parent_dirs(self, config: TowerConfig, tmp_path: Path) -> None:
        deep_path = tmp_path / "nested" / "dir" / "config.yaml"
        save_config(config, deep_path)
        assert deep_path.exists()


class TestRegisterRepo:
    def test_register_adds_to_list(
        self,
        config: TowerConfig,
        config_path: Path,
    ) -> None:
        result = register_repo(config, "/repos/test", config_path)
        assert result == str(Path("/repos/test").resolve())
        assert result in config.repos

    def test_register_idempotent(
        self,
        config: TowerConfig,
        config_path: Path,
    ) -> None:
        register_repo(config, "/repos/test", config_path)
        register_repo(config, "/repos/test", config_path)
        resolved = str(Path("/repos/test").resolve())
        assert config.repos.count(resolved) == 1

    def test_register_persists_to_file(
        self,
        config: TowerConfig,
        config_path: Path,
    ) -> None:
        register_repo(config, "/repos/test", config_path)
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        resolved = str(Path("/repos/test").resolve())
        assert resolved in raw["repos"]


class TestUnregisterRepo:
    def test_unregister_removes_from_list(
        self,
        config: TowerConfig,
        config_path: Path,
    ) -> None:
        resolved = str(Path("/repos/test").resolve())
        config.repos = [resolved]
        save_config(config, config_path)

        result = unregister_repo(config, "/repos/test", config_path)
        assert result == resolved
        assert resolved not in config.repos

    def test_unregister_nonexistent_raises(
        self,
        config: TowerConfig,
        config_path: Path,
    ) -> None:
        with pytest.raises(ValueError, match="not in the allowlist"):
            unregister_repo(config, "/repos/nonexistent", config_path)


class TestLoadConfig:
    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        config = load_config(tmp_path / "does_not_exist.yaml")
        assert config.repos == []
        assert config.server.host == "127.0.0.1"

    def test_load_saved_config(self, config_path: Path) -> None:
        cfg = TowerConfig(repos=["/repos/a"])
        save_config(cfg, config_path)
        loaded = load_config(config_path)
        assert loaded.repos == ["/repos/a"]
