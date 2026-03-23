"""CLI entry point for CodePlane (``cpl`` command group).

Contains the Click command group and all sub-commands (up, version, setup,
doctor) along with tunnel management and startup helpers.
"""

from __future__ import annotations

from pathlib import Path

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
@click.option("--skip-preflight", is_flag=True, help="Skip preflight checks")
def up(
    host: str | None,
    port: int | None,
    dev: bool,
    remote: bool,
    provider: str,
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

    remote_provider = RemoteProvider(provider) if remote else RemoteProvider.local

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

    if remote:
        error = validate_remote_provider(
            remote_provider,
            cloudflare_token=cloudflare_token,
            cloudflare_hostname=cloudflare_hostname,
        )
        if error:
            click.secho(error, fg="red", err=True)
            raise SystemExit(1)

    # Password logic: auto-generate for tunnel, allow explicit, block unsafe combos
    if remote and no_password:
        click.secho(
            "ERROR: --remote with --no-password is not allowed. Remote access requires authentication.", fg="red"
        )
        raise SystemExit(1)

    # Password priority: --password flag > CPL_TUNNEL_PASSWORD env/dotenv > auto-generate for tunnel
    effective_password: str | None = password

    if not effective_password and not no_password:
        env_pw = _env("CPL_TUNNEL_PASSWORD")
        if env_pw:
            effective_password = env_pw

    if not effective_password and not no_password and remote:
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
            message="Binding to 0.0.0.0 — no authentication is enforced. Use --remote for authenticated remote access.",
        )
        click.secho(
            "WARNING: Binding to 0.0.0.0 — no authentication is enforced.",
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
            )
        except TunnelStartError as exc:
            click.secho(f"ERROR: {exc}", fg="red", err=True)
            raise SystemExit(1) from exc
        tunnel_origin = tunnel_handle.origin

    app = create_app(dev=dev, tunnel_origin=tunnel_origin, password=effective_password)

    try:
        _print_startup_banner(host, port, dev, tunnel_origin, effective_password)
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
