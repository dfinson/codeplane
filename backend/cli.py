"""CLI entry point for CodePlane (``cpl`` command group).

Contains the Click command group and all sub-commands (up, version, setup,
doctor, down, restart) along with tunnel management and startup helpers.
"""

from __future__ import annotations

import contextlib
import signal
from pathlib import Path
from typing import Any

import click
import structlog
import uvicorn

from backend.app_factory import create_app
from backend.config import load_config
from backend.logging_config import setup_logging
from backend.persistence.database import run_migrations
from backend.services.tunnel_service import (
    RemoteProvider,
    TunnelHandle,
    TunnelStartError,
    start_remote_access,
    validate_remote_provider,
)

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
@click.option("--remote", is_flag=True, help="Enable remote access via a tunnel provider")
@click.option(
    "--provider",
    default="devtunnel",
    type=click.Choice(["devtunnel", "cloudflare"], case_sensitive=False),
    show_default=True,
    help="Remote access provider (requires --remote)",
)
@click.option("--password", default=None, help="Set auth password (auto-generated with --remote)")
@click.option("--no-password", is_flag=True, help="Disable password auth (not allowed with --remote)")
@click.option("--tunnel-name", default=None, help="Dev Tunnel name (default: random, reused across restarts)")
@click.option("--skip-preflight", is_flag=True, help="Skip preflight checks")
def up(
    host: str | None,
    port: int | None,
    dev: bool,
    remote: bool,
    provider: str,
    password: str | None,
    no_password: bool,
    tunnel_name: str | None,
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

    remote_provider = RemoteProvider(provider) if remote else RemoteProvider.local

    if not remote and provider != "devtunnel":
        click.secho(
            f"ERROR: --provider requires --remote (got --provider {provider} without --remote).",
            fg="red",
            err=True,
        )
        raise SystemExit(1)

    # Read credentials from .env (takes precedence) then OS environment
    import os

    dotenv_path = Path(__file__).resolve().parent.parent / ".env"
    dotenv_vars: dict[str, str] = {}
    if dotenv_path.is_file():
        for line in dotenv_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                dotenv_vars[k.strip()] = v.strip()

    def _env(key: str) -> str | None:
        return dotenv_vars.get(key) or os.environ.get(key) or None

    cloudflare_token = _env("CPL_CLOUDFLARE_TUNNEL_TOKEN")
    cloudflare_hostname = _env("CPL_CLOUDFLARE_HOSTNAME")
    tunnel_name = tunnel_name or _env("CPL_DEVTUNNEL_NAME")

    # Password logic: block unsafe combos before checking provider availability
    if remote and no_password:
        click.secho(
            "ERROR: --remote with --no-password is not allowed. Remote access requires authentication.", fg="red"
        )
        raise SystemExit(1)

    if remote:
        error = validate_remote_provider(
            remote_provider,
            cloudflare_token=cloudflare_token,
            cloudflare_hostname=cloudflare_hostname,
        )
        if error:
            click.secho(error, fg="red", err=True)
            raise SystemExit(1)

    # Password priority: --password flag > CPL_DEVTUNNEL_PASSWORD env/dotenv > auto-generate for tunnel
    effective_password: str | None = password

    if not effective_password and not no_password:
        env_pw = _env("CPL_DEVTUNNEL_PASSWORD")
        if env_pw:
            effective_password = env_pw

    if not effective_password and not no_password and remote:
        from backend.services.auth import generate_password

        effective_password = generate_password()

    # Block unauthenticated binding on all interfaces — validate before migrations
    if host == "0.0.0.0" and no_password:  # noqa: S104
        click.secho(
            "ERROR: --host 0.0.0.0 with --no-password is not allowed. "
            "Binding to all interfaces requires authentication.",
            fg="red",
            err=True,
        )
        raise SystemExit(1)

    # Build frontend (unless --dev, which uses Vite's hot-reload server separately)
    if not dev:
        _build_frontend()

    # Configure logging before everything else so all startup messages are captured
    setup_logging(config.logging.file, console_level=config.logging.level)

    # Run Alembic migrations before starting the server
    run_migrations()

    # Auto-generate password when binding to all interfaces without one set
    if host == "0.0.0.0" and not effective_password:  # noqa: S104
        from backend.services.auth import generate_password as _gen_pw

        effective_password = _gen_pw()
        click.secho(
            "WARNING: Binding to 0.0.0.0 — password auth auto-enabled.",
            fg="yellow",
            err=True,
        )

    tunnel_origin: str | None = None
    tunnel_handle: TunnelHandle | None = None

    if remote:
        try:
            tunnel_handle = start_remote_access(
                remote_provider,
                port=port,
                cloudflare_token=cloudflare_token,
                cloudflare_hostname=cloudflare_hostname,
                tunnel_name=tunnel_name,
            )
        except TunnelStartError as exc:
            click.secho(f"ERROR: {exc}", fg="red", err=True)
            raise SystemExit(1) from exc
        tunnel_origin = tunnel_handle.origin

    app = create_app(dev=dev, tunnel_origin=tunnel_origin, password=effective_password)

    # Stash banner info so lifespan can print it after services are ready
    app.state.banner_args = {
        "host": host,
        "port": port,
        "dev": dev,
        "tunnel_url": tunnel_origin,
        "password": effective_password,
    }

    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        if tunnel_handle is not None:
            tunnel_handle.close()


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
    from backend import __version__

    click.echo(f"cpl {__version__}")


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


# ---------------------------------------------------------------------------
# ``cpl down`` — gracefully stop the server
# ---------------------------------------------------------------------------


def _find_pids_on_port(port: int) -> list[int]:
    """Return PIDs of processes listening on the given TCP port."""
    import subprocess as _sp

    # Try lsof first (most POSIX systems)
    try:
        result = _sp.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return [int(p) for p in result.stdout.strip().splitlines() if p.strip().isdigit()]
    except FileNotFoundError:
        pass

    # Fallback: ss (Linux)
    try:
        import re

        result = _sp.run(["ss", "-tlnp", f"sport = :{port}"], capture_output=True, text=True)
        return [int(p) for p in re.findall(r"pid=(\d+)", result.stdout)]
    except Exception:  # noqa: BLE001
        pass

    return []


def _is_server_running(host: str, port: int) -> tuple[bool, list[int]]:
    """Detect a running CodePlane instance.

    Uses the same layered strategy as ``cpl doctor``:
    1. /health endpoint (definitive)
    2. Process scan for ``cpl up`` / ``cpl restart`` commands
    3. Port-level PID detection

    Returns (running, pids).  *pids* may be empty when detection succeeded
    via health-probe alone — callers that need PIDs should fall back to
    ``_find_pids_on_port``.
    """
    from backend.services.setup_service import _find_cpl_processes

    # 1. Health endpoint
    status, _ = _api_get(f"http://{host}:{port}", "/health")
    if status == 200:
        pids = _find_pids_on_port(port)
        return True, pids

    # 2. Process scan (cross-platform)
    pids = _find_cpl_processes()
    if pids:
        return True, pids

    # 3. Port-level detection
    pids = _find_pids_on_port(port)
    if pids:
        return True, pids

    return False, []


def _api_get(base_url: str, path: str) -> tuple[int, dict[str, Any] | None]:
    """Perform a GET request. Returns (status, body | None)."""
    import json
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    req = Request(f"{base_url}{path}", method="GET")
    try:
        with urlopen(req, timeout=5) as resp:  # noqa: S310
            raw = resp.read()
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, None
    except (URLError, OSError):
        return 0, None


def _api_post(base_url: str, path: str) -> int:
    """Perform a POST request with no body. Returns the status code (0 on error)."""
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    req = Request(f"{base_url}{path}", method="POST", data=b"", headers={"Content-Length": "0"})
    try:
        with urlopen(req, timeout=5) as resp:  # noqa: S310
            return int(resp.status)
    except (URLError, OSError):
        return 0


def _pause_active_sessions(base_url: str) -> None:
    """Fire pause signals to all running agent sessions via the API."""
    # Collect running jobs (paginated)
    running: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        path = "/api/jobs?state=running&limit=100"
        if cursor:
            path += f"&cursor={cursor}"
        status, body = _api_get(base_url, path)
        if status != 200 or not body:
            break
        running.extend(body.get("items", []))
        if not body.get("hasMore"):
            break
        cursor = body.get("cursor")

    if not running:
        click.echo("  No running sessions to pause.")
        return

    click.echo(f"  Pausing {len(running)} running session(s)…")
    for job in running:
        ok = _api_post(base_url, f"/api/jobs/{job['id']}/pause") == 204
        mark = "✓" if ok else "✗"
        title = job.get("title") or "(untitled)"
        click.echo(f"    {mark}  {job['id'][:8]}… {title}")


def _stop_server(port: int) -> bool:
    """Send SIGTERM and wait for the server to exit. Returns True when stopped."""
    import os
    import time

    pids = _find_pids_on_port(port)
    if not pids:
        # Process scan may have found PIDs that aren't on the port yet (startup race)
        from backend.services.setup_service import _find_cpl_processes

        pids = _find_cpl_processes()

    if not pids:
        click.echo("  No process found — already stopped.")
        return True

    click.echo(f"  Sending SIGTERM to PID(s) {pids}…")
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)

    while _find_pids_on_port(port):
        time.sleep(0.5)

    click.secho("  Server stopped.", fg="green")
    return True


@cli.command()
@click.option("--host", default=None, help="Server host (default: from config or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Server port (default: from config or 8080)")
@click.option("--force", is_flag=True, help="Skip session pausing; stop immediately")
def down(host: str | None, port: int | None, force: bool) -> None:
    """Gracefully pause all active sessions and shut down the server."""
    config = load_config()
    host = host or config.server.host
    port = port or config.server.port
    base_url = f"http://{host}:{port}"

    running, _ = _is_server_running(host, port)
    if not running:
        click.echo("CodePlane is not running.")
        return

    # Pause sessions unless --force
    if not force:
        click.echo("Pausing active sessions…")
        _pause_active_sessions(base_url)
    else:
        click.echo("Skipping session pause (--force).")

    click.echo(f"Stopping CodePlane on port {port}…")
    if not _stop_server(port):
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# ``cpl restart`` — down (if running) then up
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--host", default=None, help="Bind host (default: from config or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Bind port (default: from config or 8080)")
@click.option("--dev", is_flag=True, help="Dev mode: skip frontend build")
@click.option("--remote", is_flag=True, help="Enable remote access via a tunnel provider")
@click.option(
    "--provider",
    default="devtunnel",
    type=click.Choice(["devtunnel", "cloudflare"], case_sensitive=False),
    show_default=True,
    help="Remote access provider (requires --remote)",
)
@click.option("--password", default=None, help="Set auth password (auto-generated with --remote)")
@click.option("--no-password", is_flag=True, help="Disable password auth (not allowed with --remote)")
@click.option("--tunnel-name", default=None, help="Dev Tunnel name (default: random, reused across restarts)")
@click.option("--skip-preflight", is_flag=True, help="Skip preflight checks")
@click.option("--force", is_flag=True, help="Skip session pausing on shutdown")
def restart(
    host: str | None,
    port: int | None,
    dev: bool,
    remote: bool,
    provider: str,
    password: str | None,
    no_password: bool,
    tunnel_name: str | None,
    skip_preflight: bool,
    force: bool,
) -> None:
    """Stop a running instance (if any) then start the server.

    Active agent sessions are paused before shutdown and will be recovered
    automatically on startup.
    """
    import sys

    config = load_config()
    host = host or config.server.host
    port = port or config.server.port
    base_url = f"http://{host}:{port}"

    # --- Down phase ---
    running, _ = _is_server_running(host, port)
    if running:
        click.echo("Stopping running instance…")
        if not force:
            _pause_active_sessions(base_url)
        if not _stop_server(port):
            click.secho("Failed to stop existing instance.", fg="red")
            raise SystemExit(1)
    else:
        click.echo("No running instance found — starting fresh.")

    # --- Up phase (exec into ``cpl up`` so it owns the terminal) ---
    args = [sys.executable, "-m", "backend.cli", "up", "--host", host, "--port", str(port)]
    if dev:
        args.append("--dev")
    if remote:
        args.extend(["--remote", "--provider", provider])
    if password:
        args.extend(["--password", password])
    if no_password:
        args.append("--no-password")
    if tunnel_name:
        args.extend(["--tunnel-name", tunnel_name])
    if skip_preflight:
        args.append("--skip-preflight")

    click.echo("Starting CodePlane…")
    import os

    os.execv(sys.executable, args)
