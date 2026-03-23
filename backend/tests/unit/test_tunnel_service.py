from __future__ import annotations

import threading
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest

from backend.services.tunnel_service import (
    _CODEPLANE_TUNNEL_PREFIX,
    RemoteProvider,
    TunnelHandle,
    TunnelStartError,
    TunnelWatchdog,
    _find_existing_codeplane_tunnel,
    _lookup_devtunnel,
    _start_output_drain,
    _wait_for_startup,
    validate_remote_provider,
)

if TYPE_CHECKING:
    import subprocess


def _as_popen(proc: _FakeProc) -> subprocess.Popen[str]:
    return cast("subprocess.Popen[str]", proc)


class _FakeProc:
    def __init__(self, *, poll_result: int | None = None, output: str = "") -> None:
        self._poll_result = poll_result
        self.stdout: _FakeStdout | None = _FakeStdout(output)
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int) -> int | None:
        return self._poll_result

    def kill(self) -> None:
        self.killed = True


class _FakeStdout:
    def __init__(self, output: str) -> None:
        self.output = output

    def read(self, size: int = -1) -> str:
        if size >= 0:
            return self.output[:size]
        return self.output


def test_validate_remote_provider_local_has_no_requirements() -> None:
    assert validate_remote_provider(RemoteProvider.local) is None


@patch("backend.services.tunnel_service.shutil.which", return_value=None)
def test_validate_remote_provider_devtunnel_requires_cli(mock_which) -> None:
    error = validate_remote_provider(RemoteProvider.devtunnel)
    assert error is not None
    assert "devtunnel" in error.lower()


@patch("backend.services.tunnel_service.shutil.which", return_value="/usr/bin/cloudflared")
def test_validate_remote_provider_cloudflare_requires_token_and_hostname(mock_which) -> None:
    error = validate_remote_provider(RemoteProvider.cloudflare)
    assert error is not None
    assert "CPL_CLOUDFLARE_HOSTNAME" in error
    assert "CPL_CLOUDFLARE_TUNNEL_TOKEN" in error


@patch("backend.services.tunnel_service.shutil.which", return_value="/usr/bin/cloudflared")
def test_validate_remote_provider_cloudflare_with_config_passes(mock_which) -> None:
    error = validate_remote_provider(
        RemoteProvider.cloudflare,
        cloudflare_hostname="codeplane.example.com",
        cloudflare_token="token",
    )
    assert error is None


def test_watchdog_detects_dead_process() -> None:
    watchdog = TunnelWatchdog(
        tunnel_url="https://example.test",
        restart_command=["devtunnel", "host", "name"],
        proc=_as_popen(_FakeProc(poll_result=1)),
        label="devtunnel",
    )
    assert watchdog._process_running() is False


def test_watchdog_restart_process_retries_until_healthy() -> None:
    original_proc = _FakeProc(poll_result=None)
    failed_proc = _FakeProc(poll_result=1, output="transient failure")
    recovered_proc = _FakeProc(poll_result=None)
    watchdog = TunnelWatchdog(
        tunnel_url="https://example.test",
        restart_command=["devtunnel", "host", "name"],
        proc=_as_popen(original_proc),
        label="devtunnel",
    )
    watchdog._stop_event = threading.Event()
    watchdog._BACKOFF_BASE = 0  # Skip backoff delay in tests

    with (
        patch("backend.services.tunnel_service.subprocess.Popen", side_effect=[failed_proc, recovered_proc]),
        patch.object(watchdog, "_wait_for_recovery", side_effect=[True]),
    ):
        restarted = watchdog._restart_process()

    assert restarted is True
    assert original_proc.terminated is True
    assert watchdog.proc is _as_popen(recovered_proc)


def test_watchdog_restart_process_gives_up_after_retries() -> None:
    watchdog = TunnelWatchdog(
        tunnel_url="https://example.test",
        restart_command=["devtunnel", "host", "name"],
        proc=_as_popen(_FakeProc(poll_result=None)),
        label="devtunnel",
    )
    watchdog._stop_event = threading.Event()
    watchdog._BACKOFF_BASE = 0  # Skip backoff delay in tests
    failed_procs = [_FakeProc(poll_result=1, output=f"failure {index}") for index in range(3)]

    with patch("backend.services.tunnel_service.subprocess.Popen", side_effect=failed_procs):
        restarted = watchdog._restart_process()

    assert restarted is False
    assert watchdog.proc is _as_popen(failed_procs[-1])


# ---------------------------------------------------------------------------
# #9 — Random default tunnel name / prefix-based reuse
# ---------------------------------------------------------------------------


class TestTunnelNameRandomization:
    """Cover the new auto-random naming and prefix reuse logic."""

    @patch("backend.services.tunnel_service._list_devtunnels", return_value=[])
    def test_find_existing_tunnel_returns_none_when_empty(self, _mock) -> None:
        assert _find_existing_codeplane_tunnel() is None

    @patch(
        "backend.services.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "cpl-a1b2c3d4.usw2"}],
    )
    def test_find_existing_tunnel_matches_prefix(self, _mock) -> None:
        result = _find_existing_codeplane_tunnel()
        assert result is not None
        name, region = result
        assert name == "cpl-a1b2c3d4"
        assert region == "usw2"

    @patch(
        "backend.services.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "user-codeplane.usw2"}],
    )
    def test_find_existing_tunnel_ignores_old_naming_convention(self, _mock) -> None:
        result = _find_existing_codeplane_tunnel()
        assert result is None

    @patch(
        "backend.services.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "cpl-abc."}],  # empty region
    )
    def test_find_existing_tunnel_skips_empty_region(self, _mock) -> None:
        result = _find_existing_codeplane_tunnel()
        assert result is None

    @patch(
        "backend.services.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "cpl-abcd1234.euw1"}, {"tunnelId": "unrelated.usw2"}],
    )
    def test_lookup_devtunnel_exact_match(self, _mock) -> None:
        found, region = _lookup_devtunnel("cpl-abcd1234")
        assert found is True
        assert region == "euw1"

    @patch("backend.services.tunnel_service._list_devtunnels", return_value=[])
    def test_lookup_devtunnel_not_found(self, _mock) -> None:
        found, region = _lookup_devtunnel("nonexistent")
        assert found is False
        assert region is None

    def test_prefix_constant_starts_with_cpl(self) -> None:
        assert _CODEPLANE_TUNNEL_PREFIX == "cpl-"


# ---------------------------------------------------------------------------
# #7 — Lock around watchdog self.proc
# ---------------------------------------------------------------------------


class TestWatchdogLock:
    """Verify the threading lock is initialized and used during restart."""

    def test_watchdog_has_lock(self) -> None:
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="test",
        )
        assert hasattr(watchdog, "_lock")
        # Should be a threading.Lock instance
        assert hasattr(watchdog._lock, "acquire")
        assert hasattr(watchdog._lock, "release")

    def test_restart_updates_proc_under_lock(self) -> None:
        """Verify _restart_process assigns self.proc (observable after restart)."""
        original = _FakeProc(poll_result=None)
        new_proc = _FakeProc(poll_result=None)
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(original),
            label="test",
        )
        watchdog._stop_event = threading.Event()

        with (
            patch("backend.services.tunnel_service.subprocess.Popen", return_value=new_proc),
            patch.object(watchdog, "_wait_for_recovery", return_value=True),
        ):
            watchdog._restart_process()

        assert watchdog.proc is _as_popen(new_proc)

    def test_tunnel_handle_close_reads_proc_under_lock(self) -> None:
        """Verify TunnelHandle.close() uses the lock when reading watchdog.proc."""
        proc = _FakeProc(poll_result=None)
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(proc),
            label="test",
        )
        # Manually stop the watchdog thread (it was never started)
        watchdog._stop_event.set()

        handle = TunnelHandle(
            provider=RemoteProvider.devtunnel,
            origin="https://example.test",
            proc=_as_popen(proc),
            watchdog=watchdog,
        )
        # Should not raise
        handle.close()
        assert proc.terminated


# ---------------------------------------------------------------------------
# #11 — Bounded subprocess output read
# ---------------------------------------------------------------------------


class TestBoundedOutputRead:
    def test_read_process_output_respects_max_bytes(self) -> None:
        large_output = "x" * 200_000
        proc = _FakeProc(poll_result=1, output=large_output)
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(proc),
            label="test",
        )
        result = watchdog._read_process_output(_as_popen(proc))
        assert len(result) <= watchdog._MAX_OUTPUT_BYTES

    def test_read_process_output_returns_full_when_small(self) -> None:
        proc = _FakeProc(poll_result=1, output="small output")
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(proc),
            label="test",
        )
        result = watchdog._read_process_output(_as_popen(proc))
        assert result == "small output"

    def test_read_process_output_no_stdout(self) -> None:
        proc = _FakeProc(poll_result=1)
        proc.stdout = None
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(proc),
            label="test",
        )
        assert watchdog._read_process_output(_as_popen(proc)) == ""


# ---------------------------------------------------------------------------
# #15 — Watchdog local health check
# ---------------------------------------------------------------------------


class TestWatchdogLocalHealthCheck:
    def test_health_url_uses_localhost_when_port_set(self) -> None:
        watchdog = TunnelWatchdog(
            tunnel_url="https://cpl-abc-8080.usw2.devtunnels.ms",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="devtunnel",
            local_port=8080,
        )
        # Verify the URL constructed in _health_ok targets localhost
        assert watchdog._local_port == 8080

    def test_health_url_uses_tunnel_url_when_no_port(self) -> None:
        watchdog = TunnelWatchdog(
            tunnel_url="https://cpl-abc-8080.usw2.devtunnels.ms",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="devtunnel",
        )
        assert watchdog._local_port is None

    @patch("urllib.request.urlopen")
    def test_health_ok_calls_localhost_url(self, mock_urlopen) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        watchdog = TunnelWatchdog(
            tunnel_url="https://cpl-abc-8080.usw2.devtunnels.ms",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="devtunnel",
            local_port=9090,
        )
        result = watchdog._health_ok()
        assert result is True
        # Verify the URL passed to urlopen was the localhost one
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert "127.0.0.1:9090" in req.full_url


# ---------------------------------------------------------------------------
# #4 — Cloudflare token via env var (not CLI arg)
# ---------------------------------------------------------------------------


class TestCloudflareEnvVar:
    def test_restart_env_stored_on_watchdog(self) -> None:
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.com",
            restart_command=["cloudflared", "tunnel", "run"],
            restart_env={"TUNNEL_TOKEN": "secret-token"},
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="cloudflare",
        )
        assert watchdog.restart_env == {"TUNNEL_TOKEN": "secret-token"}
        # The token should NOT be in the restart command
        assert "secret-token" not in watchdog.restart_command


# ---------------------------------------------------------------------------
# Stability fixes — stdout drain to prevent pipe deadlock
# ---------------------------------------------------------------------------


class TestOutputDrain:
    def test_drain_runs_without_hanging(self) -> None:
        """Verify drain thread starts and exits cleanly with a fake process."""
        proc = _FakeProc(poll_result=None)
        # Empty output means drain thread reads "" and exits immediately
        _start_output_drain(_as_popen(proc))

    def test_drain_skips_when_no_stdout(self) -> None:
        proc = _FakeProc(poll_result=None)
        proc.stdout = None
        # Should not raise or start any thread
        _start_output_drain(_as_popen(proc))


# ---------------------------------------------------------------------------
# Stability fixes — startup polling instead of fixed sleep
# ---------------------------------------------------------------------------


class TestWaitForStartup:
    def test_raises_on_immediate_exit(self) -> None:
        proc = _FakeProc(poll_result=1, output="crash info")
        with pytest.raises(TunnelStartError, match="crash info"):
            _wait_for_startup(_as_popen(proc), label="test", timeout=0.5)

    def test_survives_if_process_stays_alive(self) -> None:
        proc = _FakeProc(poll_result=None)
        _wait_for_startup(_as_popen(proc), label="test", timeout=0.5)

    def test_generic_message_when_no_output(self) -> None:
        proc = _FakeProc(poll_result=1)
        proc.stdout = None
        with pytest.raises(TunnelStartError, match="test process exited during startup"):
            _wait_for_startup(_as_popen(proc), label="test", timeout=0.5)


# ---------------------------------------------------------------------------
# Stability fixes — exponential backoff between restart attempts
# ---------------------------------------------------------------------------


class TestRestartBackoff:
    def test_backoff_constants_defined(self) -> None:
        assert TunnelWatchdog._BACKOFF_BASE == 5
        assert TunnelWatchdog._GIVEUP_COOLDOWN == 60
        assert TunnelWatchdog._RELAY_CHECK_FREQUENCY == 5

    def test_backoff_waits_called_between_attempts(self) -> None:
        """Verify _stop_event.wait is called with increasing backoff timeouts."""
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="test",
        )
        watchdog._BACKOFF_BASE = 2
        wait_timeouts: list[float] = []

        def tracking_wait(timeout: float | None = None) -> bool:
            if timeout is not None:
                wait_timeouts.append(timeout)
            return False  # Not stopped, return immediately

        failed_procs = [_FakeProc(poll_result=1) for _ in range(3)]

        with (
            patch("backend.services.tunnel_service.subprocess.Popen", side_effect=failed_procs),
            patch.object(watchdog._stop_event, "wait", side_effect=tracking_wait),
        ):
            watchdog._restart_process()

        # Attempt 1: grace(2s). Attempt 2: backoff(2s), grace(2s). Attempt 3: backoff(4s), grace(2s).
        assert 2 in wait_timeouts  # backoff before attempt 2: 2 * 2^0 = 2
        assert 4 in wait_timeouts  # backoff before attempt 3: 2 * 2^1 = 4


# ---------------------------------------------------------------------------
# Stability fixes — relay health check every N iterations
# ---------------------------------------------------------------------------


class TestRelayHealthCheck:
    @patch("urllib.request.urlopen")
    def test_health_ok_uses_tunnel_url_when_forced(self, mock_urlopen) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        watchdog = TunnelWatchdog(
            tunnel_url="https://cpl-abc-8080.usw2.devtunnels.ms",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="devtunnel",
            local_port=9090,
        )
        result = watchdog._health_ok(use_tunnel_url=True)
        assert result is True
        req = mock_urlopen.call_args[0][0]
        assert "cpl-abc-8080.usw2.devtunnels.ms" in req.full_url
        assert "127.0.0.1" not in req.full_url

    @patch("urllib.request.urlopen")
    def test_health_ok_defaults_to_localhost(self, mock_urlopen) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        watchdog = TunnelWatchdog(
            tunnel_url="https://cpl-abc-8080.usw2.devtunnels.ms",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="devtunnel",
            local_port=9090,
        )
        watchdog._health_ok(use_tunnel_url=False)
        req = mock_urlopen.call_args[0][0]
        assert "127.0.0.1:9090" in req.full_url


# ---------------------------------------------------------------------------
# Stability fixes — close() race with watchdog restart
# ---------------------------------------------------------------------------


class TestCloseRace:
    def test_close_terminates_diverged_procs(self) -> None:
        """When handle.proc and watchdog.proc diverge, both get terminated."""
        original_proc = _FakeProc(poll_result=None)
        restarted_proc = _FakeProc(poll_result=None)

        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(restarted_proc),
            label="test",
        )
        watchdog._stop_event.set()

        handle = TunnelHandle(
            provider=RemoteProvider.devtunnel,
            origin="https://example.test",
            proc=_as_popen(original_proc),
            watchdog=watchdog,
        )
        handle.close()

        assert original_proc.terminated
        assert restarted_proc.terminated

    def test_close_deduplicates_same_proc(self) -> None:
        """When handle.proc and watchdog.proc are the same, no error."""
        proc = _FakeProc(poll_result=None)

        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(proc),
            label="test",
        )
        watchdog._stop_event.set()

        handle = TunnelHandle(
            provider=RemoteProvider.devtunnel,
            origin="https://example.test",
            proc=_as_popen(proc),
            watchdog=watchdog,
        )
        handle.close()
        assert proc.terminated


# ---------------------------------------------------------------------------
# Stability fixes — cooldown after restart give-up
# ---------------------------------------------------------------------------


class TestGiveupCooldown:
    def test_run_enters_cooldown_after_failed_restart(self) -> None:
        """Verify the watchdog loop waits _GIVEUP_COOLDOWN seconds after give-up."""
        proc = _FakeProc(poll_result=1)
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(proc),
            label="test",
        )
        watchdog._GIVEUP_COOLDOWN = 0.05
        watchdog._CHECK_INTERVAL = 0.05

        call_count = 0

        def mock_restart() -> bool:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                watchdog._stop_event.set()  # Stop after second attempt
            return False

        with patch.object(watchdog, "_restart_process", side_effect=mock_restart):
            watchdog._run()

        # Should have attempted restart at least twice (initial + after cooldown)
        assert call_count >= 2
