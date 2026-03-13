"""FastAPI application factory and CLI entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api import approvals, artifacts, events, health, jobs, settings, voice, workspace
from backend.config import init_config, load_config
from backend.persistence.database import create_engine, create_session_factory, run_migrations
from backend.persistence.event_repo import EventRepository
from backend.services.agent_adapter import CopilotAdapter
from backend.services.approval_service import ApprovalService
from backend.services.event_bus import EventBus
from backend.services.retention_service import RetentionService
from backend.services.runtime_service import RuntimeService
from backend.services.sse_manager import SSEManager
from backend.services.voice_service import VoiceService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.models.events import DomainEvent

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

log = structlog.get_logger()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage engine lifecycle — create on startup, dispose on shutdown."""
    engine = create_engine()
    session_factory = create_session_factory(engine)

    # --- Event infrastructure ---
    event_bus = EventBus()
    sse_manager = SSEManager()
    # TODO(Phase 5+): wire sse_manager.set_active_job_count() from
    # JobService state-transition callbacks so selective streaming
    # activates when >20 jobs are running concurrently.

    # Persist-then-broadcast subscriber: ensures event.db_id is set
    # (monotonic autoincrement) before SSE frames are built.
    async def _persist_and_broadcast(event: DomainEvent) -> None:
        async with session_factory() as session:
            repo = EventRepository(session)
            await repo.append(event)
            await session.commit()
        await sse_manager.handle_event(event)

    event_bus.subscribe(_persist_and_broadcast)

    # --- Runtime service ---
    config = load_config()
    adapter = CopilotAdapter()
    approval_service = ApprovalService(session_factory=session_factory)

    runtime_service = RuntimeService(
        session_factory=session_factory,
        event_bus=event_bus,
        adapter=adapter,
        config=config,
        approval_service=approval_service,
    )

    # Recover orphaned jobs from a previous crash
    await runtime_service.recover_on_startup()

    # Store on app.state for access from route handlers
    app.state.event_bus = event_bus
    app.state.sse_manager = sse_manager
    app.state.runtime_service = runtime_service
    app.state.approval_service = approval_service

    # --- Voice service ---
    voice_service: VoiceService | None = None
    if config.voice.enabled:
        voice_service = VoiceService(model_name=config.voice.model)
    app.state.voice_service = voice_service
    app.state.voice_max_bytes = config.voice.max_audio_size_mb * 1024 * 1024

    # --- Retention service ---
    retention_service = RetentionService(
        session_factory=session_factory,
        config=config,
    )
    app.state.retention_service = retention_service

    if config.retention.cleanup_on_startup:
        await retention_service.run_cleanup()

    # Start daily retention background task
    retention_task = asyncio.create_task(
        retention_service.daily_loop(),
        name="retention-daily",
    )

    # Session factory available for route handlers that need ad-hoc sessions
    app.state.session_factory = session_factory

    # --- MCP server ---
    mcp_cleanup = None
    if config.mcp_server.enabled:
        from backend.mcp.server import create_mcp_server

        mcp_server = create_mcp_server(
            session_factory=session_factory,
            runtime_service=runtime_service,
            approval_service=approval_service,
        )
        app.state.mcp_server = mcp_server
        mcp_app = mcp_server.streamable_http_app()
        app.mount(config.mcp_server.path, mcp_app)
        # Manually start the session manager's task group (sub-app lifespan
        # doesn't fire when mounted during the parent's lifespan).
        mcp_ctx = mcp_server.session_manager.run()
        await mcp_ctx.__aenter__()
        mcp_cleanup = mcp_ctx
        log.info("mcp_server_mounted", path=config.mcp_server.path)

    async def _session_dep() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[jobs._get_session] = _session_dep
    yield
    if mcp_cleanup is not None:
        await mcp_cleanup.__aexit__(None, None, None)
    retention_task.cancel()
    await runtime_service.shutdown()
    await sse_manager.close_all()
    app.dependency_overrides.clear()
    await engine.dispose()


def create_app(*, dev: bool = False, tunnel_origin: str | None = None, password: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Tower", version="0.1.0", lifespan=_lifespan)

    origins: list[str] = []
    if dev:
        origins.append("http://localhost:5173")
    if tunnel_origin:
        origins.append(tunnel_origin)

    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Password auth — enabled when password is provided (tunnel mode or explicit)
    if password:
        from backend.services.auth import auth_middleware, handle_login, set_password

        set_password(password)

        from starlette.routing import Route

        app.routes.insert(0, Route("/api/auth/login", handle_login, methods=["POST"]))

        @app.middleware("http")
        async def _auth_gate(request: Any, call_next: Any) -> Any:
            return await auth_middleware(request, call_next)

    app.include_router(health.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(workspace.router, prefix="/api")
    app.include_router(voice.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")

    # Serve frontend static files (SPA fallback for client-side routing)
    if _FRONTEND_DIR.is_dir():
        from starlette.responses import FileResponse

        _index_html = str(_FRONTEND_DIR / "index.html")

        @app.middleware("http")
        async def _spa_fallback(request: Any, call_next: Any) -> Any:
            path = request.url.path
            # Only intercept clean browser-navigation paths (no API/MCP, no traversal)
            if (
                request.method in ("GET", "HEAD")
                and not path.startswith(("/api", "/mcp"))
                and "\x00" not in path
                and ".." not in path
            ):
                response = await call_next(request)
                if response.status_code == 404:
                    return FileResponse(_index_html)
                return response
            return await call_next(request)

        app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR / "assets")), name="static-assets")

    return app


@click.group()
def cli() -> None:
    """Tower — control tower for coding agents."""


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
            subprocess.run(["npm", "ci"], cwd=str(frontend_root), check=True, capture_output=True)
        subprocess.run(["npm", "run", "build"], cwd=str(frontend_root), check=True, capture_output=True)
        click.secho("Frontend built.", fg="green")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        click.secho(f"Frontend build failed: {exc}", fg="yellow")
        click.echo("The API will still work, but there will be no web UI.")
        return False


@cli.command()
@click.option("--host", default=None, help="Bind host (default: from config or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Bind port (default: from config or 8080)")
@click.option("--dev", is_flag=True, help="Dev mode: skip frontend build, enable CORS for Vite (localhost:5173)")
@click.option("--tunnel", is_flag=True, help="Start Dev Tunnel for remote access")
@click.option("--password", default=None, help="Set auth password (auto-generated if --tunnel without --password)")
@click.option("--no-password", is_flag=True, help="Disable password auth (not allowed with --tunnel)")
def up(host: str | None, port: int | None, dev: bool, tunnel: bool, password: str | None, no_password: bool) -> None:
    """Start the Tower server."""
    config = load_config()
    host = host or config.server.host
    port = port or config.server.port

    # Password logic: auto-generate for tunnel, allow explicit, block unsafe combos
    if tunnel and no_password:
        click.secho("ERROR: --tunnel --no-password is not allowed. Remote access requires authentication.", fg="red")
        raise SystemExit(1)

    effective_password: str | None = password
    if not no_password and tunnel and not password:
        from backend.services.auth import generate_password

        effective_password = generate_password()
    if not no_password and password:
        effective_password = password

    # Also check env var
    if not effective_password and not no_password:
        import os

        env_pw = os.environ.get("TOWER_PASSWORD")
        if env_pw:
            effective_password = env_pw

    # Build frontend (unless --dev, which uses Vite's hot-reload server separately)
    if not dev:
        _build_frontend()

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

    if tunnel:
        tunnel_origin, tunnel_proc = _start_tunnel(port)

    app = create_app(dev=dev, tunnel_origin=tunnel_origin, password=effective_password)

    try:
        _print_startup_banner(host, port, dev, tunnel_origin, effective_password)
        uvicorn.run(app, host=host, port=port)
    finally:
        if tunnel_proc is not None:
            tunnel_proc.terminate()


def _start_tunnel(port: int) -> tuple[str | None, Any]:
    """Start a devtunnel and return (origin_url, process)."""
    import subprocess

    try:
        proc = subprocess.Popen(
            [
                "devtunnel",
                "host",
                "--port-numbers",
                str(port),
                "--allow-anonymous",
                "--protocol",
                "http",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Read output lines looking for the tunnel URL.
        # devtunnel prints two URLs: "https://ID.euw.devtunnels.ms:PORT" and
        # "https://ID-PORT.euw.devtunnels.ms". We want the second form
        # (port embedded in subdomain) since it works on standard HTTPS port 443.
        import re

        tunnel_url: str | None = None
        if proc.stdout:
            for line in proc.stdout:
                # Prefer the port-in-subdomain URL (e.g. https://abc-8080.euw.devtunnels.ms)
                match = re.search(rf"(https://\S+-{port}\.(?:euw|use|usw|ase|jpe)\.devtunnels\.ms)", line)
                if match:
                    tunnel_url = match.group(1).rstrip("/,")
                    break
                # Fallback: any devtunnels URL
                fallback = re.search(r"(https://\S+\.devtunnels\.ms\S*)", line)
                if fallback:
                    tunnel_url = fallback.group(1).rstrip("/,: ")
                    # Keep reading in case the port-subdomain URL comes next
        if tunnel_url:
            log.info("tunnel_started", url=tunnel_url)
        else:
            log.warning("tunnel_url_not_detected")
        return tunnel_url, proc
    except FileNotFoundError:
        click.secho(
            "ERROR: 'devtunnel' CLI not found. Install from https://aka.ms/devtunnels/cli",
            fg="red",
            err=True,
        )
        return None, None


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
        console.print(Panel("\n".join(lines), title="[bold cyan]Tower[/bold cyan]", border_style="cyan"))
    except ImportError:
        click.echo(f"Tower server: http://{host}:{port}")
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
        pass


@cli.command()
def init() -> None:
    """Create default configuration at ~/.tower/config.yaml."""
    import backend.config as _cfg

    if _cfg.DEFAULT_CONFIG_PATH.exists():
        click.echo(f"Configuration already exists at {_cfg.DEFAULT_CONFIG_PATH}")
        click.echo("Delete it first if you want to regenerate defaults.")
        return
    path = init_config()
    click.echo(f"Created default configuration at {path}")


@cli.command()
def version() -> None:
    """Print Tower version."""
    click.echo("tower 0.1.0")


@cli.command()
def setup() -> None:
    """Interactive setup wizard — check dependencies, configure data directory, authenticate."""
    from backend.services.setup_service import run_setup

    run_setup()


@cli.command()
def doctor() -> None:
    """Quick dependency check (non-interactive)."""
    from backend.services.setup_service import preflight_check

    click.echo("Checking dependencies...")
    ok = preflight_check(verbose=True)
    if ok:
        click.secho("\nAll required dependencies are present.", fg="green")
    else:
        click.secho("\nSome required dependencies are missing. Run 'tower setup' to install.", fg="red")
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
