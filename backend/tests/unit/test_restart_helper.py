"""Tests for the detached restart helper: native spawn, adoption wait, the
helper-side claim/lock/started-marker sequence (Story 1.3), and job pause,
old-process stop, replacement start, and readiness verification (Story 1.4).
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import psutil
import pytest

from backend.services.dev_restart import restart_helper as rh
from backend.services.dev_restart.launch_profile import (
    LaunchProfile,
    SecretSource,
    build_active_launch_profile,
)
from backend.services.dev_restart.restart_protocol import (
    RestartPhase,
    RestartTimeouts,
    get_request_paths,
    get_restart_lock_path,
    read_json_file,
    write_json_atomic,
)
from backend.services.dev_restart.restart_remote import RemoteProbeError

if TYPE_CHECKING:
    from collections.abc import Iterator

_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _isolated_codeplane_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CODEPLANE_HOME at a throwaway directory for every test in this file."""
    monkeypatch.setenv("CODEPLANE_HOME", str(tmp_path))
    import backend.config as config_module

    monkeypatch.setattr(config_module, "_codeplane_dir", None)
    return tmp_path


def _local_profile(**overrides: object) -> LaunchProfile:
    defaults: dict[str, object] = dict(
        executable=sys.executable,
        working_directory=str(Path.cwd()),
        host="127.0.0.1",
        port=8080,
        dev=False,
        remote=False,
        provider="local",
        tunnel_ownership=None,
        tunnel_name=None,
        password_source=SecretSource.not_required(),
        tunnel_credential_source=SecretSource.not_required(),
        started_pid=4242,
        started_process_time=1_700_000_000.5,
    )
    defaults.update(overrides)
    return build_active_launch_profile(**defaults)  # type: ignore[arg-type]


def _remote_profile(**overrides: object) -> LaunchProfile:
    defaults: dict[str, object] = dict(
        executable=sys.executable,
        working_directory=str(Path.cwd()),
        host="0.0.0.0",  # noqa: S104
        port=8080,
        dev=False,
        remote=True,
        provider="devtunnel",
        tunnel_ownership="managed",
        tunnel_name="cpl-ab12cd34",
        password_source=SecretSource.resolvable("environment", "CPL_PASSWORD"),
        tunnel_credential_source=SecretSource.resolvable("provider-login", "devtunnel"),
        started_pid=4242,
        started_process_time=1_700_000_000.5,
    )
    defaults.update(overrides)
    return build_active_launch_profile(**defaults)  # type: ignore[arg-type]


def _fake_monotonic(increment: float = 0.01) -> Iterator[float]:
    """A deterministic, strictly-increasing fake clock for timeout-loop tests.

    Using the real wall clock with mocked ``time.sleep`` makes iteration
    counts wall-clock-dependent (a no-op sleep lets the loop spin as fast as
    the CPU allows). Advancing a fixed amount per call instead makes the
    number of loop iterations before a deadline expires deterministic.
    """
    state = 0.0
    while True:
        state += increment
        yield state


class _FakeClock:
    def __init__(self, increment: float = 0.01) -> None:
        self._gen = _fake_monotonic(increment)

    def __call__(self) -> float:
        return next(self._gen)


# ---------------------------------------------------------------------------
# Story 1.3 — native detached spawn
# ---------------------------------------------------------------------------


class TestSpawnDetachedHelper:
    def test_posix_uses_new_session_and_inherited_log_handle(self, tmp_path: Path) -> None:
        log_handle = MagicMock()
        fake_proc = MagicMock(pid=4321)
        with (
            patch.object(rh.sys, "platform", "linux"),
            patch.object(rh.subprocess, "Popen", return_value=fake_proc) as mock_popen,
        ):
            pid = rh.spawn_detached_helper(
                Path("/usr/bin/python3"), Path("/repo/tools/dev_restart.py"), tmp_path / "req.pending.json", log_handle
            )

        assert pid == 4321
        argv, kwargs = mock_popen.call_args
        expected_python = str(Path("/usr/bin/python3"))
        expected_script = str(Path("/repo/tools/dev_restart.py"))
        assert argv[0] == [expected_python, expected_script, "--helper", str(tmp_path / "req.pending.json")]
        assert kwargs["start_new_session"] is True
        assert "creationflags" not in kwargs
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] is log_handle
        assert kwargs["stderr"] is log_handle

    def test_windows_uses_detached_process_flags(self, tmp_path: Path) -> None:
        log_handle = MagicMock()
        fake_proc = MagicMock(pid=555)
        with (
            patch.object(rh.sys, "platform", "win32"),
            patch.object(rh.subprocess, "Popen", return_value=fake_proc) as mock_popen,
        ):
            pid = rh.spawn_detached_helper(
                Path("C:/python.exe"), Path("C:/repo/tools/dev_restart.py"), tmp_path / "req.pending.json", log_handle
            )

        assert pid == 555
        _, kwargs = mock_popen.call_args
        expected_flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        assert kwargs["creationflags"] == expected_flags
        assert "start_new_session" not in kwargs


class TestSpawnDetachedHelperNativeSurvival:
    """Story 1.7 AC6: native process-level proof that a process spawned via
    ``spawn_detached_helper`` survives its *immediate spawning process*
    exiting -- the same OS-level detachment property (POSIX new
    session/process group, Windows ``DETACHED_PROCESS``) that lets the real
    helper survive the initiator (``tools/dev_restart.py``) and the listener
    process group being torn down (AD-1). The test never asserts on process
    *names* (Dev Notes: "Tests must target exact process identity, not
    executable names") -- it records the grandchild's PID and creation time
    up front and re-checks that exact identity, mirroring
    ``is_identity_alive``.
    """

    def test_grandchild_survives_immediate_parent_exit(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "grandchild.pid"
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            "import os, sys, time\n"
            "pid_file = sys.argv[2]\n"
            "tmp_name = pid_file + '.tmp'\n"
            "with open(tmp_name, 'w', encoding='utf-8') as f:\n"
            "    f.write(f'{os.getpid()}:{__import__(\"psutil\").Process().create_time()}')\n"
            "os.replace(tmp_name, pid_file)\n"
            "time.sleep(10)\n",
            encoding="utf-8",
        )
        fake_parent_script = tmp_path / "fake_parent.py"
        fake_parent_script.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(_REPO_ROOT)!r})\n"
            "from pathlib import Path\n"
            "from backend.services.dev_restart.restart_helper import spawn_detached_helper\n"
            "python_exe, grandchild_script, pid_file, log_path = sys.argv[1:5]\n"
            "with open(log_path, 'a', encoding='utf-8') as log_handle:\n"
            "    spawn_detached_helper(Path(python_exe), Path(grandchild_script), Path(pid_file), log_handle)\n",
            encoding="utf-8",
        )
        log_path = tmp_path / "spawn.log"

        # Run the fake parent to completion -- it spawns the grandchild and
        # exits immediately without waiting, exactly like the real parent
        # process handing off to the detached helper.
        argv = [
            sys.executable,
            str(fake_parent_script),
            sys.executable,
            str(grandchild_script),
            str(pid_file),
            str(log_path),
        ]
        result = subprocess.run(  # noqa: S603 - fixed argv, test-owned scripts
            argv,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, f"fake parent failed: {result.stdout}\n{result.stderr}"

        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pid_file.exists(), "grandchild never reported its PID -- spawn did not survive"

        pid_str, _, create_time_str = pid_file.read_text(encoding="utf-8").partition(":")
        grandchild_pid = int(pid_str)
        grandchild_create_time = float(create_time_str)

        try:
            # The fake parent process has already exited (subprocess.run
            # returned above); the grandchild being alive right now proves
            # it outlived its immediate spawning process, not merely that it
            # was launched.
            assert psutil.pid_exists(grandchild_pid)
            live_create_time = psutil.Process(grandchild_pid).create_time()
            assert live_create_time == pytest.approx(grandchild_create_time, abs=0.5)
        finally:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                psutil.Process(grandchild_pid).kill()


class TestAwaitAdoption:
    def test_returns_true_when_started_marker_matches(self) -> None:
        paths = get_request_paths("req-adopt-1")
        write_json_atomic(
            paths.started,
            {"requestId": "req-adopt-1", "helperPid": 111, "helperProcessTime": 1.0, "startedAt": "x"},
        )
        assert rh.await_adoption(paths, "req-adopt-1", timeout_seconds=1.0) is True

    def test_returns_true_when_claimed_marker_matches(self) -> None:
        paths = get_request_paths("req-adopt-claimed")
        write_json_atomic(
            paths.claimed,
            {"requestId": "req-adopt-claimed", "helperPid": 111, "helperProcessTime": 1.0, "claimedAt": "x"},
        )
        assert rh.await_adoption(paths, "req-adopt-claimed", timeout_seconds=1.0) is True

    def test_ignores_mismatched_request_id_and_times_out(self) -> None:
        paths = get_request_paths("req-adopt-2")
        write_json_atomic(
            paths.started,
            {"requestId": "someone-else", "helperPid": 111, "helperProcessTime": 1.0, "startedAt": "x"},
        )
        assert rh.await_adoption(paths, "req-adopt-2", timeout_seconds=0.3) is False

    def test_returns_false_when_marker_never_appears(self) -> None:
        paths = get_request_paths("req-adopt-3")
        assert rh.await_adoption(paths, "req-adopt-3", timeout_seconds=0.3) is False

    def test_final_check_catches_marker_published_at_deadline(self) -> None:
        paths = get_request_paths("req-adopt-4")

        with (
            patch.object(rh, "_request_marker_matches", side_effect=[False, False, True]),
            patch.object(rh.time, "monotonic", side_effect=[0.0, 0.0, 0.1]),
            patch.object(rh.time, "sleep"),
        ):
            assert rh.await_adoption(paths, "req-adopt-4", timeout_seconds=0.05) is True


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_drops_secret_like_keys() -> None:
    fields = {"password": "x", "authToken": "y", "job_id": "abc", "Cookie": "z", "detail": "ok"}
    assert rh._redact(fields) == {"job_id": "abc", "detail": "ok"}


# ---------------------------------------------------------------------------
# RestartRequest.from_dict
# ---------------------------------------------------------------------------


class TestRestartRequestFromDict:
    def test_parses_all_fields(self, tmp_path: Path) -> None:
        profile = _local_profile()
        data = {
            "requestId": "req-parse-1",
            "targetSourceRoot": str(tmp_path),
            "launchProfile": profile.to_dict(),
            "timeouts": RestartTimeouts().to_dict(),
            "nonce": "abc123",
        }
        request = rh.RestartRequest.from_dict(data)
        assert request.request_id == "req-parse-1"
        assert request.target_source_root == tmp_path
        assert request.nonce == "abc123"
        assert request.timeouts.adoption_seconds == 5.0

    def test_generates_nonce_when_missing(self, tmp_path: Path) -> None:
        profile = _local_profile()
        data = {
            "requestId": "req-parse-2",
            "targetSourceRoot": str(tmp_path),
            "launchProfile": profile.to_dict(),
            "timeouts": RestartTimeouts().to_dict(),
        }
        request = rh.RestartRequest.from_dict(data)
        assert len(request.nonce) == 32


# ---------------------------------------------------------------------------
# Story 1.3 — helper-side claim (pending -> claimed -> started)
# ---------------------------------------------------------------------------


class TestClaimRequest:
    def test_claims_pending_and_writes_helper_identity(self, tmp_path: Path) -> None:
        paths = get_request_paths("req-claim-1")
        profile = _local_profile()
        write_json_atomic(
            paths.pending,
            {
                "requestId": "req-claim-1",
                "targetSourceRoot": str(tmp_path),
                "launchProfile": profile.to_dict(),
                "timeouts": RestartTimeouts().to_dict(),
                "createdAt": "2026-01-01T00:00:00Z",
            },
        )

        request = rh._claim_request(paths.pending, paths, "req-claim-1", helper_pid=999, helper_process_time=123.4)

        assert request.request_id == "req-claim-1"
        assert not paths.pending.exists()
        claimed = read_json_file(paths.claimed)
        assert claimed["helperPid"] == 999
        assert claimed["helperProcessTime"] == 123.4
        assert "claimedAt" in claimed

    def test_request_id_mismatch_raises_helper_abort(self, tmp_path: Path) -> None:
        paths = get_request_paths("req-claim-2")
        write_json_atomic(paths.pending, {"requestId": "wrong-id"})

        with pytest.raises(rh.HelperAbort) as excinfo:
            rh._claim_request(paths.pending, paths, "req-claim-2", 1, 1.0)

        assert excinfo.value.phase == RestartPhase.failed
        assert excinfo.value.reason == "request_id_mismatch"

    def test_unreadable_request_raises_helper_abort(self, tmp_path: Path) -> None:
        paths = get_request_paths("req-claim-3")
        missing_path = tmp_path / "does-not-exist.pending.json"

        with pytest.raises(rh.HelperAbort) as excinfo:
            rh._claim_request(missing_path, paths, "req-claim-3", 1, 1.0)

        assert excinfo.value.reason == "unreadable_request"


def test_write_started_marker_contents() -> None:
    paths = get_request_paths("req-started-1")
    rh._write_started_marker(paths, "req-started-1", helper_pid=42, helper_process_time=99.9)

    data = read_json_file(paths.started)
    assert data["requestId"] == "req-started-1"
    assert data["helperPid"] == 42
    assert data["helperProcessTime"] == 99.9
    assert "startedAt" in data


# ---------------------------------------------------------------------------
# Story 1.4 — complete running-job listing (must abort on any failure)
# ---------------------------------------------------------------------------


class TestListRunningJobs:
    def test_single_page(self) -> None:
        profile = _local_profile()
        with patch.object(
            rh,
            "_http_request",
            return_value=(200, {"items": [{"id": "j1"}, {"id": "j2"}], "hasMore": False, "cursor": None}),
        ) as mock_req:
            jobs = rh._list_running_jobs(profile)

        assert [j["id"] for j in jobs] == ["j1", "j2"]
        mock_req.assert_called_once()
        assert "state=running" in mock_req.call_args[0][1]

    def test_paginates_until_has_more_false(self) -> None:
        profile = _local_profile()
        responses = [
            (200, {"items": [{"id": "j1"}], "hasMore": True, "cursor": "c2"}),
            (200, {"items": [{"id": "j2"}], "hasMore": False, "cursor": None}),
        ]
        with patch.object(rh, "_http_request", side_effect=responses):
            jobs = rh._list_running_jobs(profile)

        assert [j["id"] for j in jobs] == ["j1", "j2"]

    def test_failure_aborts_before_returning_any_jobs(self) -> None:
        profile = _local_profile()
        with (
            patch.object(rh, "_http_request", return_value=(500, None)),
            pytest.raises(rh.HelperAbort) as excinfo,
        ):
            rh._list_running_jobs(profile)

        assert excinfo.value.reason == "job_list_failed"


class TestPauseJobs:
    def test_all_succeed_records_no_failures(self) -> None:
        profile = _local_profile()
        jobs = [{"id": "j1"}, {"id": "j2"}]
        with patch.object(rh, "_http_request", return_value=(204, None)):
            failed = rh._pause_jobs(profile, jobs, "req-pause-1")

        assert failed == []

    def test_individual_failures_recorded_without_aborting(self) -> None:
        profile = _local_profile()
        jobs = [{"id": "j1"}, {"id": "j2"}]
        with patch.object(rh, "_http_request", side_effect=[(204, None), (409, None)]):
            failed = rh._pause_jobs(profile, jobs, "req-pause-2")

        assert failed == ["j2"]

    def test_malformed_job_record_logged_and_skipped_not_aborted(self) -> None:
        """A job record missing (or with a non-string) ``id`` must not raise and
        abort the batch (AD-7): pausing must continue for every remaining job."""
        profile = _local_profile()
        jobs: list[dict[str, Any]] = [{"no_id_field": "oops"}, {"id": "j2"}, {"id": 12345}]
        with patch.object(rh, "_http_request", return_value=(204, None)) as mock_http:
            failed = rh._pause_jobs(profile, jobs, "req-pause-3")

        assert failed == []
        # Only the one well-formed job record ("j2") was actually paused --
        # the missing-id and non-string-id records were skipped, not raised.
        mock_http.assert_called_once()
        assert mock_http.call_args[0][1].endswith("/api/jobs/j2/pause")


# ---------------------------------------------------------------------------
# Story 1.4 — exact old-process stop with port-release proof
# ---------------------------------------------------------------------------


class TestStopOldProcess:
    def test_terminates_and_returns_once_listener_released(self) -> None:
        profile = _local_profile(started_pid=1234, started_process_time=100.0)
        fake_proc = MagicMock()
        fake_proc.create_time.return_value = 100.0

        with (
            patch.object(rh.psutil, "Process", return_value=fake_proc),
            patch.object(rh, "profile_owns_listener", return_value=False),
            patch.object(rh.time, "monotonic", side_effect=_FakeClock()),
            patch.object(rh.time, "sleep"),
        ):
            rh._stop_old_process(profile, timeout_seconds=1.0, request_id="req-stop-1")

        fake_proc.terminate.assert_called_once()
        fake_proc.kill.assert_not_called()

    def test_escalates_to_kill_after_first_deadline_expires(self) -> None:
        profile = _local_profile(started_pid=1234, started_process_time=100.0)
        fake_proc = MagicMock()
        fake_proc.create_time.return_value = 100.0
        # Bound through the first deadline, released once the escalation loop checks.
        listener_results = iter([True, False])

        with (
            patch.object(rh.psutil, "Process", return_value=fake_proc),
            patch.object(rh, "profile_owns_listener", side_effect=lambda _p: next(listener_results)),
            patch.object(rh.time, "monotonic", side_effect=_FakeClock(increment=0.02)),
            patch.object(rh.time, "sleep"),
        ):
            rh._stop_old_process(profile, timeout_seconds=0.01, request_id="req-stop-2")

        fake_proc.terminate.assert_called_once()
        fake_proc.kill.assert_called_once()

    def test_raises_helper_abort_if_never_released(self) -> None:
        profile = _local_profile(started_pid=1234, started_process_time=100.0)
        fake_proc = MagicMock()
        fake_proc.create_time.return_value = 100.0

        with (
            patch.object(rh.psutil, "Process", return_value=fake_proc),
            patch.object(rh, "profile_owns_listener", return_value=True),
            patch.object(rh.time, "monotonic", side_effect=_FakeClock(increment=0.05)),
            patch.object(rh.time, "sleep"),
            pytest.raises(rh.HelperAbort) as excinfo,
        ):
            rh._stop_old_process(profile, timeout_seconds=0.01, request_id="req-stop-3")

        assert excinfo.value.phase == RestartPhase.stopping
        fake_proc.terminate.assert_called_once()
        fake_proc.kill.assert_called_once()

    def test_skips_terminate_kill_when_pid_was_reused(self) -> None:
        """A live PID whose creation time no longer matches must never be signaled."""
        profile = _local_profile(started_pid=1234, started_process_time=100.0)
        fake_proc = MagicMock()
        fake_proc.create_time.return_value = 999.0  # different process now holds this PID

        with (
            patch.object(rh.psutil, "Process", return_value=fake_proc),
            patch.object(rh, "profile_owns_listener", return_value=False),
            patch.object(rh.time, "monotonic", side_effect=_FakeClock()),
            patch.object(rh.time, "sleep"),
        ):
            rh._stop_old_process(profile, timeout_seconds=1.0, request_id="req-stop-4")

        fake_proc.terminate.assert_not_called()
        fake_proc.kill.assert_not_called()

    def test_uses_canonical_tight_tolerance_not_a_loose_one(self) -> None:
        """Regression: a naive inline ``< 1.0`` tolerance would wrongly treat a PID
        reused within a second as the original process. The canonical
        ``is_identity_alive`` check (0.01s tolerance) must reject this and never
        signal the reused process."""
        profile = _local_profile(started_pid=1234, started_process_time=100.0)
        fake_proc = MagicMock()
        # 0.5s later: within a naive "< 1.0s" window, but well outside the
        # canonical 0.01s identity tolerance -- must be treated as reused.
        fake_proc.create_time.return_value = 100.5

        with (
            patch.object(rh.psutil, "Process", return_value=fake_proc),
            patch.object(rh, "profile_owns_listener", return_value=False),
            patch.object(rh.time, "monotonic", side_effect=_FakeClock()),
            patch.object(rh.time, "sleep"),
        ):
            rh._stop_old_process(profile, timeout_seconds=1.0, request_id="req-stop-5")

        fake_proc.terminate.assert_not_called()
        fake_proc.kill.assert_not_called()


# ---------------------------------------------------------------------------
# Story 1.4 — exactly-one replacement launch, nonce propagation, no /resume
# ---------------------------------------------------------------------------


class TestRuntimeContract:
    def test_replacement_replays_access_only_without_secret_arguments(self, tmp_path: Path) -> None:
        data = _remote_profile(provider="cloudflare").to_dict()
        data["passwordSource"] = {"kind": "not_required"}
        data["authMode"] = "access_only"
        profile = LaunchProfile.from_dict(data)
        with (
            patch.object(rh, "get_replacement_log_path", return_value=tmp_path / "replacement.log"),
            patch.object(rh.subprocess, "Popen", return_value=MagicMock()) as spawn,
        ):
            rh._start_replacement(profile, tmp_path, nonce="n", request_id="access-mode")
        args = spawn.call_args.args[0]
        assert "--access-only" in args
        assert "--password" not in args
        assert "--no-password" not in args


class TestRemoteHelperAuthentication:
    def test_remote_password_login_stays_in_memory_and_uses_loopback(self, monkeypatch):
        monkeypatch.setenv("CPL_PASSWORD", "test-password")
        response = MagicMock(status=200, headers={"set-cookie": "cpl_session=test-session; HttpOnly; Path=/"})
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        try:
            with patch.object(rh.urllib.request, "build_opener", return_value=opener):
                rh._authorize_helper(_remote_profile())
            request = opener.open.call_args.args[0]
            assert request.full_url == "http://127.0.0.1:8080/api/auth/login"
            assert rh._helper_session == "test-session"
            with pytest.raises(rh.HelperAbort, match="destination_rejected"):
                rh._http_request("GET", "http://127.0.0.1:3000/api/jobs")
        finally:
            rh._helper_session = None
            rh._helper_auth_origin = None

    def test_access_only_helper_refuses_missing_handoff_before_stopping(self):
        data = _remote_profile(provider="cloudflare").to_dict()
        data["authMode"] = "access_only"
        data["passwordSource"] = {"kind": "not_required"}
        with pytest.raises(rh.HelperAbort, match="authenticated_handoff"):
            rh._authorize_helper(LaunchProfile.from_dict(data))


class TestStartReplacement:
    def test_builds_exact_local_argv_and_env_with_nonce(self, tmp_path: Path) -> None:
        """Exact argv reproducibility (verified against ``python -m backend.main up --help``,
        which exposes the identical Click options this builds: --host, --port,
        --dev, --remote, --provider, --tunnel-name)."""
        profile = _local_profile(executable=sys.executable, host="127.0.0.1", port=9001, dev=True, remote=False)
        fake_proc = MagicMock()
        replacement_log_path = tmp_path / "req-start-1.server.log"

        with (
            patch.object(rh, "get_replacement_log_path", return_value=replacement_log_path),
            patch.object(rh.subprocess, "Popen", return_value=fake_proc) as mock_popen,
        ):
            result = rh._start_replacement(profile, tmp_path, nonce="nonce-abc", request_id="req-start-1")

        assert result is fake_proc
        mock_popen.assert_called_once()
        argv, kwargs = mock_popen.call_args
        args = argv[0]
        assert args == [
            sys.executable,
            "-m",
            "backend.main",
            "up",
            "--skip-preflight",
            "--host",
            "127.0.0.1",
            "--port",
            "9001",
            "--dev",
        ]
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["env"]["CODEPLANE_RESTART_NONCE"] == "nonce-abc"
        assert kwargs["env"]["CODEPLANE_RESTART_REQUEST_ID"] == "req-start-1"
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] is kwargs["stderr"]
        assert kwargs["stdout"] is not sys.stdout
        assert kwargs["stderr"] is not sys.stderr
        assert Path(kwargs["stdout"].name) == replacement_log_path

    def test_builds_exact_remote_argv_with_explicit_provider_and_tunnel_name(self, tmp_path: Path) -> None:
        """--provider is always explicit, never left to the CLI default (AC5: reproduce the
        recorded provider exactly, including non-default providers like cloudflare)."""
        profile = _remote_profile(host="0.0.0.0", port=9002, provider="cloudflare", tunnel_name="cpl-abc123")  # noqa: S104

        with (
            patch.object(rh, "get_replacement_log_path", return_value=tmp_path / "req-start-2.server.log"),
            patch.object(rh.subprocess, "Popen", return_value=MagicMock()) as mock_popen,
        ):
            rh._start_replacement(profile, tmp_path, nonce="n", request_id="req-start-2")

        args = mock_popen.call_args[0][0]
        assert args == [
            sys.executable,
            "-m",
            "backend.main",
            "up",
            "--skip-preflight",
            "--host",
            "0.0.0.0",  # noqa: S104
            "--port",
            "9002",
            "--remote",
            "--provider",
            "cloudflare",
            "--tunnel-name",
            "cpl-abc123",
            "--tunnel-ownership",
            "managed",
        ]

    def test_replays_managed_tunnel_ownership_exactly(self, tmp_path: Path) -> None:
        """Restart must replay the recorded ownership exactly (AD-8) so a managed
        connector is started again rather than falling back to the legacy
        auto-detect path."""
        profile = _remote_profile(provider="devtunnel", tunnel_name="cpl-managed1", tunnel_ownership="managed")

        with (
            patch.object(
                rh, "get_replacement_log_path", return_value=tmp_path / "req-start-ownership-managed.server.log"
            ),
            patch.object(rh.subprocess, "Popen", return_value=MagicMock()) as mock_popen,
        ):
            rh._start_replacement(profile, tmp_path, nonce="n", request_id="req-start-ownership-managed")

        args = mock_popen.call_args[0][0]
        assert args[-2:] == ["--tunnel-ownership", "managed"]

    def test_replays_external_tunnel_ownership_exactly(self, tmp_path: Path) -> None:
        """An externally-owned connector must be replayed as ``external`` so the
        replacement never scans for or spawns a connector process — it only
        resolves the exact recorded origin (AD-8, SPEC CAP-6)."""
        profile = _remote_profile(provider="cloudflare", tunnel_name=None, tunnel_ownership="external")

        with (
            patch.object(
                rh, "get_replacement_log_path", return_value=tmp_path / "req-start-ownership-external.server.log"
            ),
            patch.object(rh.subprocess, "Popen", return_value=MagicMock()) as mock_popen,
        ):
            rh._start_replacement(profile, tmp_path, nonce="n", request_id="req-start-ownership-external")

        args = mock_popen.call_args[0][0]
        assert args[-2:] == ["--tunnel-ownership", "external"]

    def test_never_invokes_resume_endpoint(self, tmp_path: Path) -> None:
        """Exactly one replacement is started; nothing in this module ever calls /resume."""
        profile = _local_profile()
        with (
            patch.object(rh, "get_replacement_log_path", return_value=tmp_path / "req-start-3.server.log"),
            patch.object(rh.subprocess, "Popen", return_value=MagicMock()) as mock_popen,
        ):
            rh._start_replacement(profile, tmp_path, nonce="n", request_id="req-start-3")

        mock_popen.assert_called_once()
        assert "/resume" not in " ".join(str(a) for a in mock_popen.call_args[0][0])
        assert "resume" not in rh.__dict__  # no resume-shaped helper exists in this module


# ---------------------------------------------------------------------------
# Story 1.4/1.5 — readiness-marker verification via the active launch profile
# ---------------------------------------------------------------------------


class TestWaitForReady:
    def test_returns_new_pid_when_marker_and_profile_agree(self) -> None:
        paths = get_request_paths("req-ready-1")
        write_json_atomic(paths.ready, {"requestId": "req-ready-1", "pid": 5555, "writtenAt": "x"})
        new_profile = _local_profile(started_pid=5555)
        fake_child = MagicMock()
        fake_child.poll.return_value = None

        with (
            patch.object(rh, "load_active_profile", return_value=new_profile),
            patch.object(rh, "profile_owns_listener", return_value=True),
        ):
            pid = rh._wait_for_ready(paths, "req-ready-1", fake_child, timeout_seconds=1.0)

        assert pid == 5555

    def test_ignores_ready_marker_for_different_request(self) -> None:
        paths = get_request_paths("req-ready-2")
        write_json_atomic(paths.ready, {"requestId": "someone-else", "pid": 5555, "writtenAt": "x"})
        fake_child = MagicMock()
        fake_child.poll.return_value = None

        with pytest.raises(rh.HelperAbort) as excinfo:
            rh._wait_for_ready(paths, "req-ready-2", fake_child, timeout_seconds=0.2)

        assert excinfo.value.reason == "readiness_timeout"

    def test_child_exit_before_readiness_raises_helper_abort(self) -> None:
        paths = get_request_paths("req-ready-3")
        fake_child = MagicMock()
        fake_child.poll.return_value = 1
        fake_child.returncode = 1

        with pytest.raises(rh.HelperAbort) as excinfo:
            rh._wait_for_ready(paths, "req-ready-3", fake_child, timeout_seconds=1.0)

        assert excinfo.value.reason == "child_exited"
        assert excinfo.value.fields["exit_code"] == 1

    def test_timeout_without_ready_marker_raises_helper_abort(self) -> None:
        paths = get_request_paths("req-ready-4")
        fake_child = MagicMock()
        fake_child.poll.return_value = None

        with pytest.raises(rh.HelperAbort) as excinfo:
            rh._wait_for_ready(paths, "req-ready-4", fake_child, timeout_seconds=0.1)

        assert excinfo.value.reason == "readiness_timeout"


# ---------------------------------------------------------------------------
# Cleanup and retention (AD-10)
# ---------------------------------------------------------------------------


def test_cleanup_success_removes_all_markers() -> None:
    paths = get_request_paths("req-cleanup-1")
    for path in (paths.pending, paths.claimed, paths.started, paths.ready):
        write_json_atomic(path, {"x": 1})

    rh._cleanup_success(paths)

    for path in (paths.pending, paths.claimed, paths.started, paths.ready):
        assert not path.exists()


# ---------------------------------------------------------------------------
# Top-level orchestration (run_helper / _run_claimed)
# ---------------------------------------------------------------------------


def _write_pending_request(
    request_id: str, target_source_root: Path, profile: LaunchProfile, **timeout_overrides: float
) -> Path:
    paths = get_request_paths(request_id)
    timeouts = RestartTimeouts(
        adoption_seconds=timeout_overrides.get("adoption_seconds", 0.1),
        response_grace_seconds=timeout_overrides.get("response_grace_seconds", 0.01),
        pause_wait_seconds=timeout_overrides.get("pause_wait_seconds", 0.01),
        stop_seconds=timeout_overrides.get("stop_seconds", 0.05),
        readiness_seconds=timeout_overrides.get("readiness_seconds", 0.2),
        remote_probe_seconds=timeout_overrides.get("remote_probe_seconds", 0.1),
    )
    write_json_atomic(
        paths.pending,
        {
            "requestId": request_id,
            "targetSourceRoot": str(target_source_root),
            "launchProfile": profile.to_dict(),
            "timeouts": timeouts.to_dict(),
            "nonce": "nonce-xyz",
            "createdAt": "2026-01-01T00:00:00Z",
        },
    )
    return paths.pending


class TestRunHelperIntegration:
    def test_full_success_path_cleans_up_and_releases_lock(self, tmp_path: Path) -> None:
        old_profile = _local_profile(started_pid=os.getpid(), started_process_time=psutil.Process().create_time())
        pending_path = _write_pending_request("req-run-1", tmp_path, old_profile)
        paths = get_request_paths("req-run-1")

        new_profile = _local_profile(started_pid=99999, started_process_time=42.0)
        fake_child = MagicMock()
        fake_child.poll.return_value = None

        def fake_start_replacement(profile: LaunchProfile, root: Path, nonce: str, req_id: str) -> MagicMock:
            write_json_atomic(paths.ready, {"requestId": req_id, "pid": 99999, "writtenAt": "now"})
            return fake_child

        with (
            patch.object(rh, "_list_running_jobs", return_value=[{"id": "j1"}]),
            patch.object(rh, "_pause_jobs", return_value=[]),
            patch.object(rh, "_stop_old_process"),
            patch.object(rh, "_start_replacement", side_effect=fake_start_replacement),
            patch.object(rh, "load_active_profile", return_value=new_profile),
            patch.object(rh, "profile_owns_listener", return_value=True),
        ):
            exit_code = rh.run_helper(pending_path)

        assert exit_code == 0
        assert not paths.pending.exists()
        assert not paths.claimed.exists()
        assert not paths.started.exists()
        assert not paths.ready.exists()
        assert not get_restart_lock_path().exists()

    def test_lock_held_by_live_helper_returns_1_without_claiming(self, tmp_path: Path) -> None:
        write_json_atomic(
            get_restart_lock_path(),
            {
                "requestId": "some-other-request",
                "helperPid": os.getpid(),
                "helperProcessTime": psutil.Process().create_time(),
                "createdAt": "2026-01-01T00:00:00Z",
            },
        )
        old_profile = _local_profile()
        pending_path = _write_pending_request("req-run-2", tmp_path, old_profile)

        exit_code = rh.run_helper(pending_path)

        assert exit_code == 1
        # Never claimed -- the pre-existing (unrelated) lock is left alone too.
        assert pending_path.exists()
        locked = read_json_file(get_restart_lock_path())
        assert locked["requestId"] == "some-other-request"

    def test_job_listing_failure_aborts_before_any_pause_or_stop(self, tmp_path: Path) -> None:
        old_profile = _local_profile()
        pending_path = _write_pending_request("req-run-3", tmp_path, old_profile)
        paths = get_request_paths("req-run-3")

        with (
            patch.object(
                rh,
                "_list_running_jobs",
                side_effect=rh.HelperAbort(RestartPhase.failed, "job_list_failed", status=500),
            ),
            patch.object(rh, "_pause_jobs") as mock_pause,
            patch.object(rh, "_stop_old_process") as mock_stop,
            patch.object(rh, "_start_replacement") as mock_start,
        ):
            exit_code = rh.run_helper(pending_path)

        assert exit_code == 1
        mock_pause.assert_not_called()
        mock_stop.assert_not_called()
        mock_start.assert_not_called()
        # Claim + started marker already happened (adoption precedes pausing);
        # failure artifacts are retained for diagnostics, not cleaned up.
        assert not paths.pending.exists()
        assert paths.claimed.exists()
        assert paths.started.exists()
        assert not get_restart_lock_path().exists()

    def test_individual_pause_failures_do_not_abort_the_restart(self, tmp_path: Path) -> None:
        old_profile = _local_profile(started_pid=os.getpid(), started_process_time=psutil.Process().create_time())
        pending_path = _write_pending_request("req-run-4", tmp_path, old_profile)
        paths = get_request_paths("req-run-4")

        new_profile = _local_profile(started_pid=77777, started_process_time=1.0)
        fake_child = MagicMock()
        fake_child.poll.return_value = None

        def fake_start_replacement(profile: LaunchProfile, root: Path, nonce: str, req_id: str) -> MagicMock:
            write_json_atomic(paths.ready, {"requestId": req_id, "pid": 77777, "writtenAt": "now"})
            return fake_child

        with (
            patch.object(rh, "_list_running_jobs", return_value=[{"id": "j1"}, {"id": "j2"}]),
            patch.object(rh, "_pause_jobs", return_value=["j2"]),
            patch.object(rh, "_stop_old_process"),
            patch.object(rh, "_start_replacement", side_effect=fake_start_replacement),
            patch.object(rh, "load_active_profile", return_value=new_profile),
            patch.object(rh, "profile_owns_listener", return_value=True),
        ):
            exit_code = rh.run_helper(pending_path)

        assert exit_code == 0
        assert not paths.pending.exists()


class TestCheckingRemotePhase:
    """Story 1.6: after local readiness, a remote profile must be probed at
    the resolved origin via restart_remote before the restart succeeds.
    Origin resolution and the network probe are never reimplemented here --
    only resolve_remote_probe_target/probe_remote_origin are exercised.
    """

    def _run_remote_restart(
        self, tmp_path: Path, request_id: str, old_profile: LaunchProfile, new_profile: LaunchProfile
    ) -> tuple[int, MagicMock]:
        pending_path = _write_pending_request(request_id, tmp_path, old_profile)
        paths = get_request_paths(request_id)
        fake_child = MagicMock()
        fake_child.poll.return_value = None

        def fake_start_replacement(profile: LaunchProfile, root: Path, nonce: str, req_id: str) -> MagicMock:
            write_json_atomic(paths.ready, {"requestId": req_id, "pid": new_profile.started_pid, "writtenAt": "now"})
            return fake_child

        probe_mock = MagicMock()
        with (
            patch.object(rh, "_authorize_helper"),
            patch.object(rh, "_list_running_jobs", return_value=[]),
            patch.object(rh, "_pause_jobs", return_value=[]),
            patch.object(rh, "_stop_old_process"),
            patch.object(rh, "_start_replacement", side_effect=fake_start_replacement),
            patch.object(rh, "load_active_profile", return_value=new_profile),
            patch.object(rh, "profile_owns_listener", return_value=True),
            patch.object(rh, "probe_remote_origin", probe_mock),
        ):
            exit_code = rh.run_helper(pending_path)
        return exit_code, probe_mock

    def test_reusable_origin_probes_the_original_recorded_origin(self, tmp_path: Path) -> None:
        old_profile = _remote_profile(
            started_pid=os.getpid(),
            started_process_time=psutil.Process().create_time(),
            tunnel_origin="https://cpl-fixed.usw2.devtunnels.ms",
            tunnel_origin_reusable=True,
        )
        # The replacement's own recorded origin is irrelevant for a reusable
        # tunnel -- resolve_remote_probe_target must ignore it entirely.
        new_profile = _remote_profile(
            started_pid=88881,
            started_process_time=7.0,
            tunnel_origin="https://should-be-ignored.example",
            tunnel_origin_reusable=True,
        )

        exit_code, probe_mock = self._run_remote_restart(tmp_path, "req-remote-1", old_profile, new_profile)

        assert exit_code == 0
        probe_mock.assert_called_once_with("https://cpl-fixed.usw2.devtunnels.ms", pytest.approx(0.1))
        paths = get_request_paths("req-remote-1")
        assert not paths.pending.exists()
        assert not paths.ready.exists()

    def test_non_reusable_origin_probes_the_replacement_origin_and_reports_change(self, tmp_path: Path) -> None:
        old_profile = _remote_profile(
            started_pid=os.getpid(),
            started_process_time=psutil.Process().create_time(),
            tunnel_origin="https://cpl-old.usw2.devtunnels.ms",
            tunnel_origin_reusable=False,
        )
        new_profile = _remote_profile(
            started_pid=88882,
            started_process_time=8.0,
            tunnel_origin="https://cpl-new.usw2.devtunnels.ms",
            tunnel_origin_reusable=False,
        )

        exit_code, probe_mock = self._run_remote_restart(tmp_path, "req-remote-2", old_profile, new_profile)

        assert exit_code == 0
        probe_mock.assert_called_once_with("https://cpl-new.usw2.devtunnels.ms", pytest.approx(0.1))

    def test_probe_timeout_aborts_restart_without_cleanup(self, tmp_path: Path) -> None:
        old_profile = _remote_profile(
            started_pid=os.getpid(),
            started_process_time=psutil.Process().create_time(),
            tunnel_origin="https://cpl-fixed.usw2.devtunnels.ms",
            tunnel_origin_reusable=True,
        )
        new_profile = _remote_profile(
            started_pid=88883,
            started_process_time=9.0,
            tunnel_origin="https://cpl-fixed.usw2.devtunnels.ms",
            tunnel_origin_reusable=True,
        )
        pending_path = _write_pending_request("req-remote-3", tmp_path, old_profile)
        paths = get_request_paths("req-remote-3")
        fake_child = MagicMock()
        fake_child.poll.return_value = None

        def fake_start_replacement(profile: LaunchProfile, root: Path, nonce: str, req_id: str) -> MagicMock:
            write_json_atomic(paths.ready, {"requestId": req_id, "pid": new_profile.started_pid, "writtenAt": "now"})
            return fake_child

        with (
            patch.object(rh, "_authorize_helper"),
            patch.object(rh, "_list_running_jobs", return_value=[]),
            patch.object(rh, "_pause_jobs", return_value=[]),
            patch.object(rh, "_stop_old_process"),
            patch.object(rh, "_start_replacement", side_effect=fake_start_replacement),
            patch.object(rh, "load_active_profile", return_value=new_profile),
            patch.object(rh, "profile_owns_listener", return_value=True),
            patch.object(
                rh,
                "probe_remote_origin",
                side_effect=RemoteProbeError("remote origin https://cpl-fixed.usw2.devtunnels.ms timed out"),
            ),
        ):
            exit_code = rh.run_helper(pending_path)

        assert exit_code == 1
        # Readiness had already succeeded (the ready marker was written), but
        # a failed remote probe must retain every artifact for diagnosis --
        # never clean up on a failure path (AD-10/AD-11).
        assert paths.claimed.exists()
        assert paths.started.exists()
        assert paths.ready.exists()
        assert not get_restart_lock_path().exists()
class TestAccessOnlyHandoffHelper:
    def test_redirect_never_receives_the_credential(self):
        assert hasattr(rh, "authenticate_access_handoff"), "Introduce authenticate_access_handoff"
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        seen = []

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append((self.path, bool(self.headers.get("Cf-Access-Jwt-Assertion"))))
                self.send_response(302)
                self.send_header("Location", "/leak")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            rh._helper_access_token = "a.b.c"
            rh._helper_access_profile = _remote_profile(port=server.server_port)
            rh._helper_auth_origin = f"http://127.0.0.1:{server.server_port}"
            with patch.object(rh, "profile_owns_listener", return_value=True):
                status, _ = rh._http_request("GET", rh._helper_auth_origin + "/api/jobs?state=running")
            assert status == 0
            assert seen == [("/api/jobs?state=running", True)]
        finally:
            rh._clear_helper_auth()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    @pytest.mark.parametrize("credential", ["valid", "expired", "audience", "signature", "redirect", "identity"])
    def test_authentication_precedes_adoption_and_mutation(self, tmp_path, monkeypatch, credential):
        authenticate = getattr(rh, "authenticate_access_handoff", None)
        assert callable(authenticate), "Introduce authenticate_access_handoff before Access-only adoption"
        import json
        import urllib.error
        from types import SimpleNamespace

        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa

        from backend.services.auth import cf_access
        from backend.services.dev_restart import restart_protocol as protocol

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cf_access.configure(team="example", aud="audience", eager=False)
        public_key = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        monkeypatch.setattr(cf_access, "_jwks_keys", [public_key])
        monkeypatch.setattr(cf_access, "_jwks_fetched_at", time.monotonic())
        now = int(time.time())
        payload = {"iss": "https://example.cloudflareaccess.com", "aud": "audience", "iat": now, "exp": now + 60}
        if credential == "expired":
            payload["exp"] = now - 1
        elif credential == "audience":
            payload["aud"] = "other"
        elif credential == "signature":
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(payload, key, algorithm="RS256")
        profile = _remote_profile(
            provider="cloudflare", password_source=SecretSource.not_required(),
            tunnel_origin="https://access.example.test", auth_mode="access_only",
        )
        request = rh.RestartRequest("handoff", tmp_path, profile,
                                    RestartTimeouts(response_grace_seconds=0, pause_wait_seconds=0), "nonce")
        paths = get_request_paths("handoff")
        frame = {
            "version": 1, "kind": "credential", "requestId": "handoff",
            "serverPid": profile.started_pid, "serverProcessTime": profile.started_process_time,
            "origin": profile.tunnel_origin, "jwt": token,
        }
        read_fd, write_fd = os.pipe()
        os.write(write_fd, protocol.encode_handoff_frame(frame))
        os.write(write_fd, protocol.encode_handoff_frame({"version": 1, "kind": "commit", "requestId": "handoff"}))
        os.close(write_fd)
        seen = []

        def verify(req, timeout):
            seen.append(req.full_url)
            assert req.full_url.startswith("http://127.0.0.1:8080/api/jobs")
            if len(seen) == 1:
                assert not paths.started.exists()
            if credential == "redirect":
                raise urllib.error.HTTPError(
                    req.full_url, 302, "redirect", {"Location": "https://elsewhere.test"}, None
                )
            valid = cf_access.verify_token(req.get_header("Cf-access-jwt-assertion"))
            response = MagicMock(status=200 if valid else 401)
            response.read.return_value = json.dumps({"items": [], "hasMore": False}).encode()
            response.__enter__.return_value = response
            return response

        try:
            with (
                patch.object(rh, "_claim_request", return_value=request),
                patch.object(rh, "profile_owns_listener", return_value=credential != "identity"),
                patch.object(rh.sys, "stdin", SimpleNamespace(fileno=lambda: read_fd, close=lambda: os.close(read_fd))),
                patch.object(rh.urllib.request, "build_opener", return_value=SimpleNamespace(open=verify)),
                patch.object(rh, "_stop_old_process") as stop,
                patch.object(rh, "_start_replacement", return_value=SimpleNamespace(pid=999)),
                patch.object(rh, "_wait_for_ready", return_value=999),
                patch.object(rh, "load_active_profile", return_value=profile),
                patch.object(rh, "probe_remote_origin"),
            ):
                result = rh._run_claimed(paths.pending, paths, "handoff", os.getpid(), 1.0)
            assert result == (0 if credential == "valid" else 1)
            assert stop.call_count == (1 if credential == "valid" else 0)
            if credential != "valid":
                assert not paths.started.exists()
            assert rh._helper_access_token is None
            assert token not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))
            if credential == "redirect":
                assert len(seen) == 1
        finally:
            cf_access.reset()
            with contextlib.suppress(OSError):
                os.close(read_fd)
