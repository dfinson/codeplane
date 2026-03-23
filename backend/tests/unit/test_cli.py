"""Tests for CLI entry points."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from backend.main import cli


def test_version_command() -> None:
    from backend import __version__

    runner = CliRunner()
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_doctor_command_runs() -> None:
    """cpl doctor runs without crashing (may fail on missing deps, which is fine)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    # exit 0 = all clear, exit 1 = some failures — both are valid
    assert result.exit_code in (0, 1)


def test_doctor_json_output() -> None:
    """cpl doctor --json produces valid JSON."""
    import json

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--json"])
    assert result.exit_code in (0, 1)
    data = json.loads(result.output)
    assert "checks" in data
    assert "passed" in data
    assert "warnings" in data
    assert "failed" in data


# ---------------------------------------------------------------------------
# cpl down
# ---------------------------------------------------------------------------


class TestDown:
    def test_not_running(self) -> None:
        """down exits cleanly when nothing is running."""
        runner = CliRunner()
        with (
            patch("backend.cli._is_server_running", return_value=(False, [])),
        ):
            result = runner.invoke(cli, ["down"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_pauses_and_stops(self) -> None:
        """down pauses sessions then stops the server."""
        runner = CliRunner()
        with (
            patch("backend.cli._is_server_running", return_value=(True, [1234])),
            patch("backend.cli._pause_active_sessions") as mock_pause,
            patch("backend.cli._stop_server", return_value=True) as mock_stop,
        ):
            result = runner.invoke(cli, ["down"])
        assert result.exit_code == 0
        mock_pause.assert_called_once()
        mock_stop.assert_called_once()

    def test_force_skips_pause(self) -> None:
        """down --force skips session pausing."""
        runner = CliRunner()
        with (
            patch("backend.cli._is_server_running", return_value=(True, [1234])),
            patch("backend.cli._pause_active_sessions") as mock_pause,
            patch("backend.cli._stop_server", return_value=True),
        ):
            result = runner.invoke(cli, ["down", "--force"])
        assert result.exit_code == 0
        mock_pause.assert_not_called()


# ---------------------------------------------------------------------------
# cpl restart
# ---------------------------------------------------------------------------


class TestRestart:
    def test_no_running_instance_execs_up(self) -> None:
        """restart with no running instance goes straight to exec."""
        runner = CliRunner()
        with (
            patch("backend.cli._is_server_running", return_value=(False, [])),
            patch("os.execv") as mock_exec,
        ):
            result = runner.invoke(cli, ["restart"])
        assert "starting fresh" in result.output.lower()
        mock_exec.assert_called_once()

    def test_stops_then_execs_up(self) -> None:
        """restart stops an existing instance then execs up."""
        runner = CliRunner()
        with (
            patch("backend.cli._is_server_running", return_value=(True, [5678])),
            patch("backend.cli._pause_active_sessions"),
            patch("backend.cli._stop_server", return_value=True),
            patch("os.execv") as mock_exec,
        ):
            result = runner.invoke(cli, ["restart"])
        assert result.exit_code == 0
        mock_exec.assert_called_once()
        # The exec args should contain "up"
        args = mock_exec.call_args[0][1]
        assert "up" in args

    def test_remote_flag_forwarded(self) -> None:
        """restart --remote forwards the flag to cpl up."""
        runner = CliRunner()
        with (
            patch("backend.cli._is_server_running", return_value=(False, [])),
            patch("os.execv") as mock_exec,
        ):
            result = runner.invoke(cli, ["restart", "--remote"])  # noqa: F841
        args = mock_exec.call_args[0][1]
        assert "--remote" in args
