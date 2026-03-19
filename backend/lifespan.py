"""Application lifespan — startup and shutdown management for CodePlane.

Handles database initialisation, service wiring, background tasks, and
graceful shutdown.  Extracted from main.py to keep concerns separated.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from backend.config import MCP_PATH, VOICE_MAX_AUDIO_SIZE_MB, load_config
from backend.persistence.database import create_engine, create_session_factory
from backend.persistence.event_repo import EventRepository
from backend.services.adapter_registry import AdapterRegistry
from backend.services.approval_service import ApprovalService
from backend.services.diff_service import DiffService
from backend.services.event_bus import EventBus
from backend.services.git_service import GitService
from backend.services.merge_service import MergeService
from backend.services.platform_adapter import PlatformRegistry
from backend.services.retention_service import RetentionService
from backend.services.runtime_service import RuntimeService
from backend.services.sse_manager import SSEManager
from backend.services.summarization_service import SummarizationService
from backend.services.utility_session import UtilitySessionService
from backend.services.voice_service import VoiceService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.models.events import DomainEvent

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Helper dataclass to bundle core services for easy passing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CoreServices:
    approval_service: ApprovalService
    adapter_registry: AdapterRegistry
    platform_registry: PlatformRegistry
    merge_service: MergeService
    utility_session: UtilitySessionService
    runtime_service: RuntimeService


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------


def _init_event_infrastructure(
    session_factory: Any,
) -> tuple[EventBus, SSEManager]:
    """Create event bus and SSE manager with persist-then-broadcast wiring."""
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
        await sse_manager.broadcast_domain_event(event)

    event_bus.subscribe(_persist_and_broadcast)
    return event_bus, sse_manager


async def _wire_core_services(
    session_factory: Any,
    event_bus: EventBus,
    config: Any,
) -> _CoreServices:
    """Instantiate and wire together the core application services."""
    approval_service = ApprovalService(session_factory=session_factory)
    adapter_registry = AdapterRegistry(
        approval_service=approval_service,
        event_bus=event_bus,
    )
    git_service = GitService(config)
    diff_service = DiffService(git_service=git_service, event_bus=event_bus)
    platform_registry = PlatformRegistry(platform_configs=config.platforms)
    merge_service = MergeService(
        git_service=git_service,
        event_bus=event_bus,
        session_factory=session_factory,
        config=config.completion,
        platform_registry=platform_registry,
        diff_service=diff_service,
    )

    # --- Utility session pool (warm cheap model for naming / summaries) ---
    utility_session = UtilitySessionService(
        model=config.runtime.utility_model,
        max_pool_fn=lambda: config.runtime.max_concurrent_jobs,
    )
    log.debug("utility_session_starting", model=config.runtime.utility_model)
    await utility_session.start()

    summarization_service = SummarizationService(
        session_factory=session_factory,
        adapter=utility_session,
    )

    runtime_service = RuntimeService(
        session_factory=session_factory,
        event_bus=event_bus,
        adapter_registry=adapter_registry,
        config=config,
        approval_service=approval_service,
        diff_service=diff_service,
        merge_service=merge_service,
        summarization_service=summarization_service,
        platform_registry=platform_registry,
        utility_session=utility_session,
    )

    # Recover orphaned jobs from a previous crash
    await runtime_service.recover_on_startup()

    return _CoreServices(
        approval_service=approval_service,
        adapter_registry=adapter_registry,
        platform_registry=platform_registry,
        merge_service=merge_service,
        utility_session=utility_session,
        runtime_service=runtime_service,
    )


async def _init_optional_services(
    app: FastAPI,
    config: Any,
    session_factory: Any,
    services: _CoreServices,
) -> tuple[Any, asyncio.Task[None], Any]:
    """Initialise terminal, voice, retention, model cache, and MCP services.

    Returns (terminal_service, retention_task, mcp_cleanup) needed for
    shutdown.
    """
    from backend.api import terminal

    # --- Terminal service ---
    terminal_service = None
    if config.terminal.enabled:
        from backend.services.terminal_service import TerminalService

        terminal_service = TerminalService(
            max_sessions=config.terminal.max_sessions,
            default_shell=config.terminal.default_shell,
            scrollback_size_kb=config.terminal.scrollback_size_kb,
        )
        terminal.set_terminal_service(terminal_service)
        terminal.set_utility_session(services.utility_session)
        app.state.terminal_service = terminal_service
        log.debug("terminal_service_enabled", max_sessions=config.terminal.max_sessions)

    # --- Model list cache ---
    # Fetch once at startup so the job-creation form renders instantly.
    # The SDK is not hot-swapped at runtime, so a one-time cache is correct.
    cached_models: list[dict[str, object]] = []
    try:
        from copilot import CopilotClient

        _model_client = CopilotClient()
        await _model_client.start()
        try:
            cached_models = [m.to_dict() for m in await _model_client.list_models()]
            log.debug("models_cached", count=len(cached_models))
        finally:
            await _model_client.stop()
    except Exception as exc:
        log.warning(
            "model_cache_failed",
            error=str(exc),
            client_type="CopilotClient",
            exc_info=True,
        )
    app.state.cached_models = cached_models

    # --- Voice service ---
    voice_service = VoiceService()
    # Pre-load the whisper model at startup so the first request is fast
    log.debug("voice_model_preloading", model="base.en")
    await asyncio.to_thread(voice_service._ensure_model)  # noqa: SLF001
    app.state.voice_service = voice_service
    app.state.voice_max_bytes = VOICE_MAX_AUDIO_SIZE_MB * 1024 * 1024

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

    # --- MCP server ---
    from backend.mcp.server import create_mcp_server

    mcp_server = create_mcp_server(
        session_factory=session_factory,
        runtime_service=services.runtime_service,
        approval_service=services.approval_service,
    )
    app.state.mcp_server = mcp_server
    mcp_app = mcp_server.streamable_http_app()
    app.mount(MCP_PATH, mcp_app)
    # Manually start the session manager's task group (sub-app lifespan
    # doesn't fire when mounted during the parent's lifespan).
    mcp_ctx = mcp_server.session_manager.run()
    await mcp_ctx.__aenter__()
    mcp_cleanup = mcp_ctx
    log.debug("mcp_server_mounted", path=MCP_PATH)

    return terminal_service, retention_task, mcp_cleanup


# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage engine lifecycle — create on startup, dispose on shutdown."""
    engine = create_engine()
    session_factory = create_session_factory(engine)

    event_bus, sse_manager = _init_event_infrastructure(session_factory)

    config = load_config()
    services = await _wire_core_services(session_factory, event_bus, config)

    # Store on app.state for access from route handlers
    app.state.event_bus = event_bus
    app.state.sse_manager = sse_manager
    app.state.session_factory = session_factory
    app.state.runtime_service = services.runtime_service
    app.state.merge_service = services.merge_service
    app.state.platform_registry = services.platform_registry
    app.state.approval_service = services.approval_service
    app.state.adapter_registry = services.adapter_registry
    app.state.utility_session = services.utility_session

    terminal_service, retention_task, mcp_cleanup = await _init_optional_services(
        app, config, session_factory, services,
    )

    # Session factory available for route handlers that need ad-hoc sessions
    app.state.session_factory = session_factory

    # Session dependency override for job routes
    from backend.api import jobs

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

    # Shutdown in reverse initialisation order
    await mcp_cleanup.__aexit__(None, None, None)
    retention_task.cancel()
    if terminal_service is not None:
        await terminal_service.shutdown()
    await services.utility_session.shutdown()
    await services.runtime_service.shutdown()
    await sse_manager.close_all()
    app.dependency_overrides.clear()
    await engine.dispose()
