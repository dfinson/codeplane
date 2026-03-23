"""Remote access provider helpers.

Supports three runtime modes:

- ``local`` — no remote ingress
- ``devtunnel`` — zero-config remote access for OSS users
- ``cloudflare`` — user-managed stable ingress via a named Cloudflare tunnel
"""

from __future__ import annotations

import contextlib
import json
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog

log = structlog.get_logger()


class RemoteProvider(StrEnum):
    local = "local"
    devtunnel = "devtunnel"
    cloudflare = "cloudflare"


class TunnelStartError(RuntimeError):
    """Raised when a remote access provider cannot be started."""


@dataclass(slots=True)
class TunnelHandle:
    """Tracks a running remote access connector and its cleanup state."""

    provider: RemoteProvider
    origin: str | None = None
    proc: subprocess.Popen[str] | None = None
    watchdog: TunnelWatchdog | None = None

    def close(self) -> None:
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
            p.terminate()


class TunnelWatchdog:
    """Restart a tunnel host process when the remote relay stops forwarding."""

    _CHECK_INTERVAL: float = 10
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
        self._stop_event = __import__("threading").Event()
        self._lock = __import__("threading").Lock()
        self._thread: Any = None

    def start(self) -> None:
        import threading

        self._thread = threading.Thread(target=self._run, daemon=True, name=f"{self.label}-watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _health_ok(self, *, use_tunnel_url: bool = False) -> bool:
        import urllib.request

        if use_tunnel_url or not self._local_port:
            url = f"{self.tunnel_url}/api/health"
        else:
            url = f"http://127.0.0.1:{self._local_port}/api/health"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self._HTTP_TIMEOUT) as resp:  # noqa: S310
                return bool(resp.status == 200)
        except Exception:
            return False

    def _process_running(self, proc: subprocess.Popen[str] | None = None) -> bool:
        current = proc or self.proc
        return current is not None and current.poll() is None

    def _terminate_process(self, proc: subprocess.Popen[str] | None = None) -> None:
        current = proc or self.proc
        if current is None:
            return
        try:
            current.terminate()
            current.wait(timeout=5)
        except Exception:
            with contextlib.suppress(Exception):
                current.kill()

    def _read_process_output(self, proc: subprocess.Popen[str]) -> str:
        if proc.stdout is None:
            return ""
        with contextlib.suppress(Exception):
            return proc.stdout.read(self._MAX_OUTPUT_BYTES).strip()
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

        env = {**__import__("os").environ, **(self.restart_env or {})} if self.restart_env else None

        for attempt in range(1, self._RESTART_ATTEMPTS + 1):
            if attempt > 1:
                backoff = self._BACKOFF_BASE * (2 ** (attempt - 2))
                log.debug("tunnel_watchdog_backoff", provider=self.label, seconds=backoff, attempt=attempt)
                if self._stop_event.wait(timeout=backoff):
                    return True

            self._terminate_process()

            proc = subprocess.Popen(
                self.restart_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            with self._lock:
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
        if self._stop_event.wait(timeout=self._CHECK_INTERVAL):
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
            elif self._health_ok(use_tunnel_url=use_relay):
                consecutive_failures = 0
            else:
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
        with contextlib.suppress(Exception):
            while True:
                chunk = stdout.read(8192)
                if not chunk:
                    break

    threading.Thread(target=_drain, daemon=True, name="tunnel-stdout-drain").start()


def _wait_for_startup(proc: subprocess.Popen[str], *, label: str = "tunnel", timeout: float = 5) -> None:
    """Poll until the tunnel process has survived the startup window or raise on early exit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = ""
            if proc.stdout:
                with contextlib.suppress(Exception):
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
        if shutil.which("devtunnel"):
            return None
        return "ERROR: 'devtunnel' CLI not found.\n  Install: https://aka.ms/devtunnels/cli\n  Or run: cpl setup"

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


def start_remote_access(
    provider: RemoteProvider,
    *,
    port: int,
    cloudflare_token: str | None = None,
    cloudflare_hostname: str | None = None,
    tunnel_name: str | None = None,
) -> TunnelHandle:
    """Start the selected remote access provider."""
    if provider is RemoteProvider.local:
        return TunnelHandle(provider=provider)
    if provider is RemoteProvider.devtunnel:
        origin, proc, resolved_name = _start_devtunnel(port, tunnel_name=tunnel_name)
        handle = TunnelHandle(provider=provider, origin=origin, proc=proc)
        handle.watchdog = TunnelWatchdog(
            tunnel_url=origin,
            restart_command=["devtunnel", "host", resolved_name],
            proc=proc,
            label="devtunnel",
            local_port=port,
        )
        handle.watchdog.start()
        return handle
    origin, proc = _start_cloudflare(port, cloudflare_token=cloudflare_token, cloudflare_hostname=cloudflare_hostname)
    handle = TunnelHandle(provider=provider, origin=origin, proc=proc)
    handle.watchdog = TunnelWatchdog(
        tunnel_url=origin,
        restart_command=["cloudflared", "tunnel", "--no-autoupdate", "run"],
        restart_env={"TUNNEL_TOKEN": cloudflare_token or ""},
        proc=proc,
        label="cloudflare",
        local_port=port,
    )
    handle.watchdog.start()
    return handle


def _run_capture(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


_CODEPLANE_TUNNEL_PREFIX = "cpl-"


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


def _start_devtunnel(port: int, *, tunnel_name: str | None = None) -> tuple[str, subprocess.Popen[str], str]:
    if tunnel_name:
        # Explicit name — use as-is
        exists, region = _lookup_devtunnel(tunnel_name)
    else:
        # Auto mode — reuse an existing codeplane tunnel or generate a random name
        existing = _find_existing_codeplane_tunnel()
        if existing:
            tunnel_name, region = existing
            exists = True
        else:
            tunnel_name = f"{_CODEPLANE_TUNNEL_PREFIX}{secrets.token_hex(4)}"
            exists, region = False, None

    if not exists:
        create_result = _run_capture(["devtunnel", "create", tunnel_name, "--allow-anonymous", "--expiration", "30d"])
        if create_result.returncode != 0:
            raise TunnelStartError(
                create_result.stderr.strip() or create_result.stdout.strip() or "devtunnel create failed"
            )

    _run_capture(["devtunnel", "port", "create", tunnel_name, "-p", str(port), "--protocol", "http"])
    _, region = _lookup_devtunnel(tunnel_name)
    if not region:
        raise TunnelStartError("Could not determine the Dev Tunnel region.")

    proc = subprocess.Popen(
        ["devtunnel", "host", tunnel_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_for_startup(proc, label="devtunnel")
    _start_output_drain(proc)

    tunnel_url = f"https://{tunnel_name}-{port}.{region}.devtunnels.ms"
    log.debug("tunnel_started", provider="devtunnel", url=tunnel_url)
    return tunnel_url, proc, tunnel_name


def _start_cloudflare(
    port: int,
    *,
    cloudflare_token: str | None,
    cloudflare_hostname: str | None,
) -> tuple[str, subprocess.Popen[str]]:
    if not cloudflare_token or not cloudflare_hostname:
        raise TunnelStartError("Cloudflare remote access requires a tunnel token and hostname.")

    hostname = cloudflare_hostname.removeprefix("https://").rstrip("/")
    env = {**__import__("os").environ, "TUNNEL_TOKEN": cloudflare_token}
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--no-autoupdate", "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    _wait_for_startup(proc, label="cloudflare")
    _start_output_drain(proc)

    tunnel_url = f"https://{hostname}"
    log.debug("tunnel_started", provider="cloudflare", url=tunnel_url, port=port)
    return tunnel_url, proc
