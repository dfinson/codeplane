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
        if self.proc is not None:
            self.proc.terminate()
        if self.watchdog is not None and self.watchdog.proc is not self.proc:
            self.watchdog.proc.terminate()


class TunnelWatchdog:
    """Restart a tunnel host process when the remote relay stops forwarding."""

    _CHECK_INTERVAL = 10
    _FAIL_THRESHOLD = 2
    _HTTP_TIMEOUT = 5
    _RESTART_ATTEMPTS = 3
    _RESTART_GRACE_PERIOD = 2
    _RECOVERY_TIMEOUT = 15

    def __init__(self, *, tunnel_url: str, restart_command: list[str], proc: subprocess.Popen[str], label: str) -> None:
        self.tunnel_url = tunnel_url
        self.restart_command = restart_command
        self.proc = proc
        self.label = label
        self._stop_event = __import__("threading").Event()
        self._thread: Any = None

    def start(self) -> None:
        import threading

        self._thread = threading.Thread(target=self._run, daemon=True, name=f"{self.label}-watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _health_ok(self) -> bool:
        import urllib.request

        try:
            req = urllib.request.Request(f"{self.tunnel_url}/api/health", method="GET")
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
            return proc.stdout.read().strip()
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

        for attempt in range(1, self._RESTART_ATTEMPTS + 1):
            self._terminate_process()

            proc = subprocess.Popen(
                self.restart_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
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

        while not self._stop_event.is_set():
            if not self._process_running():
                log.warning("tunnel_watchdog_process_exited", provider=self.label)
                self._restart_process()
                consecutive_failures = 0
            elif self._health_ok():
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
                    self._restart_process()
                    consecutive_failures = 0
                    if self._stop_event.wait(timeout=self._CHECK_INTERVAL):
                        return

            if self._stop_event.wait(timeout=self._CHECK_INTERVAL):
                return


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
) -> TunnelHandle:
    """Start the selected remote access provider."""
    if provider is RemoteProvider.local:
        return TunnelHandle(provider=provider)
    if provider is RemoteProvider.devtunnel:
        origin, proc, tunnel_name = _start_devtunnel(port)
        handle = TunnelHandle(provider=provider, origin=origin, proc=proc)
        handle.watchdog = TunnelWatchdog(
            tunnel_url=origin,
            restart_command=["devtunnel", "host", tunnel_name],
            proc=proc,
            label="devtunnel",
        )
        handle.watchdog.start()
        return handle
    origin, proc = _start_cloudflare(port, cloudflare_token=cloudflare_token, cloudflare_hostname=cloudflare_hostname)
    handle = TunnelHandle(provider=provider, origin=origin, proc=proc)
    handle.watchdog = TunnelWatchdog(
        tunnel_url=origin,
        restart_command=[
            "cloudflared",
            "tunnel",
            "--no-autoupdate",
            "run",
            "--token",
            cloudflare_token or "",
        ],
        proc=proc,
        label="cloudflare",
    )
    handle.watchdog.start()
    return handle


def _run_capture(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def _parse_devtunnel_name() -> str:
    user_result = _run_capture(["devtunnel", "user", "show"])
    username = "codeplane"
    for line in user_result.stdout.splitlines():
        if "Logged in as" not in line:
            continue
        parts = line.split()
        if "as" in parts:
            idx = parts.index("as") + 1
            if idx < len(parts):
                username = parts[idx]
        break
    return f"{username}-codeplane"


def _lookup_devtunnel(tunnel_name: str) -> tuple[bool, str | None]:
    list_result = _run_capture(["devtunnel", "list", "--json"])
    if list_result.returncode != 0:
        return False, None

    try:
        data = json.loads(list_result.stdout)
    except json.JSONDecodeError:
        return False, None

    for tunnel in data.get("tunnels", []):
        tunnel_id = tunnel.get("tunnelId", "")
        if not tunnel_id:
            continue
        name, _, region = tunnel_id.partition(".")
        if name == tunnel_name:
            return True, region or None
    return False, None


def _start_devtunnel(port: int) -> tuple[str, subprocess.Popen[str], str]:
    tunnel_name = _parse_devtunnel_name()
    exists, region = _lookup_devtunnel(tunnel_name)

    if not exists:
        create_result = _run_capture(["devtunnel", "create", tunnel_name, "--allow-anonymous", "--expiration", "30d"])
        if create_result.returncode != 0:
            tunnel_name = f"{tunnel_name}-{secrets.token_hex(2)}"
            create_retry = _run_capture(
                ["devtunnel", "create", tunnel_name, "--allow-anonymous", "--expiration", "30d"]
            )
            if create_retry.returncode != 0:
                raise TunnelStartError(
                    create_retry.stderr.strip() or create_retry.stdout.strip() or "devtunnel create failed"
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
    time.sleep(2)
    if proc.poll() is not None:
        output = proc.stdout.read().strip() if proc.stdout else ""
        raise TunnelStartError(output or "devtunnel host exited immediately")

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
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--no-autoupdate", "run", "--token", cloudflare_token],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(2)
    if proc.poll() is not None:
        output = proc.stdout.read().strip() if proc.stdout else ""
        raise TunnelStartError(output or "cloudflared tunnel exited immediately")

    tunnel_url = f"https://{hostname}"
    log.debug("tunnel_started", provider="cloudflare", url=tunnel_url, port=port)
    return tunnel_url, proc
