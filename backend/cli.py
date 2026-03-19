"""CLI entry point for CodePlane (``cpl`` command group).

Contains the Click command group and all sub-commands (up, version, setup,
doctor) along with tunnel management and startup helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import structlog
import uvicorn

from backend.app_factory import create_app
from backend.config import load_config
from backend.logging_config import setup_logging
from backend.persistence.database import run_migrations

if TYPE_CHECKING:
    import subprocess
    import threading

log = structlog.get_logger()


@click.group()
def cli() -> None:
    """CodePlane — control plane for coding agents."""


# ---------------------------------------------------------------------------
# Frontend build helper
# ---------------------------------------------------------------------------


def _build_frontend() -> bool:
    """Build the frontend if sources are newer than dist/."""
    import subprocess

    frontend_root = Path(__file__).resolve().parent.parent / "frontend"
    package_json = frontend_root / "package.json"
    if not package_json.exists():
        return False

    dist = frontend_root / "dist" / "index.html"
    src = frontend_root / "src"
    # Skip build if dist is up-to-date
    if dist.exists() and src.exists():
        dist_mtime = dist.stat().st_mtime
        src_mtime = max(f.stat().st_mtime for f in src.rglob("*") if f.is_file())
        if dist_mtime > src_mtime:
            return True

    click.echo("Building frontend...")
    try:
        # Ensure deps are installed
        if not (frontend_root / "node_modules").is_dir():
            subprocess.run(["npm", "ci"], cwd=str(frontend_root), check=True, capture_output=True, timeout=300)
        subprocess.run(["npm", "run", "build"], cwd=str(frontend_root), check=True, capture_output=True, timeout=300)
        click.secho("Frontend built.", fg="green")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        click.secho(f"Frontend build failed: {exc}", fg="yellow")
        click.echo("The API will still work, but there will be no web UI.")
        return False


# ---------------------------------------------------------------------------
# ``cpl up`` — start the server
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--host", default=None, help="Bind host (default: from config or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Bind port (default: from config or 8080)")
@click.option("--dev", is_flag=True, help="Dev mode: skip frontend build")
@click.option("--tunnel", is_flag=True, help="Start Dev Tunnel for remote access")
@click.option("--password", default=None, help="Set auth password (auto-generated if --tunnel without --password)")
@click.option("--no-password", is_flag=True, help="Disable password auth (not allowed with --tunnel)")
@click.option("--skip-preflight", is_flag=True, help="Skip preflight checks")
def up(
    host: str | None,
    port: int | None,
    dev: bool,
    tunnel: bool,
    password: str | None,
    no_password: bool,
    skip_preflight: bool,
) -> None:
    """Start the CodePlane server."""
    config = load_config()
    host = host or config.server.host
    port = port or config.server.port

    # Run preflight checks before starting
    if not skip_preflight:
        from backend.services.setup_service import validate_preflight

        if not validate_preflight(port):
            raise SystemExit(1)

    # Password logic: auto-generate for tunnel, allow explicit, block unsafe combos
    if tunnel and no_password:
        click.secho("ERROR: --tunnel --no-password is not allowed. Remote access requires authentication.", fg="red")
        raise SystemExit(1)

    # Password priority: --password flag > CPL_TUNNEL_PASSWORD env/dotenv > auto-generate for tunnel
    effective_password: str | None = password

    if not effective_password and not no_password:
        import os
        from pathlib import Path

        # .env takes precedence over system env
        env_pw: str | None = None
        dotenv = Path(__file__).resolve().parent.parent / ".env"
        if dotenv.is_file():
            for line in dotenv.read_text().splitlines():
                line = line.strip()
                if line.startswith("CPL_TUNNEL_PASSWORD=") and not line.startswith("#"):
                    env_pw = line.split("=", 1)[1].strip()
                    break
        if not env_pw:
            env_pw = os.environ.get("CPL_TUNNEL_PASSWORD")
        if env_pw:
            effective_password = env_pw

    if not effective_password and not no_password and tunnel:
        from backend.services.auth import generate_password

        effective_password = generate_password()

    # Build frontend (unless --dev, which uses Vite's hot-reload server separately)
    if not dev:
        _build_frontend()

    # Configure logging before everything else so all startup messages are captured
    setup_logging(config.logging.file, console_level=config.logging.level)

    # Run Alembic migrations before starting the server
    run_migrations()

    # Startup warning for 0.0.0.0 binding
    if host == "0.0.0.0":  # noqa: S104
        log.warning(
            "binding_all_interfaces",
            host=host,
            message="Binding to 0.0.0.0 — no authentication is enforced. Use --tunnel for authenticated remote access.",
        )
        click.secho(
            "WARNING: Binding to 0.0.0.0 — no authentication is enforced.",
            fg="yellow",
            err=True,
        )

    tunnel_origin: str | None = None
    tunnel_proc = None
    tunnel_watchdog: _TunnelWatchdog | None = None

    if tunnel:
        tunnel_origin, tunnel_proc = _start_tunnel(port)

    app = create_app(dev=dev, tunnel_origin=tunnel_origin, password=effective_password)

    if tunnel and tunnel_origin and tunnel_proc:
        tunnel_watchdog = _TunnelWatchdog(
            tunnel_url=tunnel_origin,
            tunnel_name=tunnel_origin.split("//")[1].rsplit(f"-{port}.", 1)[0],
            port=port,
            proc=tunnel_proc,
        )
        tunnel_watchdog.start()

    try:
        _print_startup_banner(host, port, dev, tunnel_origin, effective_password)
        uvicorn.run(app, host=host, port=port)
    finally:
        if tunnel_watchdog is not None:
            tunnel_watchdog.stop()
        if tunnel_proc is not None:
            tunnel_proc.terminate()
        # Also terminate the watchdog's proc if it was swapped during a restart
        if tunnel_watchdog is not None and tunnel_watchdog.proc is not tunnel_proc:
            tunnel_watchdog.proc.terminate()


# ---------------------------------------------------------------------------
# Tunnel watchdog — restart devtunnel host when the relay drops
# ---------------------------------------------------------------------------


class _TunnelWatchdog:
    """Background thread that pings the tunnel URL and restarts devtunnel host
    when the relay connection goes stale.

    The devtunnel host process sometimes loses its WebSocket connection to the
    Azure relay but doesn't exit.  This watchdog detects the failure via HTTP
    health checks and kills/restarts the process automatically.
    """

    _CHECK_INTERVAL = 10  # seconds between health checks
    _FAIL_THRESHOLD = 2  # consecutive failures before restart
    _HTTP_TIMEOUT = 5  # seconds per health check request

    def __init__(self, *, tunnel_url: str, tunnel_name: str, port: int, proc: subprocess.Popen[str]) -> None:
        self.tunnel_url = tunnel_url
        self.tunnel_name = tunnel_name
        self.port = port
        self.proc = proc
        self._stop_event: threading.Event = __import__("threading").Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import threading

        self._thread = threading.Thread(target=self._run, daemon=True, name="tunnel-watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _health_ok(self) -> bool:
        """Return True if the tunnel is forwarding traffic."""
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(
                f"{self.tunnel_url}/api/health",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=self._HTTP_TIMEOUT) as resp:
                return bool(resp.status == 200)
        except Exception:
            log.debug("tunnel_health_check_failed", tunnel=self.tunnel_name, exc_info=True)
            return False

    def _restart_host(self) -> None:
        """Kill the current devtunnel host and start a fresh one."""
        import subprocess

        log.debug("tunnel_watchdog_restarting", tunnel=self.tunnel_name)

        # Kill the old process
        import contextlib

        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            log.debug("tunnel_terminate_failed_forcing_kill", tunnel=self.tunnel_name, exc_info=True)
            with contextlib.suppress(Exception):
                self.proc.kill()

        # Start a new host process
        proc = subprocess.Popen(
            ["devtunnel", "host", self.tunnel_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Wait for readiness
        if proc.stdout:
            for line in proc.stdout:
                if "Connect via" in line or "Hosting port" in line:
                    break

        self.proc = proc
        log.debug("tunnel_watchdog_restarted", tunnel=self.tunnel_name)

    def _run(self) -> None:
        # Give the tunnel a grace period to fully initialize
        if self._stop_event.wait(timeout=self._CHECK_INTERVAL):
            return

        consecutive_failures = 0

        while not self._stop_event.is_set():
            if self._health_ok():
                if consecutive_failures > 0:
                    log.debug("tunnel_watchdog_recovered", failures=consecutive_failures)
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                log.debug(
                    "tunnel_watchdog_check_failed",
                    consecutive=consecutive_failures,
                    threshold=self._FAIL_THRESHOLD,
                )
                if consecutive_failures >= self._FAIL_THRESHOLD:
                    self._restart_host()
                    consecutive_failures = 0
                    # Extra grace period after restart
                    if self._stop_event.wait(timeout=self._CHECK_INTERVAL):
                        return

            if self._stop_event.wait(timeout=self._CHECK_INTERVAL):
                return


def _start_tunnel(port: int) -> tuple[str | None, subprocess.Popen[str] | None]:
    """Start a devtunnel with a stable, reusable tunnel name.

    Naming convention: {username}-cpl
    The tunnel is created once and reused on subsequent runs.
    If the name is taken, random padding is appended.
    """
    import json
    import secrets
    import subprocess

    def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, text=True, timeout=30, **kwargs)

    try:
        # Get logged-in username
        user_result = _run(["devtunnel", "user", "show"])
        username = "codeplane"
        for line in user_result.stdout.splitlines():
            if "Logged in as" in line:
                # "Logged in as dfinson using GitHub."
                parts = line.split()
                idx = parts.index("as") + 1 if "as" in parts else -1
                if idx > 0 and idx < len(parts):
                    username = parts[idx]
                break

        tunnel_name = f"{username}-codeplane"

        # Check if tunnel already exists
        list_result = _run(["devtunnel", "list", "--json"])
        existing_tunnels: list[str] = []
        tunnel_region = "euw"  # default
        try:
            data = json.loads(list_result.stdout)
            for t in data.get("tunnels", []):
                tid = t.get("tunnelId", "")
                existing_tunnels.append(tid.split(".")[0])
                # Extract region from existing tunnel (e.g. "dfinson-cpl.euw")
                if tid.startswith(tunnel_name) and "." in tid:
                    tunnel_region = tid.split(".")[1]
        except (json.JSONDecodeError, KeyError):
            log.warning(
                "tunnel_list_parse_failed",
                stdout=list_result.stdout[:500] if list_result.stdout else None,
                exc_info=True,
            )

        if tunnel_name not in existing_tunnels:
            # Create the tunnel
            create_result = _run(
                [
                    "devtunnel",
                    "create",
                    tunnel_name,
                    "--allow-anonymous",
                    "--expiration",
                    "30d",
                ]
            )
            if create_result.returncode != 0:
                # Name might be taken by another user — add random padding
                tunnel_name = f"{username}-codeplane-{secrets.token_hex(2)}"
                _run(
                    [
                        "devtunnel",
                        "create",
                        tunnel_name,
                        "--allow-anonymous",
                        "--expiration",
                        "30d",
                    ]
                )

            # Add port
            _run(
                [
                    "devtunnel",
                    "port",
                    "create",
                    tunnel_name,
                    "-p",
                    str(port),
                    "--protocol",
                    "http",
                ]
            )
            log.debug("tunnel_created", name=tunnel_name)
        else:
            log.debug("tunnel_reused", name=tunnel_name)

        # Host the tunnel
        proc = subprocess.Popen(
            ["devtunnel", "host", tunnel_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Construct the stable URL from the tunnel name
        tunnel_url = f"https://{tunnel_name}-{port}.{tunnel_region}.devtunnels.ms"

        # Wait for the tunnel to actually be ready (check stdout for "Connect via")
        if proc.stdout:
            for line in proc.stdout:
                if "Connect via" in line or "Hosting port" in line:
                    break

        log.debug("tunnel_started", url=tunnel_url, name=tunnel_name)
        return tunnel_url, proc
    except FileNotFoundError:
        click.secho(
            "ERROR: 'devtunnel' CLI not found. Install from https://aka.ms/devtunnels/cli",
            fg="red",
            err=True,
        )
        return None, None
    except subprocess.TimeoutExpired:
        log.warning("tunnel_setup_timeout")
        return None, None


# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------


def _print_startup_banner(host: str, port: int, dev: bool, tunnel_url: str | None, password: str | None = None) -> None:
    """Print a startup banner with server info."""
    url = tunnel_url or f"http://{host}:{port}"

    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        lines = [f"[bold]Server:[/bold] http://{host}:{port}"]
        if dev:
            lines.append("[bold]Mode:[/bold]   Development (CORS enabled)")
        if tunnel_url:
            lines.append(f"[bold]Tunnel:[/bold] {tunnel_url}")
        if password:
            lines.append(f"[bold]Password:[/bold] {password}")
        console.print(Panel("\n".join(lines), title="[bold cyan]CodePlane[/bold cyan]", border_style="cyan"))
    except ImportError:
        click.echo(f"CodePlane server: http://{host}:{port}")
        if tunnel_url:
            click.echo(f"Tunnel: {tunnel_url}")
        if password:
            click.echo(f"Password: {password}")

    # Print QR code for the access URL
    try:
        import qrcode

        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        click.echo()
        qr.print_ascii(invert=True)
        click.echo(f"\n  Scan to open: {url}\n")
    except ImportError:
        log.debug("qrcode_not_installed", package="qrcode", exc_info=True)


# ---------------------------------------------------------------------------
# Utility commands
# ---------------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Print CodePlane version."""
    click.echo("cpl 0.1.0")


@cli.command()
def setup() -> None:
    """Interactive setup wizard — check dependencies, configure data directory, authenticate."""
    from backend.services.setup_service import execute_setup_wizard

    execute_setup_wizard()


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def doctor(as_json: bool) -> None:
    """Full non-interactive health check — deps, auth, SDK, environment."""
    from backend.services.setup_service import diagnose_configuration

    ok = diagnose_configuration(as_json=as_json)
    if not ok:
        raise SystemExit(1)
