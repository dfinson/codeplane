"""Tests for the shared restart-protocol primitives (paths, timeouts, phase
logging, atomic JSON I/O, process identity, and the restart lock).
"""

from __future__ import annotations

import io
import json
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

import psutil
import pytest

from backend.services.dev_restart.restart_protocol import (
    RestartLockHeldError,
    RestartPhase,
    RestartProtocolError,
    RestartTimeouts,
    acquire_restart_lock,
    get_dev_restart_dir,
    get_replacement_log_path,
    get_request_paths,
    get_restart_lock_path,
    get_restart_log_path,
    is_identity_alive,
    log_phase,
    read_json_file,
    release_restart_lock,
    rotate_restart_log_if_needed,
    write_json_atomic,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_codeplane_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CODEPLANE_HOME at a throwaway directory for every test in this file."""
    monkeypatch.setenv("CODEPLANE_HOME", str(tmp_path))
    import backend.config as config_module

    monkeypatch.setattr(config_module, "_codeplane_dir", None)
    return tmp_path


class TestPaths:
    def test_dev_restart_dir_is_created_under_codeplane_home(self, tmp_path: Path) -> None:
        path = get_dev_restart_dir()
        assert path == tmp_path / "dev-restart"
        assert path.is_dir()

    def test_restart_log_path(self, tmp_path: Path) -> None:
        assert get_restart_log_path() == tmp_path / "dev-restart" / "restart.log"

    def test_replacement_log_path(self, tmp_path: Path) -> None:
        assert get_replacement_log_path("req-123") == tmp_path / "dev-restart" / "req-123.server.log"

    def test_restart_lock_path(self, tmp_path: Path) -> None:
        assert get_restart_lock_path() == tmp_path / "dev-restart" / "restart.lock"

    def test_request_paths(self, tmp_path: Path) -> None:
        paths = get_request_paths("req-123")
        base = tmp_path / "dev-restart"
        assert paths.pending == base / "req-123.pending.json"
        assert paths.claimed == base / "req-123.claimed.json"
        assert paths.started == base / "req-123.started.json"
        assert paths.ready == base / "req-123.ready.json"


class TestRotateRestartLog:
    def test_missing_log_is_a_noop(self, tmp_path: Path) -> None:
        log_path = tmp_path / "dev-restart" / "restart.log"
        rotate_restart_log_if_needed(log_path)
        assert not log_path.exists()

    def test_small_log_is_not_rotated(self, tmp_path: Path) -> None:
        log_path = get_restart_log_path()
        log_path.write_text("small\n", encoding="utf-8")
        rotate_restart_log_if_needed(log_path, max_bytes=1024)
        assert log_path.read_text(encoding="utf-8") == "small\n"
        assert not log_path.with_name("restart.log.1").exists()

    def test_log_at_or_over_max_bytes_rotates_to_single_backup(self, tmp_path: Path) -> None:
        log_path = get_restart_log_path()
        log_path.write_bytes(b"x" * 2048)
        rotate_restart_log_if_needed(log_path, max_bytes=1024)
        assert not log_path.exists()
        backup_path = log_path.with_name("restart.log.1")
        assert backup_path.read_bytes() == b"x" * 2048

    def test_existing_backup_is_replaced_not_accumulated(self, tmp_path: Path) -> None:
        log_path = get_restart_log_path()
        backup_path = log_path.with_name("restart.log.1")
        backup_path.write_bytes(b"old-backup")
        log_path.write_bytes(b"y" * 2048)

        rotate_restart_log_if_needed(log_path, max_bytes=1024)

        assert backup_path.read_bytes() == b"y" * 2048
        assert not log_path.exists()
        # Exactly one backup ever exists -- no .2, .3, etc.
        siblings = list(log_path.parent.glob("restart.log*"))
        assert siblings == [backup_path]

    def test_rotation_raises_restart_protocol_error_when_replace_fails(self, tmp_path: Path) -> None:
        log_path = get_restart_log_path()
        log_path.write_bytes(b"z" * 2048)

        with (
            patch("pathlib.Path.replace", side_effect=PermissionError("file is locked")),
            pytest.raises(RestartProtocolError, match="could not rotate"),
        ):
            rotate_restart_log_if_needed(log_path, max_bytes=1024)

    def test_rotation_succeeds_while_replacement_uses_different_log_file(self, tmp_path: Path) -> None:
        log_path = get_restart_log_path()
        replacement_log_path = get_replacement_log_path("req-123")
        log_path.write_bytes(b"q" * 2048)
        replacement_log_path.write_text("replacement still running\n", encoding="utf-8")

        with replacement_log_path.open("a", encoding="utf-8") as replacement_log_handle:
            replacement_log_handle.write("more output\n")
            replacement_log_handle.flush()
            rotate_restart_log_if_needed(log_path, max_bytes=1024)

        assert not log_path.exists()
        assert log_path.with_name("restart.log.1").read_bytes() == b"q" * 2048
        assert replacement_log_path.read_text(encoding="utf-8") == "replacement still running\nmore output\n"


class TestRestartTimeouts:
    def test_defaults_match_spec(self) -> None:
        timeouts = RestartTimeouts()
        assert timeouts.adoption_seconds == 5.0
        assert timeouts.response_grace_seconds == 2.0
        assert timeouts.pause_wait_seconds == 10.0
        assert timeouts.stop_seconds == 15.0
        assert timeouts.readiness_seconds == 60.0
        assert timeouts.remote_probe_seconds == 30.0

    def test_roundtrip(self) -> None:
        timeouts = RestartTimeouts(adoption_seconds=1, stop_seconds=99)
        restored = RestartTimeouts.from_dict(timeouts.to_dict())
        assert restored == timeouts

    def test_camel_case_keys(self) -> None:
        data = RestartTimeouts().to_dict()
        assert set(data) == {
            "adoptionSeconds",
            "responseGraceSeconds",
            "pauseWaitSeconds",
            "stopSeconds",
            "readinessSeconds",
            "remoteProbeSeconds",
        }

    def test_malformed_raises(self) -> None:
        with pytest.raises(RestartProtocolError):
            RestartTimeouts.from_dict({"adoptionSeconds": "not-a-number"})

    def test_not_an_object_raises(self) -> None:
        with pytest.raises(RestartProtocolError):
            RestartTimeouts.from_dict("nope")


class TestLogPhase:
    def test_writes_one_flushed_json_line(self) -> None:
        stream = io.StringIO()
        log_phase(RestartPhase.spawned, "req-1", stream=stream, helperPid=1234)
        line = stream.getvalue().rstrip("\n")
        assert stream.getvalue().endswith("\n")
        record = json.loads(line)
        assert record["requestId"] == "req-1"
        assert record["phase"] == "spawned"
        assert record["helperPid"] == 1234
        assert "timestamp" in record

    def test_default_stream_resolved_at_call_time(self) -> None:
        """sys.stdout must be looked up when log_phase runs, not when the
        module was imported -- required because the helper process
        redirects stdout after detachment."""
        fake_stdout = io.StringIO()
        with patch("sys.stdout", fake_stdout):
            log_phase(RestartPhase.succeeded, "req-2")
        assert "succeeded" in fake_stdout.getvalue()

    def test_all_eight_phases_exist(self) -> None:
        assert {p.value for p in RestartPhase} == {
            "spawned",
            "pausing",
            "stopping",
            "starting",
            "checking_health",
            "checking_remote",
            "succeeded",
            "failed",
        }


class TestAtomicJsonIO:
    def test_write_then_read_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "data.json"
        write_json_atomic(path, {"a": 1, "b": "two"})
        assert read_json_file(path) == {"a": 1, "b": "two"}

    def test_write_uses_temp_file_and_replace(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        seen_tmp_names: list[str] = []
        real_replace = os.replace

        def _spy_replace(src: object, dst: object) -> None:
            seen_tmp_names.append(str(src))
            real_replace(src, dst)

        with patch("os.replace", side_effect=_spy_replace) as mock_replace:
            write_json_atomic(path, {"x": 1})

        mock_replace.assert_called_once()
        assert seen_tmp_names[0] != str(path)
        assert path.exists()
        # No leftover temp files after a successful write.
        leftovers = [p for p in tmp_path.iterdir() if p.name != "data.json"]
        assert leftovers == []

    def test_failed_replace_removes_only_its_own_temp_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        path.write_text('{"existing": true}', encoding="utf-8")

        with patch("os.replace", side_effect=OSError("disk full")), pytest.raises(OSError, match="disk full"):
            write_json_atomic(path, {"new": True})

        # The pre-existing complete file must be untouched.
        assert json.loads(path.read_text(encoding="utf-8")) == {"existing": True}
        # No stray temp file left behind.
        leftovers = [p for p in tmp_path.iterdir() if p.name != "data.json"]
        assert leftovers == []

    def test_read_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RestartProtocolError):
            read_json_file(tmp_path / "missing.json")

    def test_read_malformed_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(RestartProtocolError):
            read_json_file(path)

    def test_read_non_object_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(RestartProtocolError):
            read_json_file(path)


class TestIsIdentityAlive:
    def test_matching_pid_and_create_time_is_alive(self) -> None:
        with patch("psutil.Process") as mock_process_cls:
            mock_process_cls.return_value.create_time.return_value = 1000.0
            assert is_identity_alive(123, 1000.0) is True

    def test_create_time_mismatch_is_not_alive(self) -> None:
        """PID reuse: same PID, different (later) creation time."""
        with patch("psutil.Process") as mock_process_cls:
            mock_process_cls.return_value.create_time.return_value = 2000.0
            assert is_identity_alive(123, 1000.0) is False

    def test_dead_pid_is_not_alive(self) -> None:
        with patch("psutil.Process", side_effect=psutil.NoSuchProcess(123)):
            assert is_identity_alive(123, 1000.0) is False

    def test_access_denied_is_not_alive(self) -> None:
        with patch("psutil.Process", side_effect=psutil.AccessDenied()):
            assert is_identity_alive(123, 1000.0) is False

    def test_small_float_jitter_is_tolerated(self) -> None:
        with patch("psutil.Process") as mock_process_cls:
            mock_process_cls.return_value.create_time.return_value = 1000.0000001
            assert is_identity_alive(123, 1000.0) is True


class TestRestartLock:
    def test_acquire_then_release(self, tmp_path: Path) -> None:
        lock = acquire_restart_lock("req-1", helper_pid=111, helper_process_time=500.0)
        assert lock.path == tmp_path / "dev-restart" / "restart.lock"
        assert lock.path.exists()
        payload = json.loads(lock.path.read_text(encoding="utf-8"))
        assert payload["requestId"] == "req-1"
        assert payload["helperPid"] == 111

        release_restart_lock(lock)
        assert not lock.path.exists()

    def test_refuses_when_live_helper_holds_lock(self) -> None:
        with patch("backend.services.dev_restart.restart_protocol.is_identity_alive", return_value=True):
            first = acquire_restart_lock("req-1", helper_pid=111, helper_process_time=500.0)
            with pytest.raises(RestartLockHeldError):
                acquire_restart_lock("req-2", helper_pid=222, helper_process_time=600.0)
        # cleanup
        release_restart_lock(first)

    def test_reclaims_stale_lock_from_dead_helper(self) -> None:
        with patch("backend.services.dev_restart.restart_protocol.is_identity_alive", return_value=False):
            first = acquire_restart_lock("req-1", helper_pid=111, helper_process_time=500.0)
            second = acquire_restart_lock("req-2", helper_pid=222, helper_process_time=600.0)
        assert second.helper_pid == 222
        payload = json.loads(second.path.read_text(encoding="utf-8"))
        assert payload["helperPid"] == 222
        release_restart_lock(second)
        assert first.path == second.path  # same lock file, reclaimed

    def test_release_never_deletes_a_lock_belonging_to_someone_else(self, tmp_path: Path) -> None:
        lock = acquire_restart_lock("req-1", helper_pid=111, helper_process_time=500.0)
        # Simulate another helper having since reclaimed the (now-different) lock file.
        other_payload = {"requestId": "req-2", "helperPid": 222, "helperProcessTime": 600.0, "createdAt": "x"}
        lock.path.write_text(json.dumps(other_payload), encoding="utf-8")

        release_restart_lock(lock)

        assert lock.path.exists()
        assert json.loads(lock.path.read_text(encoding="utf-8"))["helperPid"] == 222
class TestAccessOnlyHandoffFraming:
    @staticmethod
    def api(name):
        from backend.services.dev_restart import restart_protocol as protocol

        function = getattr(protocol, name, None)
        assert callable(function), f"Introduce {name} for the bounded authenticated handoff protocol"
        return function

    @pytest.mark.parametrize("case", ["partial", "oversized", "invalid_json", "eof", "deadline"])
    def test_invalid_or_incomplete_channel_fails_closed(self, case):
        import os
        import time

        read = self.api("read_handoff_frame")
        read_fd, write_fd = os.pipe()
        try:
            data = {
                "partial": b"\x00\x00", "oversized": (65537).to_bytes(4, "big"),
                "invalid_json": b"\x00\x00\x00\x01{", "eof": b"", "deadline": b"",
            }[case]
            if data:
                os.write(write_fd, data)
            if case != "deadline":
                os.close(write_fd)
                write_fd = -1
            with pytest.raises(RestartProtocolError, match="handoff_"):
                read(read_fd, time.monotonic() + 0.05)
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)

    def test_credential_and_commit_are_distinct_bounded_frames(self):
        import os
        import time

        encode = self.api("encode_handoff_frame")
        read = self.api("read_handoff_frame")
        validate = self.api("validate_handoff_frame")
        commit = self.api("validate_commit_frame")
        frame = {
            "version": 1, "kind": "credential", "requestId": "request-1",
            "serverPid": 123, "serverProcessTime": 12.5,
            "origin": "https://access.example.test", "jwt": "a.b.c",
        }
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, encode(frame))
            parsed = read(read_fd, time.monotonic() + 1)
            assert validate(
                parsed, request_id="request-1", server_pid=123,
                server_process_time=12.5, origin="https://access.example.test",
            ) == "a.b.c"
            with pytest.raises(RestartProtocolError, match="identity"):
                validate(parsed, request_id="other", server_pid=123,
                         server_process_time=12.5, origin="https://access.example.test")
            with pytest.raises(RestartProtocolError, match="commit"):
                commit(parsed, "request-1")
            commit({"version": 1, "kind": "commit", "requestId": "request-1"}, "request-1")
            with pytest.raises(RestartProtocolError, match="oversized"):
                encode({"jwt": "x" * 65536})
        finally:
            os.close(read_fd)
            os.close(write_fd)
