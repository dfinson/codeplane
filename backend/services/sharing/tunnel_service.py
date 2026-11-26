"""Remote access provider helpers.

Supports three runtime modes:

- ``local`` — no remote ingress
- ``devtunnel`` — private tunnel for OSS users (requires Microsoft login)
- ``cloudflare`` — user-managed stable ingress via a named Cloudflare tunnel
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

from backend.config import get_codeplane_dir
from backend.models.domain import CodePlaneError
from backend.services.dev_restart.restart_protocol import write_json_atomic

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Connector process lifecycle (portable across Windows and POSIX)
#
# Platform branches below test ``sys.platform`` rather than
# ``platform.system()`` so type checkers narrow the Win32-only and POSIX-only
# calls on either host.
# ---------------------------------------------------------------------------


_job_lock = threading.Lock()
_job_handle: Any = None
_job_assign_warned = False


def _windows_kill_on_close_job() -> Any:
    """Return a process-wide Windows Job Object that kills its members on close.

    Connectors (``cloudflared``/``devtunnel``) are long-lived children. On
    POSIX a supervisor can reap them via the process group, but on Windows a
    child outlives an abruptly terminated parent, leaving an orphaned
    connector still serving the public hostname after CodePlane is gone —
    and a subsequent ``cpl up`` then starts a *second* connector for the same
    tunnel. Assigning every connector to a ``JOB_OBJECT_LIMIT_KILL_ON_JOB_
    CLOSE`` job makes the kernel terminate them as soon as this process exits,
    however it exits. Returns ``None`` when the job cannot be created, in
    which case spawning proceeds unmanaged rather than failing.
    """
    global _job_handle
    if sys.platform != "win32":
        return None
    with _job_lock:
        if _job_handle is not None:
            return _job_handle
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

            class _IO_COUNTERS(ctypes.Structure):  # noqa: N801 - mirrors the Win32 struct name
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801 - mirrors the Win32 struct name
                _fields_ = [
                    ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.POINTER(wintypes.ULONG)),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801 - mirrors the Win32 struct name
                _fields_ = [
                    ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return None
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                job,
                9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                kernel32.CloseHandle(job)
                return None
            _job_handle = job
            return job
        except (OSError, AttributeError, ValueError):
            log.debug("tunnel_job_object_unavailable", exc_info=True)
            return None


def _assign_to_kill_on_close_job(proc: subprocess.Popen[str]) -> bool:
    """Best-effort: bind *proc* to the kill-on-close job so it cannot outlive us.

    Returns whether the connector is actually covered. Orphan prevention is
    hardening, never a startup precondition: every failure path degrades to an
    unmanaged connector rather than failing the tunnel.

    The assignment genuinely fails on some Windows hosts. Modern Windows places
    most processes in an ambient job (so the connector inherits one at spawn),
    and ``AssignProcessToJobObject`` then returns ``ERROR_ACCESS_DENIED``
    rather than nesting it. The result must therefore be checked and reported:
    discarding it left the code claiming an orphan guarantee it did not have,
    with no way to tell the difference from a log. When this fails, an abrupt
    kill (as opposed to a graceful shutdown, which terminates the connector
    explicitly) leaves the connector running; the next ``cpl up`` adopts it via
    origin-reuse detection instead of starting a second one for the same tunnel.
    """
    global _job_assign_warned
    if sys.platform != "win32":
        return False
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int):
        return False
    job = _windows_kill_on_close_job()
    if job is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.OpenProcess(0x0200 | 0x1000 | 0x0001, False, pid)  # SET_QUOTA|SET_INFO|TERMINATE
        if not handle:
            return False
        try:
            assigned = bool(kernel32.AssignProcessToJobObject(job, handle))
            if not assigned and not _job_assign_warned:
                _job_assign_warned = True
                log.info(
                    "tunnel_job_assign_unavailable",
                    pid=pid,
                    error=ctypes.get_last_error(),
                    detail="connector may outlive an abrupt kill; a later start will reuse it",
                )
            return assigned
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        log.debug("tunnel_job_assign_failed", pid=pid, exc_info=True)
        return False


def _spawn_kwargs() -> dict[str, Any]:
    """Popen keyword arguments that keep a connector reapable on this platform."""
    if sys.platform == "win32":
        # A new process group prevents a console Ctrl+C aimed at CodePlane from
        # racing our own explicit terminate/kill sequence for the connector.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    # A dedicated session lets us signal the whole connector process tree,
    # since cloudflared/devtunnel may fork helpers of their own.
    return {"start_new_session": True}


def _spawn_connector(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    """Start a connector process with portable orphan-prevention applied."""
    proc = subprocess.Popen(  # noqa: S603 - fixed connector argv built from validated config
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        **_spawn_kwargs(),
    )
    _assign_to_kill_on_close_job(proc)
    return proc


def _terminate_and_reap(proc: subprocess.Popen[str], *, label: str = "tunnel", timeout: float = 5) -> None:
    """Terminate *proc* and guarantee it is reaped, on any platform.

    ``Popen.wait`` raises ``subprocess.TimeoutExpired``, which is a
    ``SubprocessError`` and **not** an ``OSError``. Catching only ``OSError``
    here (the previous behavior) let a connector that ignores the terminate
    signal propagate an exception out of shutdown, abandoning every remaining
    cleanup step and leaking the sibling connector process. This never raises.
    """
    if proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    try:
        _signal_process_tree(proc)
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        log.warning("tunnel_terminate_timeout", provider=label, pid=pid, timeout_seconds=timeout)
    except OSError:
        log.debug("tunnel_terminate_failed", provider=label, pid=pid, exc_info=True)

    try:
        proc.kill()
        proc.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        log.warning("tunnel_kill_failed", provider=label, pid=pid)


def _signal_process_tree(proc: subprocess.Popen[str]) -> None:
    """Send a terminate signal to *proc*, including any children it spawned.

    POSIX signals the whole process group. Windows has no group to signal and
    ``Popen.terminate`` reaches only the named process, so descendants are
    enumerated and terminated explicitly — ``devtunnel host`` in particular
    runs its transport in a child, which survived a bare ``terminate()`` and
    kept the tunnel serving after CodePlane thought it had torn it down.
    """
    pid = getattr(proc, "pid", None)
    if sys.platform != "win32" and isinstance(pid, int):
        import signal

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            return
        except OSError:
            pass  # No process group (or already gone) — fall back to a direct signal.
    elif isinstance(pid, int):
        _terminate_windows_descendants(pid)
    proc.terminate()


def _terminate_windows_descendants(pid: int) -> None:
    """Terminate the descendants of *pid* (best effort); the caller handles *pid*."""
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a declared dependency
        return
    try:
        children = psutil.Process(pid).children(recursive=True)
    except (psutil.Error, OSError):
        return
    for child in children:
        with contextlib.suppress(Exception):
            child.terminate()


class RemoteProvider(StrEnum):
    local = "local"
    devtunnel = "devtunnel"
    cloudflare = "cloudflare"


class TunnelOwnership(StrEnum):
    """Explicit connector ownership for a remote access provider.

    ``managed`` means this CodePlane instance starts and owns the connector
    process outright. ``external`` means CodePlane never starts, scans for,
    or otherwise controls a connector process — it only resolves the exact
    recorded hostname/tunnel name into an origin string so the caller can
    probe it (SPEC CAP-6, ARCHITECTURE-SPINE AD-8).
    """

    managed = "managed"
    external = "external"


class TunnelStartError(CodePlaneError):
    """Raised when a remote access provider cannot be started."""


@dataclass(slots=True)
class TunnelHandle:
    """Tracks a running remote access connector and its cleanup state."""

    provider: RemoteProvider
    origin: str | None = None
    proc: subprocess.Popen[str] | None = None
    watchdog: TunnelWatchdog | None = None
    externally_managed: bool = False
    # Stable tunnel identity (devtunnel name or Cloudflare hostname) after
    # startup -- the active launch profile persists this for restart replay
    # (Story 1.1, ARCHITECTURE-SPINE.md AD-5). ``None`` when no tunnel applies.
    name: str | None = None
    # True when ``origin`` is a stable, explicitly configured identity (a
    # named Dev Tunnel or a fixed Cloudflare hostname) rather than a name
    # generated for this run. Restart tooling uses this to decide whether the
    # origin can be reused as-is or must be republished after restart
    # (ARCHITECTURE-SPINE AD-8: "reusable" vs "non-reusable" origin).
    origin_is_reusable: bool = False
    _close_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _closed: bool = field(default=False, repr=False)

    def close(self) -> None:
        """Stop the connector. Safe to call repeatedly and from any thread.

        Three paths race to call this during shutdown -- the second-signal
        handler, the force-exit timer thread, and the normal ``finally`` -- so
        without serialization each could run its own terminate/wait/kill
        sequence against the same pids.
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self.watchdog is not None:
                self.watchdog.stop()
                with self.watchdog._lock:
                    watchdog_proc = self.watchdog.proc
            else:
                watchdog_proc = None
            # Terminate all unique process references to avoid orphans from mid-restart races
            procs_to_kill: set[subprocess.Popen[str]] = set()
            if self.proc is not None:
                procs_to_kill.add(self.proc)
            if watchdog_proc is not None:
                procs_to_kill.add(watchdog_proc)
            for p in procs_to_kill:
                _terminate_and_reap(p, label=self.provider.value)


class TunnelWatchdog:
    """Restart a tunnel host process when the remote relay stops forwarding."""

    _CHECK_INTERVAL: float = 10
    _INITIAL_DELAY: float = 30  # wait for the HTTP server to finish startup before first health check
    _FAIL_THRESHOLD = 2
    _HTTP_TIMEOUT = 5
    _RESTART_ATTEMPTS = 3
    _RESTART_GRACE_PERIOD = 2
    _RECOVERY_TIMEOUT = 15
    _MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB cap on captured process output
    _GIVEUP_COOLDOWN: float = 60  # seconds before reattempting after all restart attempts fail
    _RELAY_CHECK_FREQUENCY = 5  # verify tunnel relay URL every N health checks
    _BACKOFF_BASE = 5  # exponential backoff base between restart attempts (seconds)

    def __init__(
        self,
        *,
        tunnel_url: str,
        restart_command: list[str],
        proc: subprocess.Popen[str],
        label: str,
        local_port: int | None = None,
        restart_env: dict[str, str] | None = None,
    ) -> None:
        self.tunnel_url = tunnel_url
        self.restart_command = restart_command
        self.restart_env = restart_env
        self.proc = proc
        self.label = label
        self._local_port = local_port
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: Any = None

    def start(self) -> None:
        import threading

        self._thread = threading.Thread(target=self._run, daemon=True, name=f"{self.label}-watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _origin_ok(self) -> bool:
        """Whether the *local* origin is serving.

        Returns ``True`` when no local port is known: the caller uses this to
        suppress restarts while the origin is down, and an unknown origin must
        not be reported as down — that would make every relay failure look
        like an origin outage and disable restarts entirely.
        """
        if not self._local_port:
            return True
        return self._health_ok()

    # Server errors are not authentication gates, including Cloudflare 530/1033
    # when no healthy connector is available. Recovery is attempted, not assured.
    _RELAY_FAILURE_STATUSES = frozenset(range(500, 600))

    def _health_ok(self, *, use_tunnel_url: bool = False) -> bool:
        import urllib.error
        import urllib.request

        if use_tunnel_url or not self._local_port:
            url = f"{self.tunnel_url}/api/health"
        else:
            url = f"http://127.0.0.1:{self._local_port}/api/health"
        remote = use_tunnel_url or not self._local_port
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self._HTTP_TIMEOUT) as resp:  # noqa: S310
                if remote:
                    return resp.status not in self._RELAY_FAILURE_STATUSES
                return bool(resp.status == 200)
        except urllib.error.HTTPError as exc:
            # Access rejection does not establish a connector outage. Keep its
            # exemption without treating an edge's server-error page as health.
            if remote and exc.code not in self._RELAY_FAILURE_STATUSES:
                log.debug("tunnel_relay_gated", url=url, status=exc.code)
                return True
            log.debug("tunnel_health_check_failed", url=url, status=exc.code)
            return False
        except (urllib.error.URLError, OSError, TimeoutError):
            log.debug("tunnel_health_check_failed", url=url)
            return False

    def _process_running(self, proc: subprocess.Popen[str] | None = None) -> bool:
        current = proc or self.proc
        return current is not None and current.poll() is None

    def _terminate_process(self, proc: subprocess.Popen[str] | None = None) -> None:
        current = proc or self.proc
        if current is None:
            return
        _terminate_and_reap(current, label=self.label)

    def _read_process_output(self, proc: subprocess.Popen[str]) -> str:
        if proc.stdout is None:
            return ""
        try:
            return proc.stdout.read(self._MAX_OUTPUT_BYTES).strip()
        except OSError:
            log.debug("tunnel_read_output_failed", exc_info=True)
            return ""

    def _wait_for_recovery(self) -> bool:
        deadline = time.monotonic() + self._RECOVERY_TIMEOUT
        while time.monotonic() < deadline and not self._stop_event.is_set():
            if not self._process_running():
                return False
            if self._health_ok():
                return True
            if self._stop_event.wait(timeout=1):
                return False
        return self._process_running() and self._health_ok()

    def _restart_process(self) -> bool:
        log.debug("tunnel_watchdog_restarting", provider=self.label)
        last_error = "unknown restart failure"

        env = {**os.environ, **(self.restart_env or {})} if self.restart_env else None

        for attempt in range(1, self._RESTART_ATTEMPTS + 1):
            if attempt > 1:
                backoff = self._BACKOFF_BASE * (2 ** (attempt - 2))
                log.debug("tunnel_watchdog_backoff", provider=self.label, seconds=backoff, attempt=attempt)
                if self._stop_event.wait(timeout=backoff):
                    return True

            self._terminate_process()

            try:
                proc = _spawn_connector(self.restart_command, env=env)
            except OSError as exc:
                last_error = f"could not spawn {self.restart_command[0]}: {exc}"
                log.warning(
                    "tunnel_watchdog_restart_spawn_failed",
                    provider=self.label,
                    attempt=attempt,
                    reason=last_error,
                )
                continue
            with self._lock:
                if self._stop_event.is_set():
                    # ``close()`` has already taken its snapshot of ``self.proc``;
                    # a connector published after that point would never be
                    # reaped, so retire it here instead of adopting it.
                    _terminate_and_reap(proc, label=self.label)
                    return True
                self.proc = proc

            if self._stop_event.wait(timeout=self._RESTART_GRACE_PERIOD):
                return True

            if not self._process_running(proc):
                last_error = self._read_process_output(proc) or "tunnel process exited immediately"
                log.warning(
                    "tunnel_watchdog_restart_attempt_failed",
                    provider=self.label,
                    attempt=attempt,
                    reason=last_error,
                )
                # Ensure the dead process is reaped so it doesn't linger
                self._terminate_process(proc)
                continue

            _start_output_drain(proc)

            if self._wait_for_recovery():
                log.info(
                    "tunnel_watchdog_restarted",
                    provider=self.label,
                    attempt=attempt,
                )
                return True

            last_error = "tunnel did not recover before timeout"
            log.warning(
                "tunnel_watchdog_restart_attempt_timeout",
                provider=self.label,
                attempt=attempt,
                timeout_seconds=self._RECOVERY_TIMEOUT,
            )

        log.error(
            "tunnel_watchdog_restart_gave_up",
            provider=self.label,
            attempts=self._RESTART_ATTEMPTS,
            last_error=last_error,
        )
        return False

    def _run(self) -> None:
        if self._stop_event.wait(timeout=self._INITIAL_DELAY):
            return

        consecutive_failures = 0
        check_count = 0

        while not self._stop_event.is_set():
            check_count += 1
            use_relay = check_count % self._RELAY_CHECK_FREQUENCY == 0

            if not self._process_running():
                log.warning("tunnel_watchdog_process_exited", provider=self.label)
                if not self._restart_process():
                    log.warning("tunnel_watchdog_cooldown", provider=self.label, seconds=self._GIVEUP_COOLDOWN)
                    if self._stop_event.wait(timeout=self._GIVEUP_COOLDOWN):
                        return
                consecutive_failures = 0
            elif not self._origin_ok():
                # The local origin is down or has not finished starting. The
                # connector is alive and restarting it cannot make the origin
                # answer, so this must not count toward the restart threshold:
                # observed on Windows, where startup takes longer than
                # _INITIAL_DELAY and two failed origin checks tore down and
                # respawned a perfectly healthy connector during boot. The
                # relay tally is left untouched rather than reset -- this cycle
                # says nothing about the relay either way.
                log.debug("tunnel_watchdog_origin_unavailable", provider=self.label)
            elif not use_relay:
                # Origin healthy, but the relay was not probed this cycle, so
                # there is no new evidence about it. Clearing the tally here
                # made the threshold unreachable whenever the relay is checked
                # less often than every cycle (the production default), which
                # silently disabled connector restarts entirely.
                pass
            elif self._health_ok(use_tunnel_url=True):
                consecutive_failures = 0
            else:
                # Origin healthy but the public relay does not reach it — the
                # one condition a connector restart can actually repair.
                consecutive_failures += 1
                log.debug(
                    "tunnel_watchdog_check_failed",
                    provider=self.label,
                    consecutive=consecutive_failures,
                    threshold=self._FAIL_THRESHOLD,
                )
                if consecutive_failures >= self._FAIL_THRESHOLD:
                    if not self._restart_process():
                        log.warning("tunnel_watchdog_cooldown", provider=self.label, seconds=self._GIVEUP_COOLDOWN)
                        if self._stop_event.wait(timeout=self._GIVEUP_COOLDOWN):
                            return
                    consecutive_failures = 0

            if self._stop_event.wait(timeout=self._CHECK_INTERVAL):
                return


def _start_output_drain(proc: subprocess.Popen[str]) -> None:
    """Drain stdout in a background thread to prevent pipe buffer deadlock."""
    import threading

    stdout = proc.stdout
    if stdout is None:
        return

    def _drain() -> None:
        try:
            while True:
                chunk = stdout.read(8192)
                if not chunk:
                    break
        except OSError:
            pass  # stream closed during shutdown

    threading.Thread(target=_drain, daemon=True, name="tunnel-stdout-drain").start()


def _wait_for_startup(proc: subprocess.Popen[str], *, label: str = "tunnel", timeout: float = 5) -> None:
    """Poll until the tunnel process has survived the startup window or raise on early exit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = ""
            if proc.stdout:
                with contextlib.suppress(OSError):
                    output = proc.stdout.read(64 * 1024).strip()
            raise TunnelStartError(output or f"{label} process exited during startup")
        time.sleep(0.5)


def validate_remote_provider(
    provider: RemoteProvider,
    *,
    cloudflare_token: str | None = None,
    cloudflare_hostname: str | None = None,
) -> str | None:
    """Return a user-facing error if provider prerequisites are not met."""
    if provider is RemoteProvider.local:
        return None

    if provider is RemoteProvider.devtunnel:
        if not find_devtunnel_executable():
            return "ERROR: 'devtunnel' CLI not found.\n  Install: https://aka.ms/devtunnels/cli\n  Or run: cpl setup"
        if not devtunnel_logged_in():
            # Catch the logged-out state during validation rather than letting
            # `devtunnel create` fail later with an opaque access-scope error.
            return f"ERROR: The Dev Tunnels CLI is not logged in.\n  {_DEVTUNNEL_LOGIN_HINT}"
        return None

    missing: list[str] = []
    if not cloudflare_hostname:
        missing.append("CPL_CLOUDFLARE_HOSTNAME")
    if not cloudflare_token:
        missing.append("CPL_CLOUDFLARE_TUNNEL_TOKEN")
    if missing:
        joined = ", ".join(missing)
        return (
            "ERROR: Cloudflare remote access requires additional configuration.\n"
            f"  Missing: {joined}\n"
            "  Create a named Cloudflare Tunnel and route a public hostname to localhost."
        )
    if shutil.which("cloudflared"):
        return None
    return (
        "ERROR: 'cloudflared' CLI not found.\n"
        "  Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n"
        "  Or run: cpl setup"
    )


def _external_tunnel_origin(
    provider: RemoteProvider,
    *,
    port: int,
    cloudflare_hostname: str | None,
    tunnel_name: str | None,
) -> str:
    """Resolve the exact recorded origin for an externally owned tunnel.

    Never spawns a connector process and never scans local OS processes
    (SPEC CAP-6, ARCHITECTURE-SPINE AD-8) — it only resolves the origin
    string from configuration or a name-based tunnel-service lookup (a
    remote lookup by exact name, not a local process enumeration).
    """
    if provider is RemoteProvider.cloudflare:
        if not cloudflare_hostname:
            raise TunnelStartError(
                "External Cloudflare tunnel ownership requires CPL_CLOUDFLARE_HOSTNAME to resolve the origin."
            )
        hostname = cloudflare_hostname.removeprefix("https://").rstrip("/")
        return f"https://{hostname}"

    if provider is RemoteProvider.devtunnel:
        if not tunnel_name:
            raise TunnelStartError("External Dev Tunnel ownership requires an explicit tunnel name.")
        exists, region = _lookup_devtunnel(tunnel_name)
        if not exists or not region:
            raise TunnelStartError(f"Dev Tunnel {tunnel_name!r} was not found; cannot resolve its origin externally.")
        return _devtunnel_origin(tunnel_name, port, region)

    raise TunnelStartError(f"External tunnel ownership is not supported for provider {provider.value!r}.")


def _start_cloudflare_managed(
    port: int,
    *,
    cloudflare_token: str | None,
    cloudflare_hostname: str | None,
) -> tuple[str, subprocess.Popen[str]]:
    """Start and exclusively own a fresh cloudflared connector.

    Unlike ``_start_cloudflare``'s legacy auto-detect path, this never scans
    local processes to decide whether to reuse an existing connector
    (SPEC CAP-6, ARCHITECTURE-SPINE AD-8): explicit ``managed`` ownership
    always means CodePlane starts and owns the connector itself.
    """
    if not cloudflare_token or not cloudflare_hostname:
        raise TunnelStartError("Cloudflare remote access requires a tunnel token and hostname.")

    hostname = cloudflare_hostname.removeprefix("https://").rstrip("/")
    tunnel_url = f"https://{hostname}"

    env = {**os.environ, "TUNNEL_TOKEN": cloudflare_token}
    proc = _spawn_connector(["cloudflared", "tunnel", "--no-autoupdate", "run"], env=env)
    _wait_for_startup(proc, label="cloudflare")
    _start_output_drain(proc)

    log.debug("tunnel_started", provider="cloudflare", url=tunnel_url, port=port, ownership="managed")
    return tunnel_url, proc


def start_remote_access(
    provider: RemoteProvider,
    *,
    port: int,
    cloudflare_token: str | None = None,
    cloudflare_hostname: str | None = None,
    tunnel_name: str | None = None,
    ownership: TunnelOwnership | None = None,
) -> TunnelHandle:
    """Start the selected remote access provider.

    ``ownership`` is an explicit opt-in (SPEC CAP-6, ARCHITECTURE-SPINE AD-8):

    - ``TunnelOwnership.external`` never starts a connector or scans local
      processes; it only resolves the exact recorded hostname/tunnel name.
    - ``TunnelOwnership.managed`` always starts and owns a fresh connector,
      skipping any reuse-detection heuristics.
    - ``None`` (default) preserves the pre-existing auto-detect behavior for
      callers that have not yet been updated to record explicit ownership.
    """
    if provider is RemoteProvider.local:
        return TunnelHandle(provider=provider)

    if ownership is TunnelOwnership.external:
        origin = _external_tunnel_origin(
            provider,
            port=port,
            cloudflare_hostname=cloudflare_hostname,
            tunnel_name=tunnel_name,
        )
        log.info("tunnel_external_ownership", provider=provider.value, origin=origin)
        return TunnelHandle(
            provider=provider,
            origin=origin,
            proc=None,
            externally_managed=True,
            origin_is_reusable=True,
        )

    if provider is RemoteProvider.devtunnel:
        origin, proc, resolved_name, origin_is_reusable = _start_devtunnel(port, tunnel_name=tunnel_name)
        handle = TunnelHandle(
            provider=provider,
            origin=origin,
            proc=proc,
            name=resolved_name,
            origin_is_reusable=origin_is_reusable,
        )
        handle.watchdog = TunnelWatchdog(
            tunnel_url=origin,
            restart_command=[find_devtunnel_executable() or "devtunnel", "host", resolved_name],
            proc=proc,
            label="devtunnel",
            local_port=port,
        )
        handle.watchdog.start()
        return handle

    cf_proc: subprocess.Popen[str] | None
    if ownership is TunnelOwnership.managed:
        origin, cf_proc = _start_cloudflare_managed(
            port, cloudflare_token=cloudflare_token, cloudflare_hostname=cloudflare_hostname
        )
    else:
        # ownership is None: legacy auto-detect path, preserved for callers
        # that have not yet been updated to record explicit ownership.
        origin, cf_proc = _start_cloudflare(
            port, cloudflare_token=cloudflare_token, cloudflare_hostname=cloudflare_hostname
        )
    externally_managed = cf_proc is None
    resolved_hostname = (cloudflare_hostname or "").removeprefix("https://").rstrip("/") or None
    handle = TunnelHandle(
        provider=provider,
        origin=origin,
        proc=cf_proc,
        externally_managed=externally_managed,
        name=resolved_hostname,
        origin_is_reusable=True,  # a Cloudflare origin is always a fixed configured hostname
    )
    if cf_proc is not None:
        # We started our own process — attach a watchdog to keep it alive
        handle.watchdog = TunnelWatchdog(
            tunnel_url=origin,
            restart_command=["cloudflared", "tunnel", "--no-autoupdate", "run"],
            restart_env={"TUNNEL_TOKEN": cloudflare_token or ""},
            proc=cf_proc,
            label="cloudflare",
            local_port=port,
        )
        handle.watchdog.start()
    else:
        log.info("tunnel_reusing_existing", provider="cloudflare", url=origin)
    return handle


def _run_capture(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a provider CLI command, never raising on timeout or a missing binary.

    ``subprocess.run(timeout=...)`` raises ``TimeoutExpired`` and a missing
    executable raises ``FileNotFoundError``; both used to escape every caller
    and surface as a raw traceback out of ``cpl up`` instead of a
    ``TunnelStartError`` with an actionable message. A synthetic non-zero
    result is returned instead so callers keep their normal error handling.
    """
    if args[0] == "devtunnel":
        args = [find_devtunnel_executable() or "devtunnel", *args[1:]]
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30)  # noqa: S603
    except subprocess.TimeoutExpired:
        log.warning("tunnel_cli_timeout", command=args[0], timeout_seconds=30)
        return subprocess.CompletedProcess(args, returncode=124, stdout="", stderr=f"{args[0]} timed out after 30s")
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, returncode=127, stdout="", stderr=f"{args[0]} executable not found")
    except OSError as exc:
        log.warning("tunnel_cli_failed", command=args[0], error=str(exc))
        return subprocess.CompletedProcess(args, returncode=126, stdout="", stderr=str(exc))


_CODEPLANE_TUNNEL_PREFIX = "cpl-"

_DEVTUNNEL_FALLBACK_PATHS = (
    Path.home() / "bin" / ("devtunnel.exe" if sys.platform == "win32" else "devtunnel"),
    Path.home() / ".devtunnel" / ("devtunnel.exe" if sys.platform == "win32" else "devtunnel"),
    Path("/usr/local/bin/devtunnel"),
)


def find_devtunnel_executable() -> str | None:
    """Keep normal PATH resolution, otherwise check official installer locations."""
    if shutil.which("devtunnel"):
        return "devtunnel"
    for candidate in _DEVTUNNEL_FALLBACK_PATHS:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


@dataclass(frozen=True)
class _StableDevTunnel:
    name: str
    port: int
    origin: str


def _read_stable_devtunnel() -> _StableDevTunnel | None:
    path = get_codeplane_dir() / "devtunnel.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise TunnelStartError(f"Cannot read saved Dev Tunnel identity at {path}: {exc}") from exc
    if isinstance(payload, dict):
        name, port, origin = payload.get("tunnelName"), payload.get("port"), payload.get("origin")
        if (
            isinstance(name, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", name)
            and type(port) is int
            and 1 <= port <= 65535
            and isinstance(origin, str)
            and _valid_devtunnel_origin(origin)
        ):
            return _StableDevTunnel(name=name, port=port, origin=origin)
    raise TunnelStartError(f"Invalid saved Dev Tunnel identity at {path}; repair or remove that record.")


def _valid_devtunnel_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").endswith(".devtunnels.ms")
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _devtunnel_origin(tunnel_name: str, port: int, region: str) -> str:
    result = _run_capture(["devtunnel", "show", tunnel_name, "--json"])
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = None
        tunnel = payload.get("tunnel", payload) if isinstance(payload, dict) else None
        ports = tunnel.get("ports") if isinstance(tunnel, dict) else None
        if isinstance(ports, list):
            for entry in ports:
                if isinstance(entry, dict) and entry.get("portNumber") == port:
                    uri = entry.get("portUri")
                    if isinstance(uri, str) and _valid_devtunnel_origin(uri):
                        return uri.rstrip("/")
    log.warning("devtunnel_port_uri_unavailable", tunnel=tunnel_name, port=port)
    return f"https://{tunnel_name}-{port}.{region}.devtunnels.ms"


# Substrings that identify a logged-out Dev Tunnels CLI. The CLI does not use a
# single consistent phrasing: ``devtunnel list`` says "Login required." while
# ``devtunnel create`` reports "Unauthorized tunnel creation access: Anonymous
# does not have 'create' access scope", so matching only on "login required"
# silently drops the actionable hint on exactly the path a first-time user hits.
# An expired login is reported as "Login token expired." — and, unlike the
# other wordings, ``devtunnel user show`` still exits 0 while printing it, so
# the text is the only signal that the CLI cannot actually reach the service.
_DEVTUNNEL_LOGGED_OUT_MARKERS = (
    "login required",
    "not logged in",
    "anonymous does not have",
    "unauthorized tunnel",
    "please log in",
    "token expired",
    "login expired",
)

_DEVTUNNEL_LOGIN_HINT = "Dev Tunnels require a Microsoft or GitHub account. Run:\n  devtunnel user login"


def _looks_logged_out(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _DEVTUNNEL_LOGGED_OUT_MARKERS)


def devtunnel_logged_in() -> bool:
    """Return whether the Dev Tunnels CLI currently holds a usable login."""
    result = _run_capture(["devtunnel", "user", "show"])
    if result.returncode != 0:
        return False
    combined = f"{result.stdout}\n{result.stderr}"
    return not _looks_logged_out(combined)


def _list_devtunnels() -> list[dict[str, Any]]:
    """Return the parsed tunnel list from ``devtunnel list --json``."""
    list_result = _run_capture(["devtunnel", "list", "--json"])
    if list_result.returncode != 0:
        return []
    try:
        data = json.loads(list_result.stdout)
    except json.JSONDecodeError:
        return []
    result: list[dict[str, Any]] = data.get("tunnels", [])
    return result


def _lookup_devtunnel(tunnel_name: str) -> tuple[bool, str | None]:
    for tunnel in _list_devtunnels():
        tunnel_id = tunnel.get("tunnelId", "")
        if not tunnel_id:
            continue
        name, _, region = tunnel_id.partition(".")
        if name == tunnel_name:
            return True, region or None
    return False, None


def _find_existing_codeplane_tunnel() -> tuple[str, str] | None:
    """Find an existing tunnel whose name starts with the codeplane prefix.

    Returns ``(name, region)`` or ``None``.
    """
    for tunnel in _list_devtunnels():
        tunnel_id = tunnel.get("tunnelId", "")
        if not tunnel_id:
            continue
        name, _, region = tunnel_id.partition(".")
        if name.startswith(_CODEPLANE_TUNNEL_PREFIX) and region:
            return name, region
    return None


def _devtunnel_port_registered(tunnel_name: str, port: int) -> bool:
    """Ask the service whether *port* is already registered on *tunnel_name*.

    Uses the CLI's structured output rather than its human table: the original
    defect here was code keyed on prose the CLI never printed, and parsing a
    localizable table would be the same bet in a new place.
    """
    result = _run_capture(["devtunnel", "port", "list", tunnel_name, "--json"])
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        log.debug("devtunnel_port_list_unparsable", tunnel=tunnel_name)
        return False
    ports = payload.get("ports") if isinstance(payload, dict) else None
    if not isinstance(ports, list):
        return False
    return any(isinstance(entry, dict) and entry.get("portNumber") == port for entry in ports)


def _start_devtunnel(port: int, *, tunnel_name: str | None = None) -> tuple[str, subprocess.Popen[str], str, bool]:
    # Reusable means the identity is either explicitly configured by the
    # caller or an already-registered codeplane tunnel — restart tooling can
    # rely on the exact same origin next time. A freshly generated random
    # name is not yet known anywhere else, so it is not reusable this run
    # (ARCHITECTURE-SPINE AD-8).
    previous = _read_stable_devtunnel()
    if not tunnel_name and previous is not None:
        tunnel_name = previous.name
    if tunnel_name:
        # Explicit name — use as-is
        exists, region = _lookup_devtunnel(tunnel_name)
        origin_is_reusable = True
    else:
        # Auto mode — reuse an existing codeplane tunnel or generate a random name
        existing = _find_existing_codeplane_tunnel()
        if existing:
            tunnel_name, region = existing
            exists = True
            origin_is_reusable = True
        else:
            tunnel_name = f"{_CODEPLANE_TUNNEL_PREFIX}{secrets.token_hex(4)}"
            exists, region = False, None
            origin_is_reusable = False

    if not exists:
        create_result = _run_capture(["devtunnel", "create", tunnel_name, "--expiration", "30d"])
        if create_result.returncode != 0:
            msg = create_result.stderr.strip() or create_result.stdout.strip() or "devtunnel create failed"
            if _looks_logged_out(msg):
                msg += f"\n\n{_DEVTUNNEL_LOGIN_HINT}"
            raise TunnelStartError(msg)

    port_result = _run_capture(["devtunnel", "port", "create", tunnel_name, "-p", str(port), "--protocol", "http"])
    if port_result.returncode != 0 and not _devtunnel_port_registered(tunnel_name, port):
        # A port that is already registered is the normal case when reusing a
        # tunnel, and the real CLI reports it as "Tunnel service error:
        # Conflict with existing entity" — not the "already"/"exists" wording
        # this once matched on, so every reuse of an existing tunnel aborted
        # the run. Asking the service which ports exist is authoritative and
        # survives rewordings and localized output; a genuine failure still
        # raises, because a tunnel that cannot forward must not be treated as
        # usable just because the host process starts.
        raise TunnelStartError(
            f"Could not register port {port} on Dev Tunnel {tunnel_name!r}: "
            f"{port_result.stderr.strip() or port_result.stdout.strip() or 'unknown error'}"
        )

    _, region = _lookup_devtunnel(tunnel_name)
    if not region:
        raise TunnelStartError("Could not determine the Dev Tunnel region.")

    tunnel_url = _devtunnel_origin(tunnel_name, port, region)
    if previous is not None and previous.origin != tunnel_url:
        log.warning("devtunnel_origin_changed", previous_origin=previous.origin, origin=tunnel_url)
    try:
        write_json_atomic(
            get_codeplane_dir() / "devtunnel.json",
            {"tunnelName": tunnel_name, "port": port, "origin": tunnel_url},
        )
    except OSError as exc:
        raise TunnelStartError(f"Cannot save Dev Tunnel identity: {exc}") from exc

    proc = _spawn_connector([find_devtunnel_executable() or "devtunnel", "host", tunnel_name])
    _wait_for_startup(proc, label="devtunnel")
    _start_output_drain(proc)

    log.debug("tunnel_started", provider="devtunnel", url=tunnel_url)
    return tunnel_url, proc, tunnel_name, origin_is_reusable


def _cloudflare_tunnel_id(token: str) -> str | None:
    """Extract the tunnel UUID from a Cloudflare tunnel token.

    The token is base64-encoded JSON: ``{"a": account, "t": tunnel_id, "s": secret}``.
    """
    try:
        decoded = base64.b64decode(token + "==")
        tunnel_id = json.loads(decoded).get("t")
    except Exception:
        return None
    return tunnel_id if isinstance(tunnel_id, str) and tunnel_id else None


def _cloudflared_already_running(token: str) -> bool:
    """Check whether a connector for *our* tunnel is already running here.

    Previously this shelled out to ``pgrep -x cloudflared``, which does not
    exist on Windows: the lookup always failed there and reported "not
    running", so ``cpl up --remote --provider cloudflare`` started a *second*
    connector alongside an existing one (e.g. the cloudflared Windows
    service). Two connectors registered for the same tunnel make the
    Cloudflare edge balance traffic between them, so requests intermittently
    reach the stale connector. ``psutil`` gives the same answer on every
    platform.

    Matching on the process *name* alone is not enough, though: a machine that
    runs cloudflared for some unrelated tunnel would make us skip our own
    connector and print a public URL that routes nowhere. So a process only
    counts when its command line or TUNNEL_TOKEN environment value identifies
    our tunnel. A connector we cannot attribute is treated as somebody else's, because starting a
    redundant connector for our tunnel degrades routing while reusing a
    stranger's connector breaks the URL outright.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a declared dependency
        log.debug("cloudflared_detect_unavailable")
        return False

    markers = {token}
    tunnel_id = _cloudflare_tunnel_id(token)
    if tunnel_id:
        markers.add(tunnel_id)

    our_pid = os.getpid()
    try:
        our_descendants = {child.pid for child in psutil.Process(our_pid).children(recursive=True)}
    except (psutil.Error, OSError):
        our_descendants = set()

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            # Windows reports "cloudflared.exe"; POSIX reports "cloudflared".
            if name not in ("cloudflared", "cloudflared.exe"):
                continue
            pid = proc.info["pid"]
            if pid == our_pid or pid in our_descendants:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or ())
            if any(marker in cmdline for marker in markers):
                return True
            # Our token-run connectors receive the token through the environment, not argv.
            try:
                env_token = proc.environ().get("TUNNEL_TOKEN")
            except (psutil.Error, OSError):
                log.debug("cloudflared_environment_unreadable", pid=pid, exc_info=True)
                env_token = None
            if env_token and (
                env_token == token or (tunnel_id is not None and _cloudflare_tunnel_id(env_token) == tunnel_id)
            ):
                return True
            log.debug("cloudflared_foreign_connector_ignored", pid=pid)
        except (psutil.Error, OSError):
            continue
    return False


def _start_cloudflare(
    port: int,
    *,
    cloudflare_token: str | None,
    cloudflare_hostname: str | None,
) -> tuple[str, subprocess.Popen[str] | None]:
    if not cloudflare_token or not cloudflare_hostname:
        raise TunnelStartError("Cloudflare remote access requires a tunnel token and hostname.")

    hostname = cloudflare_hostname.removeprefix("https://").rstrip("/")
    tunnel_url = f"https://{hostname}"

    # If cloudflared is already running (e.g. via systemd), reuse it
    if _cloudflared_already_running(cloudflare_token):
        log.debug("cloudflared_already_running", url=tunnel_url, port=port)
        return tunnel_url, None

    env = {**os.environ, "TUNNEL_TOKEN": cloudflare_token}
    proc = _spawn_connector(["cloudflared", "tunnel", "--no-autoupdate", "run"], env=env)
    _wait_for_startup(proc, label="cloudflare")
    _start_output_drain(proc)

    log.debug("tunnel_started", provider="cloudflare", url=tunnel_url, port=port)
    return tunnel_url, proc
