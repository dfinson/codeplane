"""Interactive terminal session management via PTY.

Provides server-managed shell sessions that can be attached to from the
browser via WebSocket.  Each session spawns a real PTY process and streams
I/O through asyncio's event loop using ``add_reader`` on the master FD.

Sessions may optionally be tagged with a ``job_id`` so they are automatically
cleaned up when the associated job's worktree is removed.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import pty
import re
import secrets
import shutil
import signal
import struct
import subprocess  # noqa: S404
import termios
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from fastapi import WebSocket

log = structlog.get_logger()

# ANSI sequences stripped from scrollback before replay to avoid garbled output
# on reconnect (borrowed from TermBeam's sanitizeForReplay).

# OSC color query/response sequences cause echo loops on replay
_OSC_COLOR_RE = re.compile(r"\x1b\](?:4;\d+|10|11|12);[^\x07\x1b]*(?:\x07|\x1b\\)")
# Alt-screen buffer enter+exit pairs — content no longer relevant after exit
_ALT_SCREEN_PAIR_RE = re.compile(r"\x1b\[\?(1049|1047|47)h[\s\S]*?\x1b\[\?\1l")
_ALT_SCREEN_BARE_RE = re.compile(r"\x1b\[\?(?:1049|1047|47)[hl]")
# Clear-scrollback would wipe xterm.js history on replay
_CLEAR_SCROLLBACK_RE = re.compile(r"\x1b\[3J")


def _sanitize_for_replay(buf: str) -> str:
    """Strip ANSI sequences that cause problems when replaying scrollback."""
    buf = _OSC_COLOR_RE.sub("", buf)
    buf = _ALT_SCREEN_PAIR_RE.sub("", buf)
    buf = _ALT_SCREEN_BARE_RE.sub("", buf)
    buf = _CLEAR_SCROLLBACK_RE.sub("", buf)
    return buf


def _detect_shell() -> str:
    """Auto-detect the user's preferred shell."""
    shell = os.environ.get("SHELL")
    if shell and os.path.isfile(shell):
        return shell
    for candidate in ("/bin/bash", "/bin/zsh", "/bin/sh"):
        if os.path.isfile(candidate):
            return candidate
    return "/bin/sh"


@dataclass
class PtySession:
    """A single PTY session with its process, FDs, and attached clients."""

    id: str
    master_fd: int
    process: subprocess.Popen  # type: ignore[type-arg]
    shell: str
    cwd: str
    job_id: str | None = None
    clients: set[WebSocket] = field(default_factory=set)
    scrollback: str = ""
    scrollback_limit: int = 500 * 1024  # bytes
    _exit_task: asyncio.Task | None = field(default=None, repr=False)  # type: ignore[type-arg]

    def append_scrollback(self, data: str) -> None:
        """Append data to the scrollback buffer, trimming if over limit."""
        self.scrollback += data
        if len(self.scrollback) > self.scrollback_limit * 2:
            # High/low watermark: trim to limit
            buf = self.scrollback[-self.scrollback_limit :]
            nl = buf.find("\n")
            if 0 < nl < 200:
                buf = buf[nl + 1 :]
            self.scrollback = buf


class TerminalService:
    """Manages PTY sessions and their lifecycle."""

    def __init__(
        self,
        *,
        max_sessions: int = 5,
        default_shell: str | None = None,
        scrollback_size_kb: int = 500,
    ) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._max_sessions = max_sessions
        self._default_shell = default_shell or _detect_shell()
        self._scrollback_limit = scrollback_size_kb * 1024
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def sessions(self) -> dict[str, PtySession]:
        return self._sessions

    def create_session(
        self,
        *,
        cwd: str | None = None,
        shell: str | None = None,
        job_id: str | None = None,
        cols: int = 120,
        rows: int = 30,
    ) -> PtySession:
        """Spawn a new PTY session. Returns the session object."""
        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError(f"Maximum terminal sessions ({self._max_sessions}) reached")

        if self._loop is None:
            self._loop = asyncio.get_event_loop()

        shell = shell or self._default_shell
        if not os.path.isfile(shell):
            # Try resolving via PATH
            resolved = shutil.which(shell)
            if resolved:
                shell = resolved
            else:
                raise ValueError(f"Shell not found: {shell}")

        cwd = cwd or os.path.expanduser("~")
        if not os.path.isdir(cwd):
            raise ValueError(f"Working directory does not exist: {cwd}")

        session_id = secrets.token_hex(16)

        master_fd, slave_fd = pty.openpty()

        # Set initial terminal size before spawning
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        env = {**os.environ, "TERM": "xterm-256color", "CODEPLANE_TERMINAL": "1"}

        try:
            proc = subprocess.Popen(  # noqa: S603
                [shell],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                cwd=cwd,
                env=env,
                close_fds=True,
            )
        except OSError:
            os.close(master_fd)
            os.close(slave_fd)
            raise

        # Parent no longer needs the slave FD
        os.close(slave_fd)

        # Set master to non-blocking for asyncio
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        session = PtySession(
            id=session_id,
            master_fd=master_fd,
            process=proc,
            shell=shell,
            cwd=cwd,
            job_id=job_id,
            scrollback_limit=self._scrollback_limit,
        )
        self._sessions[session_id] = session

        # Wire up asyncio reader for PTY output
        self._loop.add_reader(master_fd, self._on_pty_readable, session_id)

        # Monitor for process exit
        session._exit_task = asyncio.create_task(
            self._watch_exit(session_id),
            name=f"terminal-exit-{session_id[:8]}",
        )

        log.info(
            "terminal_session_created",
            session_id=session_id,
            shell=shell,
            cwd=cwd,
            job_id=job_id,
            pid=proc.pid,
        )
        return session

    def get_session(self, session_id: str) -> PtySession | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[dict[str, int | str | None]]:
        """Return summary info for all active sessions."""
        result = []
        for s in self._sessions.values():
            result.append(
                {
                    "id": s.id,
                    "shell": s.shell,
                    "cwd": s.cwd,
                    "jobId": s.job_id,
                    "pid": s.process.pid,
                    "clients": len(s.clients),
                }
            )
        return result

    async def kill_session(self, session_id: str) -> bool:
        """Kill a terminal session and clean up resources."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await self._cleanup_session(session)
        return True

    async def kill_sessions_for_job(self, job_id: str) -> int:
        """Kill all terminal sessions associated with a job. Returns count killed."""
        to_kill = [s for s in self._sessions.values() if s.job_id == job_id]
        for session in to_kill:
            self._sessions.pop(session.id, None)
            await self._cleanup_session(session)
        if to_kill:
            log.info("terminal_sessions_killed_for_job", job_id=job_id, count=len(to_kill))
        return len(to_kill)

    def write(self, session_id: str, data: bytes) -> None:
        """Write input data to a PTY session."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        try:
            os.write(session.master_fd, data)
        except OSError:
            log.warning("terminal_write_failed", session_id=session_id)

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        """Resize a PTY session."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
            os.kill(session.process.pid, signal.SIGWINCH)
        except OSError:
            log.warning("terminal_resize_failed", session_id=session_id)

    def get_scrollback(self, session_id: str) -> str:
        """Return sanitized scrollback for replay on reconnect."""
        session = self._sessions.get(session_id)
        if session is None:
            return ""
        return _sanitize_for_replay(session.scrollback)

    async def shutdown(self) -> None:
        """Kill all sessions. Called at server shutdown."""
        session_ids = list(self._sessions.keys())
        for sid in session_ids:
            session = self._sessions.pop(sid, None)
            if session:
                await self._cleanup_session(session)
        log.info("terminal_service_shutdown", sessions_killed=len(session_ids))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_pty_readable(self, session_id: str) -> None:
        """Callback fired by asyncio when the PTY master FD has data."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        try:
            data = os.read(session.master_fd, 65536)
        except OSError:
            return
        if not data:
            return

        text = data.decode("utf-8", errors="replace")
        session.append_scrollback(text)

        # Fan-out to all attached WebSocket clients
        if session.clients:
            import json

            msg = json.dumps({"type": "output", "data": text})
            dead: list[WebSocket] = []
            for ws in session.clients:
                try:
                    asyncio.ensure_future(ws.send_text(msg))
                except Exception:
                    dead.append(ws)
            for ws in dead:
                session.clients.discard(ws)

    async def _watch_exit(self, session_id: str) -> None:
        """Monitor for PTY process exit and clean up."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        exit_code = await asyncio.to_thread(session.process.wait)
        log.info("terminal_session_exited", session_id=session_id, exit_code=exit_code)

        # Notify clients
        if session.clients:
            import json

            msg = json.dumps({"type": "exit", "code": exit_code})
            for ws in list(session.clients):
                with contextlib.suppress(Exception):
                    await ws.send_text(msg)

        # Clean up
        self._sessions.pop(session_id, None)
        if self._loop:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(session.master_fd)
        with contextlib.suppress(OSError):
            os.close(session.master_fd)

    async def _cleanup_session(self, session: PtySession) -> None:
        """Kill the process and close FDs for a session."""
        # Cancel exit watcher
        if session._exit_task and not session._exit_task.done():
            session._exit_task.cancel()

        # Remove reader
        if self._loop:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(session.master_fd)

        # Kill the process
        try:
            session.process.kill()
            await asyncio.to_thread(session.process.wait)
        except (OSError, ProcessLookupError):
            pass

        # Close FD
        with contextlib.suppress(OSError):
            os.close(session.master_fd)

        # Notify clients
        import json

        msg = json.dumps({"type": "exit", "code": -1})
        for ws in list(session.clients):
            with contextlib.suppress(Exception):
                await ws.send_text(msg)
        session.clients.clear()

        log.info("terminal_session_cleaned_up", session_id=session.id, pid=session.process.pid)
