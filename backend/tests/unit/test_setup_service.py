"""Tests for setup_service — dependency checks, env var handling."""

from __future__ import annotations

from unittest.mock import patch

from backend.services.setup_service import (
    _check_command,
    _get_env_persistence_instructions,
    preflight_check,
)


class TestCheckCommand:
    @patch("shutil.which", return_value="/usr/bin/node")
    @patch("subprocess.run")
    def test_found_command(self, mock_run, mock_which) -> None:
        mock_run.return_value = type("Result", (), {"stdout": "v20.0.0\n", "returncode": 0})()
        found, version = _check_command("node")
        assert found is True
        assert "v20.0.0" in (version or "")

    @patch("shutil.which", return_value=None)
    def test_missing_command(self, mock_which) -> None:
        found, version = _check_command("nonexistent")
        assert found is False
        assert version is None


class TestEnvPersistenceInstructions:
    @patch("backend.services.setup_service._SYSTEM", "linux")
    @patch("os.environ", {"SHELL": "/bin/bash"})
    def test_linux_bash(self) -> None:
        result = _get_env_persistence_instructions("TEST_VAR", "/opt/test")
        assert ".bashrc" in result
        assert "export TEST_VAR" in result

    @patch("backend.services.setup_service._SYSTEM", "linux")
    @patch("os.environ", {"SHELL": "/bin/zsh"})
    def test_linux_zsh(self) -> None:
        result = _get_env_persistence_instructions("TEST_VAR", "/opt/test")
        assert ".zshrc" in result

    @patch("backend.services.setup_service._SYSTEM", "linux")
    @patch("os.environ", {"SHELL": "/usr/bin/fish"})
    def test_linux_fish(self) -> None:
        result = _get_env_persistence_instructions("TEST_VAR", "/opt/test")
        assert "set -Ux" in result

    @patch("backend.services.setup_service._SYSTEM", "darwin")
    @patch("os.environ", {"SHELL": "/bin/zsh"})
    def test_macos(self) -> None:
        result = _get_env_persistence_instructions("TEST_VAR", "/opt/test")
        assert ".zshrc" in result

    @patch("backend.services.setup_service._SYSTEM", "windows")
    def test_windows(self) -> None:
        result = _get_env_persistence_instructions("TEST_VAR", "C:\\test")
        assert "PowerShell" in result


class TestPreflightCheck:
    @patch("backend.services.setup_service._check_command")
    def test_all_found(self, mock_check) -> None:
        mock_check.return_value = (True, "v1.0")
        ok = preflight_check(verbose=False)
        assert ok is True

    @patch("backend.services.setup_service._check_command")
    def test_required_missing(self, mock_check) -> None:
        # Node.js missing = required
        def side_effect(cmd: str):
            if cmd == "node":
                return (False, None)
            return (True, "v1.0")

        mock_check.side_effect = side_effect
        ok = preflight_check(verbose=False)
        assert ok is False

    @patch("backend.services.setup_service._check_command")
    def test_optional_missing_still_ok(self, mock_check) -> None:
        def side_effect(cmd: str):
            if cmd == "devtunnel":
                return (False, None)
            return (True, "v1.0")

        mock_check.side_effect = side_effect
        ok = preflight_check(verbose=False)
        assert ok is True
