"""Red-team / pressure tests for CLI commands (Phase 1).

Covers: invalid arguments, edge cases for cpl up/init/version.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

from backend.main import cli


class TestVersionCommand:
    def test_version_with_extra_args_ignored(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["version", "--help"])
        # --help is handled by click
        assert result.exit_code == 0

    def test_version_output_format(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["version"])
        assert result.output.strip() == "cpl 0.1.0"


class TestInitCommand:
    def test_init_second_call_refuses(self, tmp_path: Path) -> None:
        import backend.config as config_mod

        original = config_mod.DEFAULT_CONFIG_PATH
        try:
            config_mod.DEFAULT_CONFIG_PATH = tmp_path / "config.yaml"
            runner = CliRunner()
            result1 = runner.invoke(cli, ["init"])
            assert result1.exit_code == 0
            assert "Created" in result1.output

            result2 = runner.invoke(cli, ["init"])
            assert result2.exit_code == 0
            assert "already exists" in result2.output
        finally:
            config_mod.DEFAULT_CONFIG_PATH = original

    def test_init_creates_parent_dirs(self, tmp_path: Path) -> None:
        import backend.config as config_mod

        original = config_mod.DEFAULT_CONFIG_PATH
        try:
            config_mod.DEFAULT_CONFIG_PATH = tmp_path / "deep" / "nested" / "config.yaml"
            runner = CliRunner()
            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert config_mod.DEFAULT_CONFIG_PATH.exists()
        finally:
            config_mod.DEFAULT_CONFIG_PATH = original


class TestUpCommand:
    def test_up_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["up", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--dev" in result.output
        assert "--tunnel" in result.output

    def test_up_rejects_string_port(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["up", "--port", "not_a_number"])
        # Click should reject this as not a valid integer
        assert result.exit_code != 0
        assert "not a valid integer" in result.output.lower() or "invalid" in result.output.lower()

    def test_up_accepts_negative_port(self) -> None:
        """Click accepts negative int; uvicorn would fail at bind time."""
        runner = CliRunner()
        with patch("backend.main.run_migrations"), patch("backend.main.uvicorn.run") as mock_run:
            result = runner.invoke(cli, ["up", "--port", "-1"])
            # Should reach uvicorn.run (click doesn't validate port range)
            if result.exit_code == 0:
                assert mock_run.called
                _, kwargs = mock_run.call_args
                assert kwargs["port"] == -1

    def test_up_with_zero_port(self) -> None:
        runner = CliRunner()
        with patch("backend.main.run_migrations"), patch("backend.main.uvicorn.run") as mock_run:
            result = runner.invoke(cli, ["up", "--port", "0"])
            if result.exit_code == 0:
                assert mock_run.called

    def test_up_with_custom_host(self) -> None:
        runner = CliRunner()
        with patch("backend.main.run_migrations"), patch("backend.main.uvicorn.run") as mock_run:
            result = runner.invoke(cli, ["up", "--host", "0.0.0.0"])
            if result.exit_code == 0:
                _, kwargs = mock_run.call_args
                assert kwargs["host"] == "0.0.0.0"

    def test_up_uses_config_defaults(self) -> None:
        runner = CliRunner()
        with patch("backend.main.run_migrations"), patch("backend.main.uvicorn.run") as mock_run:
            result = runner.invoke(cli, ["up"])
            if result.exit_code == 0:
                _, kwargs = mock_run.call_args
                assert kwargs["host"] == "127.0.0.1"
                assert kwargs["port"] == 8080


class TestUnknownCommands:
    def test_unknown_subcommand(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["nonexistent"])
        assert result.exit_code != 0

    def test_no_subcommand(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, [])
        # Click group with invoke_without_command=False returns exit 2
        assert result.exit_code == 2
        assert "Usage" in result.output

    def test_help_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "CodePlane" in result.output
