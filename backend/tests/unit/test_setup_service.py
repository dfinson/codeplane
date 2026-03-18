"""Tests for setup_service — dependency checks, env var handling."""

from __future__ import annotations

from unittest.mock import patch

from backend.services.setup_service import (
    AgentCLIStatus,
    CheckResult,
    CheckStatus,
    _check_command,
    _get_env_persistence_instructions,
    _offer_inline_fix,
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


class TestOfferInlineFix:
    _CLAUDE_NOT_ON_PATH = AgentCLIStatus(
        "claude",
        "Claude Code",
        True,
        False,
        False,
        "Python SDK installed, claude CLI not on PATH",
        "Install CLI: npm install -g @anthropic-ai/claude-code",
    )

    def test_successful_fix_returns_true(self) -> None:
        warning = CheckResult(
            label="Claude Code",
            status=CheckStatus.warn,
            detail="Python SDK installed, claude CLI not on PATH",
            category="agent",
        )

        with (
            patch(
                "backend.services.setup_service.check_agent_cli",
                return_value=self._CLAUDE_NOT_ON_PATH,
            ),
            patch(
                "backend.services.setup_service._prompt_select",
                return_value=("fix", ["npm", "install", "-g", "@anthropic-ai/claude-code"]),
            ),
            patch("backend.services.setup_service.subprocess.run") as mock_run,
        ):
            mock_run.return_value = type("Result", (), {"returncode": 0, "stderr": ""})()
            assert _offer_inline_fix(warning) is True

    def test_failed_fix_then_continue_returns_false(self) -> None:
        warning = CheckResult(
            label="Claude Code",
            status=CheckStatus.warn,
            detail="Python SDK installed, claude CLI not on PATH",
            category="agent",
        )

        with (
            patch(
                "backend.services.setup_service.check_agent_cli",
                return_value=self._CLAUDE_NOT_ON_PATH,
            ),
            patch(
                "backend.services.setup_service._prompt_select",
                side_effect=[
                    ("fix", ["npm", "install", "-g", "@anthropic-ai/claude-code"]),
                    "continue",
                ],
            ),
            patch("backend.services.setup_service.subprocess.run") as mock_run,
        ):
            mock_run.return_value = type("Result", (), {"returncode": 243, "stderr": "npm error code EACCES"})()
            assert _offer_inline_fix(warning) is False

    def test_failed_fix_then_abort_raises(self) -> None:
        warning = CheckResult(
            label="Claude Code",
            status=CheckStatus.warn,
            detail="Python SDK installed, claude CLI not on PATH",
            category="agent",
        )

        with (
            patch(
                "backend.services.setup_service.check_agent_cli",
                return_value=self._CLAUDE_NOT_ON_PATH,
            ),
            patch(
                "backend.services.setup_service._prompt_select",
                side_effect=[
                    ("fix", ["npm", "install", "-g", "@anthropic-ai/claude-code"]),
                    "abort",
                ],
            ),
            patch("backend.services.setup_service.subprocess.run") as mock_run,
        ):
            mock_run.return_value = type("Result", (), {"returncode": 243, "stderr": "npm error code EACCES"})()
            try:
                _offer_inline_fix(warning)
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError("Expected SystemExit")

    def test_failed_fix_then_recheck_succeeds(self) -> None:
        warning = CheckResult(
            label="Claude Code",
            status=CheckStatus.warn,
            detail="Python SDK installed, claude CLI not on PATH",
            category="agent",
        )
        cli_fixed = AgentCLIStatus(
            "claude",
            "Claude Code",
            True,
            True,
            True,
            "claude CLI and SDK installed",
            "",
        )

        with (
            patch(
                "backend.services.setup_service.check_agent_cli",
                side_effect=[self._CLAUDE_NOT_ON_PATH, cli_fixed],
            ),
            patch(
                "backend.services.setup_service._prompt_select",
                side_effect=[
                    ("fix", ["npm", "install", "-g", "@anthropic-ai/claude-code"]),
                    "recheck",
                ],
            ),
            patch("backend.services.setup_service.subprocess.run") as mock_run,
        ):
            mock_run.return_value = type("Result", (), {"returncode": 243, "stderr": "npm error"})()
            assert _offer_inline_fix(warning) is True
