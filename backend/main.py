"""FastAPI application factory and CLI entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import click
import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


def create_app(*, dev: bool = False, tunnel_origin: str | None = None) -> FastAPI:
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

    app.include_router(health.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(workspace.router, prefix="/api")
    app.include_router(voice.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")

    return app


@click.group()
def cli() -> None:
    """Tower — control tower for coding agents."""


@cli.command()
@click.option("--host", default=None, help="Bind host (default: from config or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Bind port (default: from config or 8080)")
@click.option("--dev", is_flag=True, help="Enable development mode (CORS for localhost:5173)")
@click.option("--tunnel", is_flag=True, help="Start Dev Tunnel for remote access")
def up(host: str | None, port: int | None, dev: bool, tunnel: bool) -> None:
    """Start the Tower server."""
    config = load_config()
    host = host or config.server.host
    port = port or config.server.port

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

    app = create_app(dev=dev, tunnel_origin=tunnel_origin)

    try:
        _print_startup_banner(host, port, dev, tunnel_origin)
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
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Read output lines looking for the tunnel URL
        import re

        tunnel_url: str | None = None
        if proc.stdout:
            for line in proc.stdout:
                match = re.search(r"(https://\S+\.devtunnels\.ms\S*)", line)
                if match:
                    tunnel_url = match.group(1).rstrip("/")
                    break
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


def _print_startup_banner(host: str, port: int, dev: bool, tunnel_url: str | None) -> None:
    """Print a startup banner with server info."""
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        lines = [f"[bold]Server:[/bold] http://{host}:{port}"]
        if dev:
            lines.append("[bold]Mode:[/bold]   Development (CORS enabled)")
        if tunnel_url:
            lines.append(f"[bold]Tunnel:[/bold] {tunnel_url}")
        console.print(Panel("\n".join(lines), title="[bold cyan]Tower[/bold cyan]", border_style="cyan"))
    except ImportError:
        click.echo(f"Tower server: http://{host}:{port}")
        if tunnel_url:
            click.echo(f"Tunnel: {tunnel_url}")


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
