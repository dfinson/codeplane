"""FastAPI application factory and CLI entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import click
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
from backend.services.runtime_service import RuntimeService
from backend.services.sse_manager import SSEManager
from backend.services.voice_service import VoiceService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.models.events import DomainEvent


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

    # Session factory available for route handlers that need ad-hoc sessions
    app.state.session_factory = session_factory

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
    await runtime_service.shutdown()
    await sse_manager.close_all()
    app.dependency_overrides.clear()
    await engine.dispose()


def create_app(*, dev: bool = False) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Tower", version="0.1.0", lifespan=_lifespan)

    if dev:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
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

    app = create_app(dev=dev)
    uvicorn.run(app, host=host, port=port)


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


if __name__ == "__main__":
    cli()
