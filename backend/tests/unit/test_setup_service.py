"""Tests for setup_service — dependency checks, env var handling."""

from __future__ import annotations

import errno
from unittest.mock import patch

from backend.services.setup_service import (
    AgentAuthStatus,
    AgentCLIStatus,
    CheckResult,
    CheckStatus,
    _build_agent_check_result,
    _check_command,
    _check_port,
    _check_server_running,
    _find_cpl_processes,
    _get_env_persistence_instructions,
    _offer_inline_fix,
    _should_prompt_for_warning,
    verify_requirements,
)


class _FakeSocket:
    def __init__(self, *, connect_result: int = errno.ECONNREFUSED, bind_error: OSError | None = None) -> None:
        self._connect_result = connect_result
        self._bind_error = bind_error

    def settimeout(self, timeout: float) -> None:
        return None

    def setsockopt(self, level: int, optname: int, value: int) -> None:
        return None

    def connect_ex(self, address: tuple[str, int] | tuple[str, int, int, int]) -> int:
        return self._connect_result

    def bind(self, address: tuple[str, int]) -> None:
        if self._bind_error is not None:
            raise self._bind_error

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


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
        results = verify_requirements()
        assert not any(r.status == CheckStatus.fail for r in results)

    @patch("backend.services.setup_service._check_command")
    def test_required_missing(self, mock_check) -> None:
        # Node.js missing = required
        def side_effect(cmd: str):
            if cmd == "node":
                return (False, None)
            return (True, "v1.0")

        mock_check.side_effect = side_effect
        results = verify_requirements()
        assert any(r.status == CheckStatus.fail for r in results)

    @patch("backend.services.setup_service._check_command")
    def test_optional_missing_still_ok(self, mock_check) -> None:
        def side_effect(cmd: str):
            if cmd == "devtunnel":
                return (False, None)
            return (True, "v1.0")

        mock_check.side_effect = side_effect
        results = verify_requirements()
        assert not any(r.status == CheckStatus.fail for r in results)

    @patch("backend.services.setup_service._check_command")
    def test_optional_dependencies_can_be_omitted(self, mock_check) -> None:
        mock_check.return_value = (True, "v1.0")

        results = verify_requirements(include_optional_dependencies=False)

        assert all(r.label != "Dev Tunnels CLI" for r in results)


class TestCheckPort:
    @patch("backend.services.setup_service.socket.has_ipv6", False)
    @patch("backend.services.setup_service.socket.socket")
    def test_listener_is_reported_in_use(self, mock_socket) -> None:
        mock_socket.side_effect = [_FakeSocket(connect_result=0)]
        assert _check_port(8080) == (False, "in use")

    @patch("backend.services.setup_service.socket.has_ipv6", False)
    @patch("backend.services.setup_service.socket.socket")
    def test_refused_then_bind_success_is_available(self, mock_socket) -> None:
        mock_socket.side_effect = [
            _FakeSocket(connect_result=errno.ECONNREFUSED),
            _FakeSocket(),
        ]
        assert _check_port(8080) == (True, "available")

    @patch("backend.services.setup_service.socket.has_ipv6", False)
    @patch("backend.services.setup_service.socket.socket")
    def test_bind_failure_without_listener_is_not_reported_in_use(self, mock_socket) -> None:
        mock_socket.side_effect = [
            _FakeSocket(connect_result=errno.ECONNREFUSED),
            _FakeSocket(bind_error=OSError(errno.EADDRINUSE, "Address already in use")),
        ]
        assert _check_port(8080) == (False, "unavailable")


class TestAgentCheckResult:
    @patch("backend.services.setup_service._check_agent_auth")
    @patch("backend.services.setup_service.check_agent_cli")
    def test_ready_but_unauthenticated_agent_is_warning(self, mock_check_agent_cli, mock_check_agent_auth) -> None:
        mock_check_agent_cli.return_value = AgentCLIStatus(
            "copilot", "GitHub Copilot", True, True, True, "github-copilot-sdk 0.1.0", ""
        )
        mock_check_agent_auth.return_value = AgentAuthStatus(
            "copilot", False, "not authenticated", "Run: gh auth login"
        )

        result = _build_agent_check_result("copilot")

        assert result.status == CheckStatus.warn
        assert result.category == "agent_auth"
        assert "auth not detected" in result.detail
        assert result.hint == "Run: gh auth login"

    @patch("backend.services.setup_service._check_agent_auth")
    @patch("backend.services.setup_service.check_agent_cli")
    def test_ready_agent_with_unknown_auth_stays_passed(self, mock_check_agent_cli, mock_check_agent_auth) -> None:
        mock_check_agent_cli.return_value = AgentCLIStatus(
            "claude", "Claude Code", True, True, True, "claude CLI and SDK installed", ""
        )
        mock_check_agent_auth.return_value = AgentAuthStatus("claude", None, "unknown")

        result = _build_agent_check_result("claude")

        assert result.status == CheckStatus.passed
        assert result.category == "agent"


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
            assert _offer_inline_fix(warning) == "fixed"

    def test_failed_fix_then_continue_returns_continued(self) -> None:
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
            assert _offer_inline_fix(warning) == "continued"

    def test_explicit_skip_returns_skipped(self) -> None:
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
                return_value=("skip", []),
            ),
        ):
            assert _offer_inline_fix(warning) == "skipped"

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
            assert _offer_inline_fix(warning) == "fixed"


class TestPromptSuppression:
    def test_non_agent_warning_does_not_prompt(self) -> None:
        warning = CheckResult(label="GitHub Copilot", status=CheckStatus.warn, category="agent_auth")
        assert _should_prompt_for_warning(warning, "copilot", []) is False

    def test_default_agent_warning_still_prompts(self) -> None:
        warning = CheckResult(label="GitHub Copilot", status=CheckStatus.warn, category="agent")
        assert _should_prompt_for_warning(warning, "copilot", ["copilot"]) is True

    def test_inactive_agent_warning_prompts_if_not_suppressed(self) -> None:
        warning = CheckResult(label="Claude Code", status=CheckStatus.warn, category="agent")
        assert _should_prompt_for_warning(warning, "copilot", []) is True

    @patch("backend.services.setup_service.check_agent_cli")
    def test_inactive_agent_warning_is_suppressed_when_default_is_ready(self, mock_check_agent_cli) -> None:
        warning = CheckResult(label="Claude Code", status=CheckStatus.warn, category="agent")
        mock_check_agent_cli.return_value = AgentCLIStatus(
            "copilot", "GitHub Copilot", True, True, True, "github-copilot-sdk 0.1.0", ""
        )
        assert _should_prompt_for_warning(warning, "copilot", ["claude"]) is False

    @patch("backend.services.setup_service.check_agent_cli")
    def test_inactive_agent_warning_still_prompts_when_default_is_not_ready(self, mock_check_agent_cli) -> None:
        warning = CheckResult(label="Claude Code", status=CheckStatus.warn, category="agent")
        mock_check_agent_cli.return_value = AgentCLIStatus(
            "copilot", "GitHub Copilot", False, False, False, "not installed", "Install: uv add github-copilot-sdk"
        )
        assert _should_prompt_for_warning(warning, "copilot", ["claude"]) is True


# ---------------------------------------------------------------------------
# _find_cpl_processes
# ---------------------------------------------------------------------------


class TestFindCplProcesses:
    @patch("platform.system", return_value="Linux")
    @patch("subprocess.run")
    @patch("os.getpid", return_value=9999)
    def test_finds_cpl_up_process(self, _mock_pid, mock_run, _mock_sys) -> None:
        mock_run.return_value = type(
            "R",
            (),
            {
                "stdout": "  1234 uv run cpl up --host 127.0.0.1\n  5678 grep cpl\n",
                "returncode": 0,
            },
        )()
        pids = _find_cpl_processes()
        assert 1234 in pids

    @patch("platform.system", return_value="Linux")
    @patch("subprocess.run")
    def test_excludes_doctor_process(self, mock_run, _mock_sys) -> None:
        mock_run.return_value = type(
            "R",
            (),
            {
                "stdout": "  9999 python -m backend.cli doctor\n",
                "returncode": 0,
            },
        )()
        pids = _find_cpl_processes()
        assert 9999 not in pids

    @patch("platform.system", return_value="Linux")
    @patch("subprocess.run")
    def test_finds_cpl_restart_process(self, mock_run, _mock_sys) -> None:
        mock_run.return_value = type(
            "R",
            (),
            {
                "stdout": "  4321 uv run cpl restart --remote\n",
                "returncode": 0,
            },
        )()
        pids = _find_cpl_processes()
        assert 4321 in pids

    @patch("platform.system", return_value="Linux")
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_returns_empty_on_missing_ps(self, _mock_run, _mock_sys) -> None:
        assert _find_cpl_processes() == []


# ---------------------------------------------------------------------------
# _check_server_running
# ---------------------------------------------------------------------------


class TestCheckServerRunning:
    @patch("urllib.request.urlopen")
    def test_health_endpoint_reachable(self, mock_urlopen) -> None:
        import json

        body = json.dumps({"version": "1.0", "uptimeSeconds": 42, "activeJobs": 1, "queuedJobs": 0}).encode()
        resp = type(
            "Resp",
            (),
            {
                "read": lambda self: body,
                "status": 200,
                "__enter__": lambda s: s,
                "__exit__": lambda *a: None,
            },
        )()
        mock_urlopen.return_value = resp
        running, detail = _check_server_running("127.0.0.1", 8080)
        assert running is True
        assert "v1.0" in detail

    @patch("backend.services.setup_service._find_cpl_processes", return_value=[1234])
    @patch("urllib.request.urlopen", side_effect=OSError("refused"))
    def test_falls_back_to_process_scan(self, _mock_url, _mock_procs) -> None:
        running, detail = _check_server_running("127.0.0.1", 8080)
        assert running is True
        assert "1234" in detail

    @patch("backend.services.setup_service._find_cpl_processes", return_value=[])
    @patch("urllib.request.urlopen", side_effect=OSError("refused"))
    def test_not_running(self, _mock_url, _mock_procs) -> None:
        running, _ = _check_server_running("127.0.0.1", 8080)
        assert running is False
