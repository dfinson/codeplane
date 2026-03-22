"""Tests for TerminalService — PTY session management."""

from __future__ import annotations

import json
import os
import signal
import struct
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    import termios  # POSIX only
except ImportError:
    termios = None  # type: ignore[assignment]

from backend.services.terminal_service import (
    PtySession,
    TerminalService,
    _detect_shell,
    _sanitize_for_replay,
)

# Mark that skips an entire test class on Windows (POSIX PTY not available)
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY not available on Windows")

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_mock_process(pid: int = 12345) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.kill = MagicMock()
    proc.wait = MagicMock(return_value=0)
    return proc


def _make_session(
    session_id: str = "abc123",
    master_fd: int = 10,
    pid: int = 12345,
    **kwargs,
) -> PtySession:
    proc = _make_mock_process(pid)
    return PtySession(
        id=session_id,
        master_fd=master_fd,
        process=proc,
        shell="/bin/bash",
        cwd="/tmp",
        **kwargs,
    )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


class TestSanitizeForReplay:
    def test_strips_osc_color_queries(self) -> None:
        buf = "hello\x1b]10;rgb:ff/ff/ff\x07world"
        assert _sanitize_for_replay(buf) == "helloworld"

    def test_strips_osc_color_with_st_terminator(self) -> None:
        buf = "hello\x1b]11;rgb:00/00/00\x1b\\world"
        assert _sanitize_for_replay(buf) == "helloworld"

    def test_strips_alt_screen_pairs(self) -> None:
        buf = "before\x1b[?1049hsome alt content\x1b[?1049lafter"
        assert _sanitize_for_replay(buf) == "beforeafter"

    def test_strips_bare_alt_screen_sequences(self) -> None:
        buf = "hello\x1b[?1047hworld"
        assert _sanitize_for_replay(buf) == "helloworld"

    def test_strips_clear_scrollback(self) -> None:
        buf = "hello\x1b[3Jworld"
        assert _sanitize_for_replay(buf) == "helloworld"

    def test_passes_through_normal_text(self) -> None:
        buf = "hello world\n"
        assert _sanitize_for_replay(buf) == buf

    def test_empty_string(self) -> None:
        assert _sanitize_for_replay("") == ""


@posix_only
class TestDetectShell:
    @patch.dict(os.environ, {"SHELL": "/bin/zsh"})
    @patch("os.path.isfile", return_value=True)
    def test_uses_shell_env_var(self, mock_isfile: MagicMock) -> None:
        assert _detect_shell() == "/bin/zsh"

    @patch.dict(os.environ, {"SHELL": ""})
    @patch("os.path.isfile")
    def test_falls_back_to_candidates(self, mock_isfile: MagicMock) -> None:
        # Empty SHELL, first candidate exists
        mock_isfile.side_effect = lambda p: p == "/bin/bash"
        assert _detect_shell() == "/bin/bash"

    @patch.dict(os.environ, {"SHELL": ""})
    @patch("os.path.isfile", return_value=False)
    def test_falls_back_to_bin_sh(self, mock_isfile: MagicMock) -> None:
        assert _detect_shell() == "/bin/sh"

    @patch.dict(os.environ, {}, clear=True)
    @patch("os.path.isfile", return_value=False)
    def test_no_shell_env_var(self, mock_isfile: MagicMock) -> None:
        assert _detect_shell() == "/bin/sh"


# ------------------------------------------------------------------
# PtySession dataclass
# ------------------------------------------------------------------


class TestPtySession:
    def test_append_scrollback_basic(self) -> None:
        s = _make_session()
        s.append_scrollback("hello")
        assert s.scrollback == "hello"

    def test_append_scrollback_accumulates(self) -> None:
        s = _make_session()
        s.append_scrollback("aaa")
        s.append_scrollback("bbb")
        assert s.scrollback == "aaabbb"

    def test_append_scrollback_trims_when_over_limit(self) -> None:
        s = _make_session(scrollback_limit=100)
        # Fill beyond 2x limit to trigger trimming
        s.append_scrollback("x" * 250)
        assert len(s.scrollback) <= 100

    def test_append_scrollback_trims_at_newline(self) -> None:
        s = _make_session(scrollback_limit=50)
        # Build up past 2x limit; include a newline near the trim point
        data = "a" * 40 + "\n" + "b" * 70
        s.append_scrollback(data)
        # Should have trimmed to ~50 chars from end, snapping at newline
        assert len(s.scrollback) <= 70
        assert s.scrollback.startswith("b")


# ------------------------------------------------------------------
# TerminalService
# ------------------------------------------------------------------


class TestTerminalServiceInit:
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_default_init(self, mock_shell: MagicMock) -> None:
        svc = TerminalService()
        assert svc._max_sessions == 5
        assert svc._default_shell == "/bin/bash"
        assert svc._scrollback_limit == 500 * 1024

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_custom_init(self, mock_shell: MagicMock) -> None:
        svc = TerminalService(max_sessions=10, default_shell="/bin/zsh", scrollback_size_kb=100)
        assert svc._max_sessions == 10
        assert svc._default_shell == "/bin/zsh"
        assert svc._scrollback_limit == 100 * 1024


@posix_only
class TestCreateSession:
    @patch("backend.services.terminal_service.asyncio.create_task")
    @patch("backend.services.terminal_service.fcntl.fcntl", return_value=0)
    @patch("backend.services.terminal_service.fcntl.ioctl")
    @patch("backend.services.terminal_service.os.close")
    @patch("backend.services.terminal_service.subprocess.Popen")
    @patch("backend.services.terminal_service.pty.openpty", return_value=(10, 11))
    @patch("backend.services.terminal_service.secrets.token_hex", return_value="deadbeef" * 4)
    @patch("backend.services.terminal_service.os.path.isdir", return_value=True)
    @patch("backend.services.terminal_service.os.path.isfile", return_value=True)
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_create_session_happy_path(
        self,
        mock_detect: MagicMock,
        mock_isfile: MagicMock,
        mock_isdir: MagicMock,
        mock_hex: MagicMock,
        mock_openpty: MagicMock,
        mock_popen: MagicMock,
        mock_close: MagicMock,
        mock_ioctl: MagicMock,
        mock_fcntl: MagicMock,
        mock_create_task: MagicMock,
    ) -> None:
        mock_proc = _make_mock_process()
        mock_popen.return_value = mock_proc
        mock_loop = MagicMock()

        svc = TerminalService()
        svc._loop = mock_loop

        session = svc.create_session(cwd="/tmp", shell="/bin/bash", job_id="job-1")

        assert session.id == "deadbeef" * 4
        assert session.shell == "/bin/bash"
        assert session.cwd == "/tmp"
        assert session.job_id == "job-1"
        assert session.master_fd == 10
        assert session.process is mock_proc
        assert session.id in svc.sessions

        # Slave FD should be closed after spawn
        mock_close.assert_any_call(11)

        # Terminal size should be set
        mock_ioctl.assert_called_once()

        # Reader should be added for master FD
        mock_loop.add_reader.assert_called_once()

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_create_session_max_sessions_exceeded(self, mock_detect: MagicMock) -> None:
        svc = TerminalService(max_sessions=1)
        # Add a fake session
        svc._sessions["existing"] = _make_session()

        with pytest.raises(RuntimeError, match="Maximum terminal sessions"):
            svc.create_session()

    @patch("backend.services.terminal_service.os.path.isfile", return_value=False)
    @patch("backend.services.terminal_service.shutil.which", return_value=None)
    @patch("backend.services.terminal_service._detect_shell", return_value="/nonexistent/shell")
    def test_create_session_shell_not_found(
        self, mock_detect: MagicMock, mock_which: MagicMock, mock_isfile: MagicMock
    ) -> None:
        svc = TerminalService()
        with pytest.raises(ValueError, match="Shell not found"):
            svc.create_session(shell="/nonexistent/shell")

    @patch("backend.services.terminal_service.os.path.isfile", return_value=False)
    @patch("backend.services.terminal_service.shutil.which", return_value="/usr/bin/bash")
    @patch("backend.services.terminal_service.asyncio.create_task")
    @patch("backend.services.terminal_service.fcntl.fcntl", return_value=0)
    @patch("backend.services.terminal_service.fcntl.ioctl")
    @patch("backend.services.terminal_service.os.close")
    @patch("backend.services.terminal_service.subprocess.Popen")
    @patch("backend.services.terminal_service.pty.openpty", return_value=(10, 11))
    @patch("backend.services.terminal_service.secrets.token_hex", return_value="aabb" * 8)
    @patch("backend.services.terminal_service.os.path.isdir", return_value=True)
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_create_session_resolves_shell_via_which(
        self,
        mock_detect: MagicMock,
        mock_isdir: MagicMock,
        mock_hex: MagicMock,
        mock_openpty: MagicMock,
        mock_popen: MagicMock,
        mock_close: MagicMock,
        mock_ioctl: MagicMock,
        mock_fcntl: MagicMock,
        mock_create_task: MagicMock,
        mock_which: MagicMock,
        mock_isfile: MagicMock,
    ) -> None:
        mock_popen.return_value = _make_mock_process()
        svc = TerminalService()
        svc._loop = MagicMock()

        session = svc.create_session(shell="bash")
        assert session.shell == "/usr/bin/bash"

    @patch("backend.services.terminal_service.os.path.isdir", return_value=False)
    @patch("backend.services.terminal_service.os.path.isfile", return_value=True)
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_create_session_cwd_not_found(
        self, mock_detect: MagicMock, mock_isfile: MagicMock, mock_isdir: MagicMock
    ) -> None:
        svc = TerminalService()
        with pytest.raises(ValueError, match="Working directory does not exist"):
            svc.create_session(cwd="/nonexistent")

    @patch("backend.services.terminal_service.os.close")
    @patch("backend.services.terminal_service.fcntl.ioctl")
    @patch("backend.services.terminal_service.subprocess.Popen", side_effect=OSError("spawn failed"))
    @patch("backend.services.terminal_service.pty.openpty", return_value=(10, 11))
    @patch("backend.services.terminal_service.os.path.isdir", return_value=True)
    @patch("backend.services.terminal_service.os.path.isfile", return_value=True)
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_create_session_popen_failure_closes_fds(
        self,
        mock_detect: MagicMock,
        mock_isfile: MagicMock,
        mock_isdir: MagicMock,
        mock_openpty: MagicMock,
        mock_popen: MagicMock,
        mock_ioctl: MagicMock,
        mock_close: MagicMock,
    ) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()

        with pytest.raises(OSError, match="spawn failed"):
            svc.create_session()

        # Both master and slave FDs should be closed on error
        mock_close.assert_any_call(10)
        mock_close.assert_any_call(11)


class TestGetSession:
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_get_existing_session(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = _make_session(session_id="s1")
        svc._sessions["s1"] = session
        assert svc.get_session("s1") is session

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_get_nonexistent_session(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        assert svc.get_session("nope") is None


class TestListSessions:
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_list_empty(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        assert svc.list_sessions() == []

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_list_sessions_returns_summary(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._sessions["s1"] = _make_session(session_id="s1", pid=100)
        svc._sessions["s2"] = _make_session(session_id="s2", pid=200, job_id="j1")

        result = svc.list_sessions()
        assert len(result) == 2
        ids = {r["id"] for r in result}
        assert ids == {"s1", "s2"}
        # Check structure of one entry
        entry = next(r for r in result if r["id"] == "s2")
        assert entry["pid"] == 200
        assert entry["jobId"] == "j1"
        assert entry["clients"] == 0


@posix_only
class TestKillSession:
    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_kill_existing_session(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()
        session = _make_session(session_id="s1")
        session._exit_task = MagicMock()
        session._exit_task.done.return_value = False
        svc._sessions["s1"] = session

        with (
            patch("backend.services.terminal_service.asyncio.to_thread", new_callable=AsyncMock),
            patch("backend.services.terminal_service.os.close"),
        ):
            result = await svc.kill_session("s1")

        assert result is True
        assert "s1" not in svc.sessions

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_kill_nonexistent_session(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        result = await svc.kill_session("nope")
        assert result is False


@posix_only
class TestKillSessionsForJob:
    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_kills_matching_sessions(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()
        s1 = _make_session(session_id="s1", job_id="job-1")
        s1._exit_task = MagicMock(done=MagicMock(return_value=True))
        s2 = _make_session(session_id="s2", job_id="job-1")
        s2._exit_task = MagicMock(done=MagicMock(return_value=True))
        s3 = _make_session(session_id="s3", job_id="job-2")
        svc._sessions = {"s1": s1, "s2": s2, "s3": s3}

        with (
            patch("backend.services.terminal_service.asyncio.to_thread", new_callable=AsyncMock),
            patch("backend.services.terminal_service.os.close"),
        ):
            count = await svc.kill_sessions_for_job("job-1")

        assert count == 2
        assert "s1" not in svc.sessions
        assert "s2" not in svc.sessions
        assert "s3" in svc.sessions

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_kill_no_matching_sessions(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        count = await svc.kill_sessions_for_job("no-match")
        assert count == 0


@posix_only
class TestWrite:
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.os.write")
    def test_write_to_session(self, mock_write: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._sessions["s1"] = _make_session(session_id="s1", master_fd=42)

        svc.write("s1", b"ls\n")
        mock_write.assert_called_once_with(42, b"ls\n")

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.os.write")
    def test_write_to_nonexistent_session(self, mock_write: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc.write("nope", b"data")
        mock_write.assert_not_called()

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.os.write", side_effect=OSError("write failed"))
    def test_write_handles_os_error(self, mock_write: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._sessions["s1"] = _make_session(session_id="s1")
        # Should not raise
        svc.write("s1", b"data")


@posix_only
class TestResize:
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.os.kill")
    @patch("backend.services.terminal_service.fcntl.ioctl")
    def test_resize_session(self, mock_ioctl: MagicMock, mock_kill: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = _make_session(session_id="s1", master_fd=42, pid=999)
        svc._sessions["s1"] = session

        svc.resize("s1", cols=80, rows=24)

        expected_winsize = struct.pack("HHHH", 24, 80, 0, 0)
        mock_ioctl.assert_called_once_with(42, termios.TIOCSWINSZ, expected_winsize)
        mock_kill.assert_called_once_with(999, signal.SIGWINCH)

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.fcntl.ioctl")
    def test_resize_nonexistent_session(self, mock_ioctl: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc.resize("nope", cols=80, rows=24)
        mock_ioctl.assert_not_called()

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.fcntl.ioctl", side_effect=OSError("resize failed"))
    def test_resize_handles_os_error(self, mock_ioctl: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._sessions["s1"] = _make_session(session_id="s1")
        # Should not raise
        svc.resize("s1", cols=80, rows=24)


class TestGetScrollback:
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_get_scrollback_returns_sanitized(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = _make_session(session_id="s1")
        session.scrollback = "hello\x1b[3Jworld"
        svc._sessions["s1"] = session

        result = svc.get_scrollback("s1")
        assert result == "helloworld"

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_get_scrollback_nonexistent_session(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        assert svc.get_scrollback("nope") == ""


@posix_only
class TestShutdown:
    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_shutdown_kills_all_sessions(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()
        for sid in ("s1", "s2"):
            s = _make_session(session_id=sid)
            s._exit_task = MagicMock(done=MagicMock(return_value=True))
            svc._sessions[sid] = s

        with (
            patch("backend.services.terminal_service.asyncio.to_thread", new_callable=AsyncMock),
            patch("backend.services.terminal_service.os.close"),
        ):
            await svc.shutdown()

        assert len(svc.sessions) == 0


@posix_only
class TestOnPtyReadable:
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.os.read", return_value=b"hello output")
    def test_reads_data_and_appends_scrollback(self, mock_read: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = _make_session(session_id="s1", master_fd=42)
        svc._sessions["s1"] = session

        svc._on_pty_readable("s1")

        mock_read.assert_called_once_with(42, 65536)
        assert "hello output" in session.scrollback

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.os.read", side_effect=OSError("read error"))
    def test_handles_read_os_error(self, mock_read: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._sessions["s1"] = _make_session(session_id="s1")
        # Should not raise
        svc._on_pty_readable("s1")

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.os.read", return_value=b"")
    def test_empty_read_returns_early(self, mock_read: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = _make_session(session_id="s1")
        svc._sessions["s1"] = session
        svc._on_pty_readable("s1")
        # Scrollback should remain empty
        assert session.scrollback == ""

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_nonexistent_session_returns_early(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        # Should not raise
        svc._on_pty_readable("nope")

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.os.read", return_value=b"data")
    @patch("backend.services.terminal_service.asyncio.ensure_future")
    def test_broadcasts_to_websocket_clients(
        self, mock_ensure: MagicMock, mock_read: MagicMock, mock_detect: MagicMock
    ) -> None:
        svc = TerminalService()
        session = _make_session(session_id="s1")
        ws = AsyncMock()
        session.clients.add(ws)
        svc._sessions["s1"] = session

        svc._on_pty_readable("s1")

        mock_ensure.assert_called_once()
        # The message sent should be JSON with type=output
        # ws.send_text was the coroutine passed to ensure_future

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    @patch("backend.services.terminal_service.os.read", return_value=b"data")
    @patch("backend.services.terminal_service.asyncio.ensure_future", side_effect=Exception("ws dead"))
    def test_removes_dead_clients(self, mock_ensure: MagicMock, mock_read: MagicMock, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = _make_session(session_id="s1")
        ws = AsyncMock()
        session.clients.add(ws)
        svc._sessions["s1"] = session

        svc._on_pty_readable("s1")

        # Dead client should be removed
        assert ws not in session.clients


@posix_only
class TestWatchExit:
    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_watch_exit_cleans_up_on_process_exit(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()
        session = _make_session(session_id="s1", master_fd=42)
        svc._sessions["s1"] = session

        with (
            patch("backend.services.terminal_service.asyncio.to_thread", new_callable=AsyncMock, return_value=0),
            patch("backend.services.terminal_service.os.close"),
        ):
            await svc._watch_exit("s1")

        assert "s1" not in svc.sessions

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_watch_exit_nonexistent_session(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        # Should not raise
        await svc._watch_exit("nope")

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_watch_exit_notifies_clients(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()
        session = _make_session(session_id="s1")
        ws = AsyncMock()
        session.clients.add(ws)
        svc._sessions["s1"] = session

        with (
            patch("backend.services.terminal_service.asyncio.to_thread", new_callable=AsyncMock, return_value=0),
            patch("backend.services.terminal_service.os.close"),
        ):
            await svc._watch_exit("s1")

        ws.send_text.assert_called_once()
        msg = json.loads(ws.send_text.call_args[0][0])
        assert msg["type"] == "exit"
        assert msg["code"] == 0


@posix_only
class TestCleanupSession:
    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_cleanup_cancels_exit_task(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()
        session = _make_session(session_id="s1")
        mock_task = MagicMock()
        mock_task.done.return_value = False
        session._exit_task = mock_task

        with (
            patch("backend.services.terminal_service.asyncio.to_thread", new_callable=AsyncMock),
            patch("backend.services.terminal_service.os.close"),
        ):
            await svc._cleanup_session(session)

        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_cleanup_kills_process(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()
        session = _make_session(session_id="s1")
        session._exit_task = None

        with (
            patch("backend.services.terminal_service.asyncio.to_thread", new_callable=AsyncMock),
            patch("backend.services.terminal_service.os.close"),
        ):
            await svc._cleanup_session(session)

        session.process.kill.assert_called_once()

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_cleanup_handles_process_already_dead(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()
        session = _make_session(session_id="s1")
        session._exit_task = None
        session.process.kill.side_effect = ProcessLookupError

        with patch("backend.services.terminal_service.os.close"):
            # Should not raise
            await svc._cleanup_session(session)

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_cleanup_notifies_and_clears_clients(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        svc._loop = MagicMock()
        session = _make_session(session_id="s1")
        session._exit_task = None
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        session.clients = {ws1, ws2}

        with (
            patch("backend.services.terminal_service.asyncio.to_thread", new_callable=AsyncMock),
            patch("backend.services.terminal_service.os.close"),
        ):
            await svc._cleanup_session(session)

        # All clients should have been notified and cleared
        assert len(session.clients) == 0
        # Both websockets should have received exit message
        for ws in (ws1, ws2):
            ws.send_text.assert_called_once()
            msg = json.loads(ws.send_text.call_args[0][0])
            assert msg["type"] == "exit"
            assert msg["code"] == -1

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_cleanup_removes_reader(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        mock_loop = MagicMock()
        svc._loop = mock_loop
        session = _make_session(session_id="s1", master_fd=42)
        session._exit_task = None

        with (
            patch("backend.services.terminal_service.asyncio.to_thread", new_callable=AsyncMock),
            patch("backend.services.terminal_service.os.close"),
        ):
            await svc._cleanup_session(session)

        mock_loop.remove_reader.assert_called_with(42)


# ------------------------------------------------------------------
# Windows-specific tests (mock pywinpty; run on all platforms)
# ------------------------------------------------------------------

windows_pty = pytest.mark.skipif(sys.platform != "win32", reason="Windows ConPTY tests")


def _make_win_proc(pid: int = 99999) -> MagicMock:
    """Mock of winpty.PtyProcess."""
    proc = MagicMock()
    proc.pid = pid
    proc.isalive.return_value = False
    proc.exitstatus = 0
    proc.read.side_effect = EOFError
    proc.write = MagicMock()
    proc.setwinsize = MagicMock()
    proc.close = MagicMock()
    return proc


class TestWindowsTerminalService:
    """Tests for the Windows ConPTY code paths, mocking winpty.PtyProcess.

    These tests run on all platforms by patching sys.platform and the
    winpty PtyProcess import so the POSIX branch is never entered.
    """

    def _make_win_session(self, session_id: str = "win1", pid: int = 99999, **kwargs) -> PtySession:
        return PtySession(
            id=session_id,
            master_fd=-1,
            process=_make_win_proc(pid),
            shell="pwsh",
            cwd="C:\\Users\\user",
            **kwargs,
        )

    @patch("backend.services.terminal_service.shutil.which", side_effect=lambda x: f"C:\\Windows\\{x}.exe" if x == "pwsh" else None)
    def test_detect_shell_windows_returns_pwsh(self, mock_which: MagicMock) -> None:
        with patch("backend.services.terminal_service.sys") as mock_sys:
            mock_sys.platform = "win32"
            result = _detect_shell()
        assert result == "C:\\Windows\\pwsh.exe"

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_write_windows_decodes_bytes(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = self._make_win_session()
        svc._sessions["win1"] = session

        with patch("backend.services.terminal_service.sys") as mock_sys:
            mock_sys.platform = "win32"
            svc.write("win1", b"hello\n")

        session.process.write.assert_called_once_with("hello\n")

    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    def test_resize_windows_calls_setwinsize(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = self._make_win_session()
        svc._sessions["win1"] = session

        with patch("backend.services.terminal_service.sys") as mock_sys:
            mock_sys.platform = "win32"
            svc.resize("win1", cols=80, rows=24)

        session.process.setwinsize.assert_called_once_with(24, 80)

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_cleanup_windows_calls_close(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = self._make_win_session()
        session._exit_task = None

        with patch("backend.services.terminal_service.sys") as mock_sys:
            mock_sys.platform = "win32"
            await svc._cleanup_session(session)

        session.process.close.assert_called_once_with(force=True)

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_windows_reader_breaks_on_eof(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = self._make_win_session()
        svc._sessions["win1"] = session
        # proc.read raises EOFError immediately
        session.process.read.side_effect = EOFError

        # Should complete without raising
        await svc._windows_reader("win1")

    @pytest.mark.asyncio
    @patch("backend.services.terminal_service._detect_shell", return_value="/bin/bash")
    async def test_windows_reader_fans_out_to_clients(self, mock_detect: MagicMock) -> None:
        svc = TerminalService()
        session = self._make_win_session()
        ws = AsyncMock()
        session.clients.add(ws)
        svc._sessions["win1"] = session

        # First read returns data, second raises EOFError to stop the loop
        session.process.read.side_effect = ["hello output", EOFError()]

        with patch("backend.services.terminal_service.asyncio.ensure_future") as mock_ef:
            await svc._windows_reader("win1")

        mock_ef.assert_called_once()
        assert "hello output" in session.scrollback
