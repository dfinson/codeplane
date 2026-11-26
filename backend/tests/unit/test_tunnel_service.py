from __future__ import annotations

import base64
import json
import subprocess as real_subprocess
import sys
import threading
import urllib.error
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest

from backend.services.sharing import tunnel_service
from backend.services.sharing.tunnel_service import (
    _CODEPLANE_TUNNEL_PREFIX,
    RemoteProvider,
    TunnelHandle,
    TunnelOwnership,
    TunnelStartError,
    TunnelWatchdog,
    _cloudflared_already_running,
    _external_tunnel_origin,
    _find_existing_codeplane_tunnel,
    _list_devtunnels,
    _lookup_devtunnel,
    _run_capture,
    _start_devtunnel,
    _start_output_drain,
    _terminate_and_reap,
    _wait_for_startup,
    devtunnel_logged_in,
    start_remote_access,
    validate_remote_provider,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_tunnel_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tunnel_service, "get_codeplane_dir", lambda: tmp_path)
    monkeypatch.setattr(tunnel_service, "_DEVTUNNEL_FALLBACK_PATHS", ())


def _as_popen(proc: _FakeProc) -> subprocess.Popen[str]:
    return cast("subprocess.Popen[str]", proc)


@pytest.fixture(autouse=True)
def _stop_leaked_watchdogs() -> Iterator[None]:
    """Stop every watchdog a test starts, so none outlives it.

    ``start_remote_access`` starts a real daemon thread. Tests that exercise
    it never stopped one, so after ``_INITIAL_DELAY`` the surviving thread ran
    a live health check and wrote ``tunnel_health_check_failed`` to stdout —
    landing inside whichever test happened to be running, which corrupted the
    JSON that ``cpl doctor --json`` prints and failed an unrelated test.
    """
    started: list[TunnelWatchdog] = []
    real_start = TunnelWatchdog.start

    def _tracking_start(self: TunnelWatchdog) -> None:
        started.append(self)
        real_start(self)

    with patch.object(TunnelWatchdog, "start", _tracking_start):
        yield
    for watchdog in started:
        watchdog.stop()


class _FakeProc:
    _next_pid = 900000

    def __init__(self, *, poll_result: int | None = None, output: str = "") -> None:
        self._poll_result = poll_result
        self.stdout: _FakeStdout | None = _FakeStdout(output)
        self.terminated = False
        self.killed = False
        # Real Popen objects always expose a pid; connector lifecycle helpers
        # (orphan prevention, process-group signalling) read it.
        _FakeProc._next_pid += 1
        self.pid = _FakeProc._next_pid

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


@patch("backend.services.sharing.tunnel_service.shutil.which", return_value=None)
def test_validate_remote_provider_devtunnel_requires_cli(mock_which) -> None:
    error = validate_remote_provider(RemoteProvider.devtunnel)
    assert error is not None
    assert "devtunnel" in error.lower()


@patch("backend.services.sharing.tunnel_service.shutil.which", return_value="/usr/bin/cloudflared")
def test_validate_remote_provider_cloudflare_requires_token_and_hostname(mock_which) -> None:
    error = validate_remote_provider(RemoteProvider.cloudflare)
    assert error is not None
    assert "CPL_CLOUDFLARE_HOSTNAME" in error
    assert "CPL_CLOUDFLARE_TUNNEL_TOKEN" in error


@patch("backend.services.sharing.tunnel_service.shutil.which", return_value="/usr/bin/cloudflared")
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
        patch("backend.services.sharing.tunnel_service.subprocess.Popen", side_effect=[failed_proc, recovered_proc]),
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

    with patch("backend.services.sharing.tunnel_service.subprocess.Popen", side_effect=failed_procs):
        restarted = watchdog._restart_process()

    assert restarted is False
    assert watchdog.proc is _as_popen(failed_procs[-1])


# ---------------------------------------------------------------------------
# #9 — Random default tunnel name / prefix-based reuse
# ---------------------------------------------------------------------------


class TestTunnelNameRandomization:
    """Cover the new auto-random naming and prefix reuse logic."""

    @patch("backend.services.sharing.tunnel_service._list_devtunnels", return_value=[])
    def test_find_existing_tunnel_returns_none_when_empty(self, _mock) -> None:
        assert _find_existing_codeplane_tunnel() is None

    @patch(
        "backend.services.sharing.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "cpl-a1b2c3d4.usw2"}],
    )
    def test_find_existing_tunnel_matches_prefix(self, _mock) -> None:
        result = _find_existing_codeplane_tunnel()
        assert result is not None
        name, region = result
        assert name == "cpl-a1b2c3d4"
        assert region == "usw2"

    @patch(
        "backend.services.sharing.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "user-codeplane.usw2"}],
    )
    def test_find_existing_tunnel_ignores_old_naming_convention(self, _mock) -> None:
        result = _find_existing_codeplane_tunnel()
        assert result is None

    @patch(
        "backend.services.sharing.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "cpl-abc."}],  # empty region
    )
    def test_find_existing_tunnel_skips_empty_region(self, _mock) -> None:
        result = _find_existing_codeplane_tunnel()
        assert result is None

    @patch(
        "backend.services.sharing.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "cpl-abcd1234.euw1"}, {"tunnelId": "unrelated.usw2"}],
    )
    def test_lookup_devtunnel_exact_match(self, _mock) -> None:
        found, region = _lookup_devtunnel("cpl-abcd1234")
        assert found is True
        assert region == "euw1"

    @patch("backend.services.sharing.tunnel_service._list_devtunnels", return_value=[])
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
            patch("backend.services.sharing.tunnel_service.subprocess.Popen", return_value=new_proc),
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


class TestWatchdogRelayProbeUnderAccessControl:
    """The relay probe must not treat an access-control rejection as a fault.

    Observed live: with Cloudflare Access in front of the hostname, the relay
    probe (urllib, no session cookie, no browser User-Agent) is answered with
    403. ``urllib`` raises ``HTTPError``, which subclasses ``URLError``, so the
    old blanket ``except`` reported the relay as broken on every single check.
    The watchdog then restarted a perfectly healthy connector roughly every
    100s, and each restart took the public hostname down for a few seconds --
    surfacing to users as intermittent 502s.
    """

    @staticmethod
    def _watchdog() -> TunnelWatchdog:
        return TunnelWatchdog(
            tunnel_url="https://example.codeplane.dev",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="cloudflare",
            local_port=8080,
        )

    @staticmethod
    def _http_error(code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            url="https://example.codeplane.dev/api/health",
            code=code,
            msg="err",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    @pytest.mark.parametrize("code", [401, 403, 404, 200, 302])
    def test_relay_probe_treats_a_definite_status_as_reachable(self, code: int) -> None:
        watchdog = self._watchdog()
        with patch("urllib.request.urlopen", side_effect=self._http_error(code)):
            assert watchdog._health_ok(use_tunnel_url=True) is True

    @pytest.mark.parametrize("code", [500, 502, 503, 504, 530, 599])
    def test_relay_probe_still_fails_on_server_error(self, code: int) -> None:
        watchdog = self._watchdog()
        with patch("urllib.request.urlopen", side_effect=self._http_error(code)):
            assert watchdog._health_ok(use_tunnel_url=True) is False

    @pytest.mark.parametrize("code", [500, 502, 503, 504, 530, 599])
    def test_relay_probe_fails_on_server_error_returned_as_a_response(self, code: int) -> None:
        """Some stacks return the status instead of raising; both paths must agree."""
        watchdog = self._watchdog()
        resp = MagicMock()
        resp.status = code
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=resp):
            assert watchdog._health_ok(use_tunnel_url=True) is False

    def test_relay_probe_still_fails_when_the_edge_is_unreachable(self) -> None:
        watchdog = self._watchdog()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
            assert watchdog._health_ok(use_tunnel_url=True) is False

    @pytest.mark.parametrize("code", [401, 403, 500, 502])
    def test_local_origin_probe_is_unchanged_and_still_demands_200(self, code: int) -> None:
        """The relaxation applies only to the relay probe.

        The local origin is not behind Access, so anything other than 200 there
        is a genuine origin failure and must keep suppressing restarts.
        """
        watchdog = self._watchdog()
        with patch("urllib.request.urlopen", side_effect=self._http_error(code)):
            assert watchdog._health_ok() is False
            assert watchdog._origin_ok() is False

    @pytest.mark.parametrize(("code", "restarts"), [(403, 0), (530, 2)])
    def test_relay_recovery_distinguishes_access_gates_from_missing_connectors(self, code: int, restarts: int) -> None:
        watchdog = self._watchdog()
        with (
            patch("urllib.request.urlopen", side_effect=self._http_error(code)),
            patch.object(watchdog, "_restart_process") as restart,
            patch.object(watchdog, "_origin_ok", return_value=True),
            patch.object(watchdog._stop_event, "wait") as wait,
        ):
            cycles = (watchdog._FAIL_THRESHOLD + 2) * watchdog._RELAY_CHECK_FREQUENCY
            wait.side_effect = [False] * cycles + [True]
            watchdog._run()
            assert restart.call_count == restarts
            assert wait.call_count == cycles + 1


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

    def test_a_watchdog_without_a_local_port_can_still_restart(self) -> None:
        """``local_port`` is optional, and the origin gate must not swallow that.

        The restart gate suppresses restarts while the *origin* is down. With
        no local port there is no origin probe, and reporting "origin down"
        would classify every relay failure as somebody else's problem, making
        the class permanently incapable of the restart it exists to perform.
        """
        watchdog = TunnelWatchdog(
            tunnel_url="https://cpl-abc-8080.usw2.devtunnels.ms",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="devtunnel",
        )
        with patch.object(watchdog, "_health_ok", return_value=False) as probe:
            assert watchdog._origin_ok() is True
        assert probe.call_count == 0


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
            patch("backend.services.sharing.tunnel_service.subprocess.Popen", side_effect=failed_procs),
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
        watchdog._INITIAL_DELAY = 0
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


class TestWatchdogRestartTrigger:
    """A connector restart must only happen for a failure a restart can repair.

    The watchdog exists to restart the connector when the public relay stops
    forwarding. Counting a failing *origin* check toward the same threshold
    made a slow local startup look like a tunnel fault: observed on Windows,
    where application startup outlasts ``_INITIAL_DELAY`` and two origin
    checks failed in a row, tearing down and respawning a healthy connector
    while the server was still booting.
    """

    def _watchdog(self, health_results: list[bool]) -> tuple[TunnelWatchdog, list[bool]]:
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(_FakeProc(poll_result=None)),
            label="test",
            local_port=8080,
        )
        watchdog._INITIAL_DELAY = 0
        watchdog._CHECK_INTERVAL = 0
        return watchdog, health_results

    def test_origin_down_never_restarts_the_connector(self) -> None:
        watchdog, _ = self._watchdog([])
        checks = 0

        def _health(*, use_tunnel_url: bool = False) -> bool:
            nonlocal checks
            checks += 1
            if checks >= 6:
                watchdog._stop_event.set()
            return False  # origin never answers; connector is alive

        with (
            patch.object(watchdog, "_health_ok", side_effect=_health),
            patch.object(watchdog, "_restart_process", return_value=True) as mock_restart,
        ):
            watchdog._run()

        assert mock_restart.call_count == 0

    def test_relay_failure_with_healthy_origin_restarts_the_connector(self) -> None:
        watchdog, _ = self._watchdog([])
        watchdog._RELAY_CHECK_FREQUENCY = 1

        def _health(*, use_tunnel_url: bool = False) -> bool:
            # Origin healthy, public relay broken — the connector is at fault.
            return not use_tunnel_url

        def _restart() -> bool:
            watchdog._stop_event.set()
            return True

        with (
            patch.object(watchdog, "_health_ok", side_effect=_health),
            patch.object(watchdog, "_restart_process", side_effect=_restart) as mock_restart,
        ):
            watchdog._run()

        assert mock_restart.call_count == 1

    def test_relay_failure_restarts_with_the_production_check_frequency(self) -> None:
        """The relay tally must survive the cycles that do not probe the relay.

        ``_RELAY_CHECK_FREQUENCY`` is 5 in production, so four of every five
        cycles carry no relay evidence. Clearing the tally on those cycles kept
        it from ever reaching ``_FAIL_THRESHOLD``, which made connector
        restarts unreachable outside a test that set the frequency to 1 -- the
        watchdog's entire purpose, silently disabled.
        """
        watchdog, _ = self._watchdog([])
        assert watchdog._RELAY_CHECK_FREQUENCY > 1, "production frequency must exceed 1 for this to be meaningful"
        cycles = 0

        def _health(*, use_tunnel_url: bool = False) -> bool:
            nonlocal cycles
            if not use_tunnel_url:
                cycles += 1
                if cycles > watchdog._RELAY_CHECK_FREQUENCY * (watchdog._FAIL_THRESHOLD + 2):
                    watchdog._stop_event.set()
                return True  # origin healthy throughout
            return False  # public relay never forwards

        def _restart() -> bool:
            watchdog._stop_event.set()
            return True

        with (
            patch.object(watchdog, "_health_ok", side_effect=_health),
            patch.object(watchdog, "_restart_process", side_effect=_restart) as mock_restart,
        ):
            watchdog._run()

        assert mock_restart.call_count == 1

    def test_dead_connector_still_restarts_immediately(self) -> None:
        watchdog, _ = self._watchdog([])
        watchdog.proc = _as_popen(_FakeProc(poll_result=1))

        def _restart() -> bool:
            watchdog._stop_event.set()
            return True

        with (
            patch.object(watchdog, "_health_ok", return_value=True),
            patch.object(watchdog, "_restart_process", side_effect=_restart) as mock_restart,
        ):
            watchdog._run()

        assert mock_restart.call_count == 1


class TestCloseIsSingleOwner:
    """Shutdown calls ``close()`` from three places, two of them off-thread.

    ``cpl up`` closes the tunnel from the second-signal handler, from a
    force-exit timer thread, and from the normal ``finally``. Without a guard
    each caller ran its own terminate/wait/kill sequence against the same pids,
    and a connector spawned by an in-flight watchdog restart could be published
    after ``close()`` had already taken its snapshot -- surviving shutdown.
    """

    def test_repeated_close_terminates_the_connector_once(self) -> None:
        proc = _FakeProc(poll_result=None)
        handle = TunnelHandle(provider=RemoteProvider.devtunnel, proc=_as_popen(proc))

        with patch("backend.services.sharing.tunnel_service._terminate_and_reap") as mock_kill:
            handle.close()
            handle.close()
            handle.close()

        assert mock_kill.call_count == 1

    def test_concurrent_close_terminates_the_connector_once(self) -> None:
        proc = _FakeProc(poll_result=None)
        handle = TunnelHandle(provider=RemoteProvider.devtunnel, proc=_as_popen(proc))
        start = threading.Event()

        def _closer() -> None:
            start.wait(timeout=5)
            handle.close()

        with patch("backend.services.sharing.tunnel_service._terminate_and_reap") as mock_kill:
            threads = [threading.Thread(target=_closer) for _ in range(4)]
            for thread in threads:
                thread.start()
            start.set()
            for thread in threads:
                thread.join(timeout=5)

        assert mock_kill.call_count == 1

    def test_a_connector_spawned_after_stop_is_not_left_running(self) -> None:
        """The watchdog must not adopt a connector published after ``stop()``."""
        original = _FakeProc(poll_result=1)
        replacement = _FakeProc(poll_result=None)
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.test",
            restart_command=["echo"],
            proc=_as_popen(original),
            label="test",
            local_port=8080,
        )
        killed: list[object] = []

        def _spawn(*_args: object, **_kwargs: object) -> object:
            # close() wins the race: it set the stop event and read self.proc
            # while this restart was still spawning a replacement.
            watchdog._stop_event.set()
            return _as_popen(replacement)

        with (
            patch("backend.services.sharing.tunnel_service._spawn_connector", side_effect=_spawn),
            patch(
                "backend.services.sharing.tunnel_service._terminate_and_reap",
                side_effect=lambda p, **_kw: killed.append(p),
            ),
        ):
            watchdog._restart_process()

        assert watchdog.proc is not replacement, "the post-stop connector was adopted as the live one"
        assert replacement in killed, "the connector spawned after stop was never terminated"


# ---------------------------------------------------------------------------


class TestExternalTunnelOwnership:
    """``ownership=external`` must never spawn a connector or scan processes."""

    def test_external_cloudflare_resolves_origin_without_process_or_scan(self) -> None:
        with (
            patch("backend.services.sharing.tunnel_service.subprocess.Popen") as mock_popen,
            patch("backend.services.sharing.tunnel_service._cloudflared_already_running") as mock_scan,
        ):
            handle = start_remote_access(
                RemoteProvider.cloudflare,
                port=8080,
                cloudflare_hostname="codeplane.example.com",
                cloudflare_token="tok",
                ownership=TunnelOwnership.external,
            )

        mock_popen.assert_not_called()
        mock_scan.assert_not_called()
        assert handle.origin == "https://codeplane.example.com"
        assert handle.proc is None
        assert handle.watchdog is None
        assert handle.externally_managed is True

    def test_external_cloudflare_requires_hostname(self) -> None:
        with pytest.raises(TunnelStartError):
            start_remote_access(
                RemoteProvider.cloudflare,
                port=8080,
                cloudflare_hostname=None,
                ownership=TunnelOwnership.external,
            )

    @patch(
        "backend.services.sharing.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "my-stable-name.usw2"}],
    )
    def test_external_devtunnel_resolves_origin_without_spawning(self, _mock) -> None:
        with (
            patch("backend.services.sharing.tunnel_service.subprocess.Popen") as mock_popen,
            patch.object(
                tunnel_service, "_run_capture", return_value=real_subprocess.CompletedProcess([], 0, "{}", "")
            ),
        ):
            handle = start_remote_access(
                RemoteProvider.devtunnel,
                port=8080,
                tunnel_name="my-stable-name",
                ownership=TunnelOwnership.external,
            )

        mock_popen.assert_not_called()
        assert handle.origin == "https://my-stable-name-8080.usw2.devtunnels.ms"
        assert handle.proc is None
        assert handle.externally_managed is True

    @patch("backend.services.sharing.tunnel_service._list_devtunnels", return_value=[])
    def test_external_devtunnel_unknown_name_raises(self, _mock) -> None:
        with pytest.raises(TunnelStartError):
            start_remote_access(
                RemoteProvider.devtunnel,
                port=8080,
                tunnel_name="does-not-exist",
                ownership=TunnelOwnership.external,
            )

    def test_external_devtunnel_requires_explicit_name(self) -> None:
        with pytest.raises(TunnelStartError):
            _external_tunnel_origin(RemoteProvider.devtunnel, port=8080, cloudflare_hostname=None, tunnel_name=None)

    def test_external_unsupported_provider_raises(self) -> None:
        with pytest.raises(TunnelStartError):
            _external_tunnel_origin(RemoteProvider.local, port=8080, cloudflare_hostname=None, tunnel_name=None)


class TestManagedTunnelOwnership:
    """``ownership=managed`` must always start and own a fresh connector."""

    def test_managed_cloudflare_never_calls_reuse_scan(self) -> None:
        proc = _FakeProc(poll_result=None)
        with (
            patch("backend.services.sharing.tunnel_service.subprocess.Popen", return_value=_as_popen(proc)),
            patch("backend.services.sharing.tunnel_service._cloudflared_already_running") as mock_scan,
            patch("backend.services.sharing.tunnel_service._wait_for_startup"),
            patch("backend.services.sharing.tunnel_service._start_output_drain"),
        ):
            handle = start_remote_access(
                RemoteProvider.cloudflare,
                port=8080,
                cloudflare_hostname="codeplane.example.com",
                cloudflare_token="tok",
                ownership=TunnelOwnership.managed,
            )

        # Managed ownership must never consult the legacy process-scan heuristic —
        # it always starts and owns its own connector.
        mock_scan.assert_not_called()
        assert handle.origin == "https://codeplane.example.com"
        assert handle.proc is _as_popen(proc)
        assert handle.externally_managed is False
        assert handle.watchdog is not None

    def test_legacy_none_ownership_preserves_reuse_scan_behavior(self) -> None:
        """Back-compat: omitting ``ownership`` keeps today's auto-detect behavior."""
        with patch(
            "backend.services.sharing.tunnel_service._cloudflared_already_running", return_value=True
        ) as mock_scan:
            handle = start_remote_access(
                RemoteProvider.cloudflare,
                port=8080,
                cloudflare_hostname="codeplane.example.com",
                cloudflare_token="tok",
            )

        mock_scan.assert_called_once()
        assert handle.proc is None
        assert handle.externally_managed is True


class TestOriginReusability:
    """``origin_is_reusable`` distinguishes stable identities from generated ones."""

    def test_cloudflare_origin_is_always_reusable(self) -> None:
        with patch("backend.services.sharing.tunnel_service._cloudflared_already_running", return_value=True):
            handle = start_remote_access(
                RemoteProvider.cloudflare,
                port=8080,
                cloudflare_hostname="codeplane.example.com",
                cloudflare_token="tok",
            )
        assert handle.origin_is_reusable is True

    @patch("backend.services.sharing.tunnel_service._list_devtunnels", return_value=[])
    def test_devtunnel_generated_name_is_not_reusable(self, _mock) -> None:
        proc = _FakeProc(poll_result=None)
        with (
            patch("backend.services.sharing.tunnel_service.subprocess.run") as mock_run,
            patch("backend.services.sharing.tunnel_service.subprocess.Popen", return_value=_as_popen(proc)),
            patch("backend.services.sharing.tunnel_service._wait_for_startup"),
            patch("backend.services.sharing.tunnel_service._start_output_drain"),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            with patch(
                "backend.services.sharing.tunnel_service._lookup_devtunnel",
                return_value=(True, "usw2"),
            ):
                handle = start_remote_access(RemoteProvider.devtunnel, port=8080)

        assert handle.origin_is_reusable is False

    @patch(
        "backend.services.sharing.tunnel_service._list_devtunnels",
        return_value=[{"tunnelId": "cpl-existing.usw2"}],
    )
    def test_devtunnel_reused_existing_is_reusable(self, _mock) -> None:
        proc = _FakeProc(poll_result=None)
        with (
            patch("backend.services.sharing.tunnel_service.subprocess.run") as mock_run,
            patch("backend.services.sharing.tunnel_service.subprocess.Popen", return_value=_as_popen(proc)),
            patch("backend.services.sharing.tunnel_service._wait_for_startup"),
            patch("backend.services.sharing.tunnel_service._start_output_drain"),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            handle = start_remote_access(RemoteProvider.devtunnel, port=8080)

        assert handle.origin_is_reusable is True


# ---------------------------------------------------------------------------
# Cross-platform connector lifecycle regressions
# ---------------------------------------------------------------------------


class _StubbornProc:
    """A connector that ignores terminate and only dies on kill."""

    def __init__(self, *, kill_also_times_out: bool = False) -> None:
        self.pid = 4242
        self.stdout = None
        self.terminated = False
        self.killed = False
        self._kill_also_times_out = kill_also_times_out

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        if not self.killed or self._kill_also_times_out:
            raise real_subprocess.TimeoutExpired(cmd="connector", timeout=timeout or 0)
        return 0


class TestTerminateAndReap:
    """``subprocess.TimeoutExpired`` is a SubprocessError, not an OSError.

    The previous teardown caught only ``OSError``, so a connector that ignored
    terminate propagated the timeout out of ``TunnelHandle.close()`` and
    abandoned every remaining cleanup step.
    """

    def test_terminate_timeout_escalates_to_kill(self) -> None:
        proc = _StubbornProc()
        _terminate_and_reap(_as_popen(cast("_FakeProc", proc)), label="cloudflare", timeout=0.01)
        assert proc.terminated is True
        assert proc.killed is True

    def test_never_raises_even_when_kill_also_times_out(self) -> None:
        proc = _StubbornProc(kill_also_times_out=True)
        _terminate_and_reap(_as_popen(cast("_FakeProc", proc)), label="devtunnel", timeout=0.01)
        assert proc.killed is True

    def test_already_exited_process_is_not_signalled(self) -> None:
        proc = _FakeProc(poll_result=0)
        _terminate_and_reap(_as_popen(proc), timeout=0.01)
        assert proc.terminated is False
        assert proc.killed is False

    def test_close_reaps_watchdog_proc_when_handle_proc_hangs(self) -> None:
        """A hanging primary process must not strand the watchdog's process."""
        hanging = _StubbornProc()
        watchdog_proc = _StubbornProc()
        watchdog = TunnelWatchdog(
            tunnel_url="https://example.invalid",
            restart_command=["cloudflared"],
            proc=_as_popen(cast("_FakeProc", watchdog_proc)),
            label="cloudflare",
        )
        handle = TunnelHandle(
            provider=RemoteProvider.cloudflare,
            origin="https://example.invalid",
            proc=_as_popen(cast("_FakeProc", hanging)),
            watchdog=watchdog,
        )
        handle.close()
        assert hanging.killed is True
        assert watchdog_proc.killed is True


class TestSignalProcessTreeOnWindows:
    """POSIX signals the whole process group; Windows has to enumerate.

    ``devtunnel host`` runs its transport in a child process, so a bare
    ``Popen.terminate`` on the parent left the tunnel serving after CodePlane
    believed it had torn the connector down.
    """

    def test_descendants_are_terminated_on_windows(self) -> None:
        import psutil

        proc = _FakeProc(poll_result=None)
        proc.pid = 4242
        grandchild = MagicMock()
        tree = MagicMock()
        tree.children.return_value = [grandchild]

        with (
            patch.object(tunnel_service.sys, "platform", "win32"),
            patch.object(psutil, "Process", return_value=tree) as process_lookup,
        ):
            tunnel_service._signal_process_tree(_as_popen(proc))

        process_lookup.assert_called_once_with(4242)
        tree.children.assert_called_once_with(recursive=True)
        grandchild.terminate.assert_called_once()
        assert proc.terminated is True


class TestRunCaptureNeverRaises:
    """A hung or missing provider CLI must not escape as a raw traceback."""

    def test_timeout_returns_nonzero_result(self) -> None:
        with patch(
            "backend.services.sharing.tunnel_service.subprocess.run",
            side_effect=real_subprocess.TimeoutExpired(cmd="devtunnel", timeout=30),
        ):
            result = _run_capture(["devtunnel", "list", "--json"])
        assert result.returncode != 0
        assert "timed out" in result.stderr

    def test_missing_binary_returns_nonzero_result(self) -> None:
        with patch(
            "backend.services.sharing.tunnel_service.subprocess.run",
            side_effect=FileNotFoundError("devtunnel"),
        ):
            result = _run_capture(["devtunnel", "list", "--json"])
        assert result.returncode != 0
        assert "not found" in result.stderr

    def test_list_devtunnels_tolerates_timeout(self) -> None:
        with patch(
            "backend.services.sharing.tunnel_service.subprocess.run",
            side_effect=real_subprocess.TimeoutExpired(cmd="devtunnel", timeout=30),
        ):
            assert _list_devtunnels() == []


class TestDevtunnelLoginDetection:
    """The Dev Tunnels CLI reports "logged out" with several distinct phrasings."""

    def test_validate_reports_logged_out_before_create_fails(self) -> None:
        with (
            patch("backend.services.sharing.tunnel_service.shutil.which", return_value="/usr/bin/devtunnel"),
            patch("backend.services.sharing.tunnel_service.devtunnel_logged_in", return_value=False),
        ):
            error = validate_remote_provider(RemoteProvider.devtunnel)
        assert error is not None
        assert "devtunnel user login" in error

    def test_validate_passes_when_logged_in(self) -> None:
        with (
            patch("backend.services.sharing.tunnel_service.shutil.which", return_value="/usr/bin/devtunnel"),
            patch("backend.services.sharing.tunnel_service.devtunnel_logged_in", return_value=True),
        ):
            assert validate_remote_provider(RemoteProvider.devtunnel) is None

    def test_logged_in_is_false_for_login_required_output(self) -> None:
        with patch(
            "backend.services.sharing.tunnel_service._run_capture",
            return_value=real_subprocess.CompletedProcess([], returncode=3, stdout="Login required.", stderr=""),
        ):
            assert devtunnel_logged_in() is False

    def test_logged_in_is_false_for_expired_token_despite_exit_zero(self) -> None:
        """An expired login is the wording a returning user hits, and it is the
        one case where ``devtunnel user show`` still exits 0 — observed on
        Windows: exit 0 with "Login token expired." on stdout, while every
        other subcommand then fails with exit 3. Trusting the exit code alone
        let validation pass and the failure resurface later as a bare
        "Login token expired." with no instruction to fix it."""
        with patch(
            "backend.services.sharing.tunnel_service._run_capture",
            return_value=real_subprocess.CompletedProcess([], returncode=0, stdout="Login token expired.", stderr=""),
        ):
            assert devtunnel_logged_in() is False

    def test_logged_in_is_true_for_normal_output(self) -> None:
        with patch(
            "backend.services.sharing.tunnel_service._run_capture",
            return_value=real_subprocess.CompletedProcess(
                [], returncode=0, stdout="Logged in as dfinson using GitHub.", stderr=""
            ),
        ):
            assert devtunnel_logged_in() is True

    @pytest.mark.parametrize(
        "message",
        [
            "Login required.",
            "Unauthorized tunnel creation access: Anonymous does not have 'create' access scope",
            "You are not logged in",
            "Login token expired.",
        ],
    )
    def test_create_failure_appends_login_hint(self, message: str) -> None:
        """The anonymous-access-scope wording is what a logged-out user actually hits."""
        with (
            patch("backend.services.sharing.tunnel_service._list_devtunnels", return_value=[]),
            patch(
                "backend.services.sharing.tunnel_service._run_capture",
                return_value=real_subprocess.CompletedProcess([], returncode=3, stdout="", stderr=message),
            ),
            pytest.raises(TunnelStartError) as exc_info,
        ):
            _start_devtunnel(8080, tunnel_name="cpl-test")
        assert "devtunnel user login" in str(exc_info.value)


class TestStableDevtunnelIdentity:
    @staticmethod
    def _record(tmp_path: Path) -> None:
        (tmp_path / "devtunnel.json").write_text(
            json.dumps(
                {
                    "tunnelName": "cpl-remembered",
                    "port": 8080,
                    "origin": "https://old-8080.usw2.devtunnels.ms",
                }
            ),
            encoding="utf-8",
        )

    @pytest.mark.parametrize("explicit", [None, "cpl-explicit"])
    def test_remembered_identity_beats_decoy_but_not_explicit_name(self, tmp_path: Path, explicit: str | None) -> None:
        self._record(tmp_path)
        expected = explicit or "cpl-remembered"
        origin = "https://assigned-id-8080.usw2.devtunnels.ms"

        def capture(args: list[str]) -> real_subprocess.CompletedProcess[str]:
            assert args[1] != "create"
            body = {"ports": [{"portNumber": 8080, "portUri": origin + "/"}]}
            return real_subprocess.CompletedProcess(args, 0, stdout=json.dumps(body), stderr="")

        with (
            patch.object(
                tunnel_service,
                "_list_devtunnels",
                return_value=[
                    {"tunnelId": "cpl-decoy.usw2"},
                    {"tunnelId": "cpl-remembered.usw2"},
                    {"tunnelId": "cpl-explicit.usw2"},
                ],
            ),
            patch.object(tunnel_service, "_run_capture", side_effect=capture),
            patch.object(tunnel_service, "_spawn_connector", return_value=_as_popen(_FakeProc())) as spawn,
            patch.object(tunnel_service, "_wait_for_startup"),
            patch.object(tunnel_service, "_start_output_drain"),
            patch.object(tunnel_service.log, "warning") as warning,
        ):
            actual_origin, _, name, reusable = _start_devtunnel(8080, tunnel_name=explicit)
        assert (actual_origin, name, reusable) == (origin, expected, True)
        assert spawn.call_args.args[0][1:] == ["host", expected]
        assert json.loads((tmp_path / "devtunnel.json").read_text()) == {
            "tunnelName": expected,
            "port": 8080,
            "origin": origin,
        }
        warning.assert_called_once_with(
            "devtunnel_origin_changed", previous_origin="https://old-8080.usw2.devtunnels.ms", origin=origin
        )

    def test_generated_name_is_saved_before_hosting(self, tmp_path: Path) -> None:
        def spawn(args: list[str]) -> subprocess.Popen[str]:
            record = json.loads((tmp_path / "devtunnel.json").read_text())
            assert record["tunnelName"] == args[2]
            return _as_popen(_FakeProc())

        with (
            patch.object(tunnel_service, "_find_existing_codeplane_tunnel", return_value=None),
            patch.object(tunnel_service, "_lookup_devtunnel", return_value=(True, "usw2")),
            patch.object(tunnel_service, "_run_capture", return_value=real_subprocess.CompletedProcess([], 0, "", "")),
            patch.object(tunnel_service, "_spawn_connector", side_effect=spawn),
            patch.object(tunnel_service, "_wait_for_startup"),
            patch.object(tunnel_service, "_start_output_drain"),
        ):
            origin, _, name, reusable = _start_devtunnel(8080)
        assert name.startswith("cpl-")
        assert origin == f"https://{name}-8080.usw2.devtunnels.ms"
        assert reusable is False
        saved = tunnel_service._read_stable_devtunnel()
        assert saved is not None
        assert saved.name == name

    @pytest.mark.parametrize(
        "contents",
        [
            "{",
            "[]",
            '{"tunnelName": "-invalid", "port":8080, "origin":"https://x.devtunnels.ms"}',
            '{"tunnelName": "cpl-test", "port":true, "origin":"https://x.devtunnels.ms"}',
            '{"tunnelName": "cpl-test", "port":0, "origin":"https://x.devtunnels.ms"}',
            '{"tunnelName": "cpl-test", "port":8080, "origin":"https://unrelated.example"}',
        ],
    )
    def test_invalid_record_does_not_select_another_tunnel(self, tmp_path: Path, contents: str) -> None:
        (tmp_path / "devtunnel.json").write_text(contents, encoding="utf-8")
        with (
            patch.object(tunnel_service, "_run_capture") as capture,
            patch.object(tunnel_service, "_spawn_connector") as spawn,
            pytest.raises(TunnelStartError, match="Dev Tunnel identity"),
        ):
            _start_devtunnel(8080)
        capture.assert_not_called()
        spawn.assert_not_called()

    def test_save_failure_does_not_leave_a_connector_running(self) -> None:
        with (
            patch.object(tunnel_service, "_lookup_devtunnel", return_value=(True, "usw2")),
            patch.object(tunnel_service, "_run_capture", return_value=real_subprocess.CompletedProcess([], 0, "", "")),
            patch.object(tunnel_service, "write_json_atomic", side_effect=PermissionError("read only")),
            patch.object(tunnel_service, "_spawn_connector") as spawn,
            pytest.raises(TunnelStartError, match="Cannot save"),
        ):
            _start_devtunnel(8080, tunnel_name="cpl-test")
        spawn.assert_not_called()

    @pytest.mark.parametrize("nested", [False, True])
    def test_external_tunnel_uses_assigned_origin_without_writing_state(self, tmp_path: Path, nested: bool) -> None:
        payload = {"ports": [{"portNumber": 8080, "portUri": "https://assigned-8080.use.devtunnels.ms"}]}
        body = {"tunnel": payload} if nested else payload
        with (
            patch.object(tunnel_service, "_lookup_devtunnel", return_value=(True, "use")),
            patch.object(
                tunnel_service,
                "_run_capture",
                return_value=real_subprocess.CompletedProcess([], 0, json.dumps(body), ""),
            ),
            patch.object(tunnel_service, "_spawn_connector") as spawn,
        ):
            origin = _external_tunnel_origin(
                RemoteProvider.devtunnel, port=8080, tunnel_name="cpl-test", cloudflare_hostname=None
            )
        assert origin == "https://assigned-8080.use.devtunnels.ms"
        assert not (tmp_path / "devtunnel.json").exists()
        spawn.assert_not_called()

    @pytest.mark.parametrize(
        "payload",
        [[], {"tunnel": None}, {"ports": [None]}, {"ports": [{"portNumber": 8080, "portUri": "http://bad.example"}]}],
    )
    def test_unavailable_assigned_uri_has_an_explicit_alias_fallback(self, payload: object) -> None:
        with (
            patch.object(
                tunnel_service,
                "_run_capture",
                return_value=real_subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
            ),
            patch.object(tunnel_service.log, "warning") as warning,
        ):
            assert (
                tunnel_service._devtunnel_origin("cpl-test", 8080, "use") == "https://cpl-test-8080.use.devtunnels.ms"
            )
        warning.assert_called_once_with("devtunnel_port_uri_unavailable", tunnel="cpl-test", port=8080)

    def test_installer_location_is_used_when_path_lookup_fails(self, tmp_path: Path) -> None:
        executable = tmp_path / ("devtunnel.exe" if sys.platform == "win32" else "devtunnel")
        executable.write_text("test executable", encoding="utf-8")
        executable.chmod(0o755)
        with (
            patch.object(tunnel_service.shutil, "which", return_value=None),
            patch.object(tunnel_service, "_DEVTUNNEL_FALLBACK_PATHS", (executable,)),
            patch.object(tunnel_service.subprocess, "run") as run,
        ):
            _run_capture(["devtunnel", "user", "show"])
        assert run.call_args.args[0] == [str(executable), "user", "show"]


class TestDevtunnelPortRegistration:
    """Reuse hinges on what the service reports, not on error wording.

    The real CLI rejects a re-registration with "Tunnel service error:
    Conflict with existing entity", which matches neither "already" nor
    "exists" — the strings this once keyed on. Every start against an existing
    tunnel therefore aborted, which is exactly how the Windows devtunnel run
    failed.
    """

    _CONFLICT = (
        "Tunnel service error: Conflict with existing entity. "
        "Tunnel port number conflicts with an existing port in the tunnel."
    )
    _PORT_LIST = '{"ports":[{"portNumber":8080,"protocol":"http","clientConnections":0}]}'

    @staticmethod
    def _capture(*, create_error: str, port_list: str) -> object:
        def _fake_capture(args: list[str]) -> real_subprocess.CompletedProcess[str]:
            if args[1:3] == ["port", "create"]:
                return real_subprocess.CompletedProcess(args, returncode=1, stdout="", stderr=create_error)
            if args[1:3] == ["port", "list"]:
                return real_subprocess.CompletedProcess(args, returncode=0, stdout=port_list, stderr="")
            return real_subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

        return _fake_capture

    def test_port_create_failure_is_fatal(self) -> None:
        """A tunnel that cannot forward the port must not be reported as started."""
        with (
            patch("backend.services.sharing.tunnel_service._lookup_devtunnel", return_value=(True, "usw2")),
            patch(
                "backend.services.sharing.tunnel_service._run_capture",
                side_effect=self._capture(create_error="quota exceeded", port_list="Found 0 tunnel ports.\n"),
            ),
            pytest.raises(TunnelStartError) as exc_info,
        ):
            _start_devtunnel(8080, tunnel_name="cpl-test")
        assert "quota exceeded" in str(exc_info.value)

    @pytest.mark.parametrize("create_error", [_CONFLICT, "Port 8080 already exists on tunnel"])
    def test_already_registered_port_is_tolerated(self, create_error: str) -> None:
        proc = _FakeProc(poll_result=None)

        with (
            patch("backend.services.sharing.tunnel_service._lookup_devtunnel", return_value=(True, "usw2")),
            patch(
                "backend.services.sharing.tunnel_service._run_capture",
                side_effect=self._capture(create_error=create_error, port_list=self._PORT_LIST),
            ),
            patch("backend.services.sharing.tunnel_service.subprocess.Popen", return_value=_as_popen(proc)),
            patch("backend.services.sharing.tunnel_service._wait_for_startup"),
            patch("backend.services.sharing.tunnel_service._start_output_drain"),
        ):
            origin, _, name, _ = _start_devtunnel(8080, tunnel_name="cpl-test")
        assert origin == "https://cpl-test-8080.usw2.devtunnels.ms"
        assert name == "cpl-test"

    def test_a_different_registered_port_does_not_count(self) -> None:
        """Only the requested port proves the tunnel can forward this server."""
        other_port = '{"ports":[{"portNumber":9000,"protocol":"http","clientConnections":0}]}'
        with (
            patch("backend.services.sharing.tunnel_service._lookup_devtunnel", return_value=(True, "usw2")),
            patch(
                "backend.services.sharing.tunnel_service._run_capture",
                side_effect=self._capture(create_error=self._CONFLICT, port_list=other_port),
            ),
            pytest.raises(TunnelStartError),
        ):
            _start_devtunnel(8080, tunnel_name="cpl-test")

    def test_unparsable_port_list_is_not_treated_as_registered(self) -> None:
        """A CLI that stops emitting JSON must fail loudly, not silently proceed."""
        with (
            patch("backend.services.sharing.tunnel_service._lookup_devtunnel", return_value=(True, "usw2")),
            patch(
                "backend.services.sharing.tunnel_service._run_capture",
                side_effect=self._capture(create_error=self._CONFLICT, port_list="Found 1 tunnel port.\n8080 http 0\n"),
            ),
            pytest.raises(TunnelStartError),
        ):
            _start_devtunnel(8080, tunnel_name="cpl-test")

    def test_port_list_is_requested_as_json(self) -> None:
        """Guard the structured contract: table parsing is localizable and brittle."""
        seen: list[list[str]] = []

        def _capture(args: list[str]) -> real_subprocess.CompletedProcess[str]:
            seen.append(args)
            if args[1:3] == ["port", "create"]:
                return real_subprocess.CompletedProcess(args, returncode=1, stdout="", stderr=self._CONFLICT)
            if args[1:3] == ["port", "list"]:
                return real_subprocess.CompletedProcess(args, returncode=0, stdout=self._PORT_LIST, stderr="")
            return real_subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

        proc = _FakeProc(poll_result=None)
        with (
            patch("backend.services.sharing.tunnel_service._lookup_devtunnel", return_value=(True, "usw2")),
            patch("backend.services.sharing.tunnel_service._run_capture", side_effect=_capture),
            patch("backend.services.sharing.tunnel_service.subprocess.Popen", return_value=_as_popen(proc)),
            patch("backend.services.sharing.tunnel_service._wait_for_startup"),
            patch("backend.services.sharing.tunnel_service._start_output_drain"),
        ):
            _start_devtunnel(8080, tunnel_name="cpl-test")

        port_list_calls = [args for args in seen if args[1:3] == ["port", "list"]]
        assert port_list_calls, "the port registration check never ran"
        assert all("--json" in args for args in port_list_calls)


class TestCloudflaredDetectionIsPortable:
    """Reuse detection previously shelled out to ``pgrep``, which Windows lacks.

    That made every Windows run believe no connector was present and start a
    duplicate one for the same tunnel. It also matched on the process name
    alone, so an unrelated connector counted as ours.
    """

    #: base64 of ``{"a": "acct", "t": "tunnel-uuid", "s": "secret"}``.
    TOKEN = base64.b64encode(json.dumps({"a": "acct", "t": "tunnel-uuid", "s": "secret"}).encode()).decode()

    @classmethod
    def _proc(
        cls,
        pid: int,
        name: str,
        cmdline: list[str] | None = None,
        environ: dict[str, str] | None = None,
    ) -> object:
        stub = MagicMock()
        stub.info = {
            "pid": pid,
            "name": name,
            "cmdline": cmdline if cmdline is not None else ["cloudflared", "tunnel", "run", "--token", cls.TOKEN],
        }
        # Real connectors (``cloudflared tunnel run``) carry the token via the
        # TUNNEL_TOKEN env var rather than argv; default to no env match so
        # tests exercising cmdline-only matching stay unaffected.
        stub.environ.return_value = environ if environ is not None else {}
        return stub

    def test_detects_windows_executable_name(self) -> None:
        import psutil

        with (
            patch.object(psutil, "process_iter", return_value=[self._proc(999, "cloudflared.exe")]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is True

    def test_detects_posix_executable_name(self) -> None:
        import psutil

        with (
            patch.object(psutil, "process_iter", return_value=[self._proc(999, "cloudflared")]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is True

    def test_matches_a_connector_identified_only_by_tunnel_id(self) -> None:
        """A config-file connector names the tunnel UUID rather than the token."""
        import psutil

        proc = self._proc(999, "cloudflared", ["cloudflared", "tunnel", "run", "tunnel-uuid"])
        with (
            patch.object(psutil, "process_iter", return_value=[proc]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is True

    def test_ignores_a_connector_for_a_different_tunnel(self) -> None:
        """Reusing a stranger's connector prints a public URL that routes nowhere.

        Windows documents ``cloudflared service install`` as the persistent
        setup, so an unrelated service on the host is the likely case — and it
        only became reachable at all once detection stopped using ``pgrep``.
        """
        import psutil

        other = base64.b64encode(json.dumps({"a": "b", "t": "someone-else", "s": "x"}).encode()).decode()
        proc = self._proc(999, "cloudflared.exe", ["cloudflared", "tunnel", "run", "--token", other])
        with (
            patch.object(psutil, "process_iter", return_value=[proc]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is False

    def test_ignores_a_connector_with_an_unreadable_command_line(self) -> None:
        import psutil

        proc = self._proc(999, "cloudflared.exe", [])
        with (
            patch.object(psutil, "process_iter", return_value=[proc]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is False

    def test_ignores_unrelated_processes(self) -> None:
        import psutil

        with (
            patch.object(psutil, "process_iter", return_value=[self._proc(999, "chrome.exe")]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is False

    def test_ignores_our_own_child_connector(self) -> None:
        import os

        import psutil

        child = MagicMock()
        child.pid = 555
        parent = MagicMock()
        parent.children.return_value = [child]
        with (
            patch.object(psutil, "process_iter", return_value=[self._proc(555, "cloudflared")]),
            patch.object(psutil, "Process", return_value=parent),
            patch.object(os, "getpid", return_value=111),
        ):
            assert _cloudflared_already_running(self.TOKEN) is False

    def test_detects_a_token_run_connector_via_its_env_var(self) -> None:
        """``_start_cloudflare`` spawns ``cloudflared tunnel run`` with the token
        passed via the ``TUNNEL_TOKEN`` env var, not argv. Before this fix,
        cmdline-only matching never recognized such a connector as ours, so
        every ``cpl up --remote --provider cloudflare`` spawned a duplicate
        connector alongside it. Cloudflare's edge then load-balanced across
        both, causing intermittent 502s against the orphaned one until its
        heartbeat lapsed.
        """
        import psutil

        proc = self._proc(
            999,
            "cloudflared.exe",
            cmdline=["cloudflared", "tunnel", "--no-autoupdate", "run"],
            environ={"TUNNEL_TOKEN": self.TOKEN},
        )
        with (
            patch.object(psutil, "process_iter", return_value=[proc]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is True

    def test_ignores_a_token_run_connector_for_a_different_tunnel_via_env(self) -> None:
        import psutil

        other = base64.b64encode(json.dumps({"a": "b", "t": "someone-else", "s": "x"}).encode()).decode()
        proc = self._proc(
            999,
            "cloudflared.exe",
            cmdline=["cloudflared", "tunnel", "--no-autoupdate", "run"],
            environ={"TUNNEL_TOKEN": other},
        )
        with (
            patch.object(psutil, "process_iter", return_value=[proc]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is False

    def test_detects_same_tunnel_after_token_rotation(self) -> None:
        import psutil

        rotated = base64.b64encode(json.dumps({"a": "acct", "t": "tunnel-uuid", "s": "rotated"}).encode()).decode()
        proc = self._proc(
            999,
            "cloudflared.exe",
            cmdline=["cloudflared", "tunnel", "run"],
            environ={"TUNNEL_TOKEN": rotated},
        )
        with (
            patch.object(psutil, "process_iter", return_value=[proc]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is True

    def test_ignores_a_connector_whose_environ_is_unreadable(self) -> None:
        import psutil

        proc = self._proc(999, "cloudflared.exe", cmdline=["cloudflared", "tunnel", "--no-autoupdate", "run"])
        proc.environ.side_effect = psutil.AccessDenied(999)
        with (
            patch.object(psutil, "process_iter", return_value=[proc]),
            patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)),
        ):
            assert _cloudflared_already_running(self.TOKEN) is False


class TestJobAssignmentIsReported:
    """A failed Job Object assignment must be observable, not silently swallowed.

    ``AssignProcessToJobObject`` fails with ``ERROR_ACCESS_DENIED`` on Windows
    hosts that place the connector in an ambient job at spawn. Discarding the
    result left the caller believing the connector could not outlive an abrupt
    kill when in fact it could, with nothing in the log to say so.
    """

    def _fake_kernel32(self, *, assign_ok: bool) -> MagicMock:
        kernel32 = MagicMock()
        kernel32.OpenProcess.return_value = 4321
        kernel32.AssignProcessToJobObject.return_value = 1 if assign_ok else 0
        return kernel32

    def _assign(self, *, assign_ok: bool) -> bool:
        proc = cast("subprocess.Popen[str]", SimpleNamespace(pid=999))
        kernel32 = self._fake_kernel32(assign_ok=assign_ok)
        fake_ctypes = MagicMock()
        fake_ctypes.WinDLL.return_value = kernel32
        fake_ctypes.get_last_error.return_value = 5
        with (
            patch.object(tunnel_service.sys, "platform", "win32"),
            patch.object(tunnel_service, "_windows_kill_on_close_job", return_value=77),
            patch.dict(sys.modules, {"ctypes": fake_ctypes, "ctypes.wintypes": MagicMock()}),
        ):
            return tunnel_service._assign_to_kill_on_close_job(proc)

    def test_failed_assignment_reports_no_coverage(self) -> None:
        tunnel_service._job_assign_warned = False
        assert self._assign(assign_ok=False) is False

    def test_successful_assignment_reports_coverage(self) -> None:
        tunnel_service._job_assign_warned = False
        assert self._assign(assign_ok=True) is True
