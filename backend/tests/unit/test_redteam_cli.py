"""Red-team / pressure tests for CLI commands (Phase 1).

Covers: invalid arguments, edge cases for cpl up/init/version.
"""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from backend.main import cli


class TestVersionCommand:
    def test_version_with_extra_args_ignored(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["version", "--help"])
        # --help is handled by click
        assert result.exit_code == 0

    def test_version_output_format(self) -> None:
        from backend import __version__

        runner = CliRunner()
        result = runner.invoke(cli, ["version"])
        assert result.output.strip() == f"cpl {__version__}"


class TestDoctorCommand:
    def test_doctor_runs(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor"])
        # exit 0 (all clear) or 1 (failures) — both valid
        assert result.exit_code in (0, 1)

    def test_doctor_json_has_schema(self) -> None:
        import json

        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--json"])
        assert result.exit_code in (0, 1)
        data = json.loads(result.output)
        assert isinstance(data["checks"], list)
        assert isinstance(data["passed"], int)
        assert isinstance(data["failed"], int)


class TestUpCommand:
    def test_up_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["up", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--dev" in result.output
        assert "--remote" in result.output
        assert "--provider" in result.output
        assert "devtunnel" in result.output
        assert "cloudflare" in result.output

    @patch("backend.cli.validate_remote_provider", return_value="ERROR: 'devtunnel' CLI not found.")
    def test_up_remote_requires_devtunnel_cli(self, mock_validate) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["up", "--remote", "--skip-preflight"])

        assert result.exit_code == 1
        assert "devtunnel" in result.output.lower()

    def test_up_rejects_string_port(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["up", "--port", "not_a_number"])
        # Click should reject this as not a valid integer
        assert result.exit_code != 0
        assert "not a valid integer" in result.output.lower() or "invalid" in result.output.lower()

    def test_up_accepts_negative_port(self) -> None:
        """Click accepts negative int; uvicorn would fail at bind time."""
        runner = CliRunner()
        with patch("backend.cli.run_migrations"), patch("backend.cli.uvicorn.run") as mock_run:
            result = runner.invoke(cli, ["up", "--port", "-1"])
            # Should reach uvicorn.run (click doesn't validate port range)
            if result.exit_code == 0:
                assert mock_run.called
                _, kwargs = mock_run.call_args
                assert kwargs["port"] == -1

    def test_up_with_zero_port(self) -> None:
        runner = CliRunner()
        with patch("backend.cli.run_migrations"), patch("backend.cli.uvicorn.run") as mock_run:
            result = runner.invoke(cli, ["up", "--port", "0"])
            if result.exit_code == 0:
                assert mock_run.called

    def test_up_with_custom_host(self) -> None:
        runner = CliRunner()
        with patch("backend.cli.run_migrations"), patch("backend.cli.uvicorn.run") as mock_run:
            result = runner.invoke(cli, ["up", "--host", "0.0.0.0"])
            if result.exit_code == 0:
                _, kwargs = mock_run.call_args
                assert kwargs["host"] == "0.0.0.0"

    def test_up_uses_config_defaults(self) -> None:
        runner = CliRunner()
        with patch("backend.cli.run_migrations"), patch("backend.cli.uvicorn.run") as mock_run:
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
