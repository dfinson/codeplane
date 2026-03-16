"""Tests for CLI entry points."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

from backend.main import cli


def test_version_command() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_init_creates_config(tmp_path: Path) -> None:
    """cpl init creates a config file when none exists."""
    import backend.config as config_mod

    original = config_mod.DEFAULT_CONFIG_PATH
    try:
        config_mod.DEFAULT_CONFIG_PATH = tmp_path / "config.yaml"
        runner = CliRunner()
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "Created default configuration" in result.output
        assert config_mod.DEFAULT_CONFIG_PATH.exists()
    finally:
        config_mod.DEFAULT_CONFIG_PATH = original


def test_init_refuses_overwrite(tmp_path: Path) -> None:
    """cpl init does not overwrite an existing config."""
    import backend.config as config_mod

    original = config_mod.DEFAULT_CONFIG_PATH
    try:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("existing: true\n")
        config_mod.DEFAULT_CONFIG_PATH = cfg
        runner = CliRunner()
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "already exists" in result.output
        assert cfg.read_text() == "existing: true\n"
    finally:
        config_mod.DEFAULT_CONFIG_PATH = original
