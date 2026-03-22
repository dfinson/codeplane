"""Interactive terminal session management via PTY.

Cross-platform: uses the POSIX pty/fcntl/termios stack on Linux/macOS and
pywinpty (ConPTY) on Windows.

Sessions may optionally be tagged with a ``job_id`` so they are automatically
cleaned up when the associated job's worktree is removed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import shlex
import shutil
import struct
import subprocess  # noqa: S404
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from fastapi import WebSocket

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Platform-specific imports
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    try:
        from winpty import PtyProcess as _WinPtyProcess
    except ImportError as _e:
        raise ImportError(
            "pywinpty is required for terminal support on Windows. Install it with: pip install pywinpty"
        ) from _e
    _SIGWINCH: int | None = None
else:
    import fcntl
    import pty
    import signal
    import termios

    _SIGWINCH = signal.SIGWINCH

if sys.platform != "win32":
    _WinPtyProcess = None

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


def _normalize_prompt_label(prompt_label: str | None) -> str | None:
    """Normalize operator-provided prompt labels for shell injection."""
    if prompt_label is None:
        return None
    label = prompt_label.strip()
    return label or None


def _powershell_single_quote(value: str) -> str:
    """Return a PowerShell single-quoted string literal."""
    return "'" + value.replace("'", "''") + "'"


def _shell_prompt_assignment(prompt_label: str | None, shell_name: str) -> str | None:
    """Return a shell-specific compact prompt assignment when a label is provided."""
    label = _normalize_prompt_label(prompt_label)
    if label is None:
        return None
    escaped = shlex.quote(f"{label} $ ")
    if shell_name == "bash":
        return f"PS1={escaped}"
    if shell_name in ("sh", "dash"):
        return f"PS1={escaped}"
    if shell_name == "zsh":
        return f"PROMPT={escaped}"
    if shell_name in ("pwsh", "powershell"):
        return f"function prompt {{ {_powershell_single_quote(label + '> ')} }}"
    return None


def _detect_shell() -> str:
    """Auto-detect the preferred shell for the current platform."""
    if sys.platform == "win32":
        # Prefer PowerShell 7 (pwsh), then PowerShell 5, then cmd
        for candidate in ("pwsh", "powershell", "cmd"):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return "cmd.exe"
    # POSIX: respect $SHELL, then fall back through common paths
    shell = os.environ.get("SHELL")
    if shell and os.path.isfile(shell):
        return shell
    for candidate in ("/bin/bash", "/bin/zsh", "/bin/sh"):
        if os.path.isfile(candidate):
            return candidate
    return "/bin/sh"


@dataclass
class PtySession:
    """A single PTY session with its process, FDs, and attached clients.

    ``master_fd`` is the POSIX PTY master file descriptor; it is set to ``-1``
    on Windows where it is not used.  ``process`` is a ``subprocess.Popen``
    instance on POSIX or a ``winpty.PtyProcess`` instance on Windows.
    """

    id: str
    master_fd: int  # -1 on Windows
    process: Any  # subprocess.Popen (POSIX) | winpty.PtyProcess (Windows)
    shell: str
    cwd: str
    job_id: str | None = None
    clients: set[WebSocket] = field(default_factory=set)
    scrollback: str = ""
    scrollback_limit: int = 500 * 1024  # bytes
    _exit_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _zdotdir: str | None = field(default=None, repr=False)
    _win_reader_task: asyncio.Task[None] | None = field(default=None, repr=False)

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
        prompt_label: str | None = None,
        cols: int = 120,
        rows: int = 30,
    ) -> PtySession:
        """Spawn a new PTY session. Returns the session object."""
        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError(f"Maximum terminal sessions ({self._max_sessions}) reached")

        shell = shell or self._default_shell
        if not os.path.isfile(shell):
            resolved = shutil.which(shell)
            if resolved:
                shell = resolved
            else:
                raise ValueError(f"Shell not found: {shell}")

        cwd = cwd or os.path.expanduser("~")
        if not os.path.isdir(cwd):
            raise ValueError(f"Working directory does not exist: {cwd}")

        if self._loop is None:
            self._loop = asyncio.get_event_loop()

        session_id = secrets.token_hex(16)

        if sys.platform == "win32":
            session = self._create_session_windows(session_id, shell, cwd, job_id, prompt_label, cols, rows)
        else:
            session = self._create_session_posix(session_id, shell, cwd, job_id, prompt_label, cols, rows)

        self._sessions[session_id] = session

        session._exit_task = asyncio.create_task(
            self._watch_exit(session_id),
            name=f"terminal-exit-{session_id[:8]}",
        )
        if sys.platform == "win32":
            session._win_reader_task = asyncio.create_task(
                self._windows_reader(session_id),
                name=f"terminal-reader-{session_id[:8]}",
            )

        log.info(
            "terminal_session_created",
            session_id=session_id,
            shell=shell,
            cwd=cwd,
            job_id=job_id,
            pid=session.process.pid,
        )
        return session

    def _create_session_posix(
        self,
        session_id: str,
        shell: str,
        cwd: str,
        job_id: str | None,
        prompt_label: str | None,
        cols: int,
        rows: int,
    ) -> PtySession:
        """Spawn a PTY session using the POSIX pty/fcntl/termios stack."""
        import tempfile

        master_fd, slave_fd = pty.openpty()

        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        env = {**os.environ, "TERM": "xterm-256color", "CODEPLANE_TERMINAL": "1"}

        shell_name = os.path.basename(shell)
        prompt_assignment = _shell_prompt_assignment(prompt_label, shell_name)
        zdotdir: str | None = None
        if shell_name == "bash":
            env["PROMPT_COMMAND"] = prompt_assignment or r'PS1="…$(basename "$PWD") \$ "'
        elif shell_name in ("sh", "dash"):
            zdotdir = tempfile.mkdtemp(prefix="codeplane_sh_")
            env_script = os.path.join(zdotdir, ".shrc")
            with open(env_script, "w") as _f:
                _f.write((prompt_assignment or 'PS1="…$(basename "$PWD") $ "') + "\n")
            env["ENV"] = env_script
        elif shell_name == "zsh":
            zdotdir = tempfile.mkdtemp(prefix="codeplane_zsh_")
            real_zshrc = os.path.expanduser("~/.zshrc")
            zshrc_lines = [
                f'[[ -f "{real_zshrc}" ]] && source "{real_zshrc}"\n',
                (prompt_assignment or 'PROMPT="…%1~ %# "') + "\n",
            ]
            with open(os.path.join(zdotdir, ".zshrc"), "w") as _f:
                _f.writelines(zshrc_lines)
            env["ZDOTDIR"] = zdotdir

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
            if zdotdir:
                shutil.rmtree(zdotdir, ignore_errors=True)
            raise

        os.close(slave_fd)

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
            _zdotdir=zdotdir,
        )

        assert self._loop is not None
        self._loop.add_reader(master_fd, self._on_pty_readable, session_id)
        return session

    def _create_session_windows(
        self,
        session_id: str,
        shell: str,
        cwd: str,
        job_id: str | None,
        prompt_label: str | None,
        cols: int,
        rows: int,
    ) -> PtySession:
        """Spawn a PTY session using the Windows ConPTY backend (pywinpty)."""
        env = {**os.environ, "CODEPLANE_TERMINAL": "1"}

        # Compact prompt injection per shell type.
        shell_name = os.path.basename(shell).lower().removesuffix(".exe")
        prompt_assignment = _shell_prompt_assignment(prompt_label, shell_name)

        if shell_name in ("pwsh", "powershell"):
            # -NoExit keeps the shell interactive after running the -Command block.
            # We define a custom prompt() function that shows only the leaf dir.
            prompt_fn = prompt_assignment or r"function prompt { '…' + (Split-Path -Leaf (Get-Location)) + '> ' }"
            argv = [shell, "-NoExit", "-Command", prompt_fn]
        else:
            # cmd.exe: PROMPT env var supports $P (full path) but not basename.
            # Prefix with the ellipsis as a visual cue; full path is acceptable here.
            env["PROMPT"] = f"{prompt_label}$G " if _normalize_prompt_label(prompt_label) else "…$P$G "
            argv = [shell]

        if _WinPtyProcess is None:
            raise RuntimeError("Windows PTY backend is unavailable")

        proc = _WinPtyProcess.spawn(
            argv,
            dimensions=(rows, cols),
            env=env,
            cwd=cwd,
        )

        return PtySession(
            id=session_id,
            master_fd=-1,  # not used on Windows
            process=proc,
            shell=shell,
            cwd=cwd,
            job_id=job_id,
            scrollback_limit=self._scrollback_limit,
        )

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
            if sys.platform == "win32":
                session.process.write(data.decode("utf-8", errors="replace"))
            else:
                os.write(session.master_fd, data)
        except OSError:
            log.warning("terminal_write_failed", session_id=session_id)

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        """Resize a PTY session."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        try:
            if sys.platform == "win32":
                session.process.setwinsize(rows, cols)
            else:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
                if _SIGWINCH is not None:
                    os.kill(session.process.pid, _SIGWINCH)
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
    # Internal — POSIX
    # ------------------------------------------------------------------

    def _on_pty_readable(self, session_id: str) -> None:
        """Callback fired by asyncio when the POSIX PTY master FD has data."""
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

    # ------------------------------------------------------------------
    # Internal — Windows
    # ------------------------------------------------------------------

    async def _windows_reader(self, session_id: str) -> None:
        """Async reader loop for a Windows ConPTY session.

        Runs a blocking ``proc.read()`` call in a thread executor and fans
        output out to all attached WebSocket clients, mirroring the behaviour
        of ``_on_pty_readable`` on POSIX.
        """
        import json

        session = self._sessions.get(session_id)
        if session is None:
            return
        loop = asyncio.get_event_loop()
        while True:
            try:
                raw = await loop.run_in_executor(None, session.process.read, 65536)
            except EOFError:
                break
            except Exception:
                break
            if not raw:
                await asyncio.sleep(0.01)
                continue
            text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
            session.append_scrollback(text)
            if session.clients:
                msg = json.dumps({"type": "output", "data": text})
                dead_ws: list[WebSocket] = []
                for ws in list(session.clients):
                    try:
                        asyncio.ensure_future(ws.send_text(msg))
                    except Exception:
                        dead_ws.append(ws)
                for ws in dead_ws:
                    session.clients.discard(ws)

    # ------------------------------------------------------------------
    # Internal — shared
    # ------------------------------------------------------------------

    async def _watch_exit(self, session_id: str) -> None:
        """Monitor for PTY process exit and notify clients."""
        import json

        session = self._sessions.get(session_id)
        if session is None:
            return

        if sys.platform == "win32":
            # winpty.PtyProcess has no blocking wait(); poll isalive() in a thread.
            def _poll_until_dead() -> int:
                import time

                while session.process.isalive():
                    time.sleep(0.2)
                return getattr(session.process, "exitstatus", None) or 0

            exit_code = await asyncio.to_thread(_poll_until_dead)
        else:
            exit_code = await asyncio.to_thread(session.process.wait)

        log.info("terminal_session_exited", session_id=session_id, exit_code=exit_code)

        if session.clients:
            msg = json.dumps({"type": "exit", "code": exit_code})
            for ws in list(session.clients):
                with contextlib.suppress(Exception):
                    await ws.send_text(msg)

        self._sessions.pop(session_id, None)
        if sys.platform != "win32" and self._loop:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(session.master_fd)
            if session.master_fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(session.master_fd)
                session.master_fd = -1

    async def _cleanup_session(self, session: PtySession) -> None:
        """Kill the process and release all resources for a session."""
        import json

        # Cancel background tasks
        for task in filter(None, [session._exit_task, session._win_reader_task]):
            if not task.done():
                task.cancel()

        if sys.platform == "win32":
            with contextlib.suppress(Exception):
                session.process.close(force=True)
        else:
            if self._loop:
                with contextlib.suppress(Exception):
                    self._loop.remove_reader(session.master_fd)
            try:
                session.process.kill()
                await asyncio.to_thread(session.process.wait)
            except (OSError, ProcessLookupError):
                pass
            if session.master_fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(session.master_fd)
                session.master_fd = -1

        msg = json.dumps({"type": "exit", "code": -1})
        for ws in list(session.clients):
            with contextlib.suppress(Exception):
                await ws.send_text(msg)
        session.clients.clear()

        log.info("terminal_session_cleaned_up", session_id=session.id, pid=session.process.pid)

        if session._zdotdir:
            with contextlib.suppress(Exception):
                shutil.rmtree(session._zdotdir, ignore_errors=True)
