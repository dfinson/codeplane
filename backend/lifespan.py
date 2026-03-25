"""Application lifespan — startup and shutdown management for CodePlane.

Handles database initialisation, service wiring, background tasks, and
graceful shutdown.  Extracted from main.py to keep concerns separated.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from dishka import make_async_container
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.config import MCP_PATH, VOICE_MAX_AUDIO_SIZE_MB, CPLConfig, load_config
from backend.di import AppProvider, CachedModelsBySdk, RequestProvider, VoiceMaxBytes
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
    from backend.services.terminal_service import TerminalService

log = structlog.get_logger()

_EVENT_PERSIST_MAX_ATTEMPTS = 3
_EVENT_PERSIST_RETRY_DELAY_S = 0.05
_DEAD_LETTER_RETRY_INTERVAL_S = 5.0
_DEAD_LETTER_MAX_RETRIES = 10


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
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[EventBus, SSEManager, asyncio.Task[None]]:
    """Create event bus and SSE manager with persist-then-broadcast wiring.

    Returns the event bus, SSE manager, and a background task that retries
    events from the dead-letter queue.
    """
    event_bus = EventBus()
    sse_manager = SSEManager()
    persist_lock = asyncio.Lock()
    dead_letter: asyncio.Queue[tuple[DomainEvent, int]] = asyncio.Queue()

    # Persist-then-broadcast subscriber: ensures event.db_id is set
    # (monotonic autoincrement) before SSE frames are built.
    async def _persist_and_broadcast(event: DomainEvent) -> None:
        try:
            await _persist_event_with_retry(
                event=event,
                session_factory=session_factory,
                write_lock=persist_lock,
            )
        except Exception:
            log.error(
                "event_persist_failed_queued_for_retry",
                event_id=event.event_id,
                job_id=event.job_id,
                kind=event.kind.value,
            )
            dead_letter.put_nowait((event, 0))
            # Broadcast anyway so the SSE stream doesn't silently drop the
            # event; the client will get it without a db_id which means the
            # replay cursor won't cover it, but it's better than silence.
            await sse_manager.broadcast_domain_event(event)
            return
        await sse_manager.broadcast_domain_event(event)

    async def _dead_letter_retry_loop() -> None:
        """Background task: retry persisting events that failed initially."""
        while True:
            try:
                event, attempt = await asyncio.wait_for(dead_letter.get(), timeout=_DEAD_LETTER_RETRY_INTERVAL_S)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            try:
                await _persist_event_with_retry(
                    event=event,
                    session_factory=session_factory,
                    write_lock=persist_lock,
                )
                log.info(
                    "dead_letter_event_persisted",
                    event_id=event.event_id,
                    job_id=event.job_id,
                    retry_attempt=attempt + 1,
                )
            except Exception:
                next_attempt = attempt + 1
                if next_attempt < _DEAD_LETTER_MAX_RETRIES:
                    dead_letter.put_nowait((event, next_attempt))
                    log.warning(
                        "dead_letter_retry_failed",
                        event_id=event.event_id,
                        job_id=event.job_id,
                        attempt=next_attempt,
                    )
                else:
                    log.error(
                        "dead_letter_event_permanently_lost",
                        event_id=event.event_id,
                        job_id=event.job_id,
                        kind=event.kind.value,
                    )

    event_bus.subscribe(_persist_and_broadcast)
    retry_task = asyncio.create_task(_dead_letter_retry_loop(), name="dead-letter-retry")
    return event_bus, sse_manager, retry_task


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


async def _persist_event_with_retry(
    *,
    event: DomainEvent,
    session_factory: async_sessionmaker[AsyncSession],
    write_lock: asyncio.Lock,
    max_attempts: int = _EVENT_PERSIST_MAX_ATTEMPTS,
    retry_delay_s: float = _EVENT_PERSIST_RETRY_DELAY_S,
) -> None:
    async with write_lock:
        for attempt in range(max_attempts):
            async with session_factory() as session:
                repo = EventRepository(session)
                try:
                    await repo.append(event)
                    await session.commit()
                    return
                except OperationalError as exc:
                    await session.rollback()
                    if not _is_sqlite_lock_error(exc) or attempt == max_attempts - 1:
                        raise
                    log.warning(
                        "event_persist_retrying_after_sqlite_lock",
                        event_id=event.event_id,
                        job_id=event.job_id,
                        attempt=attempt + 1,
                    )
            await asyncio.sleep(retry_delay_s * (attempt + 1))


async def _wire_core_services(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config: CPLConfig,
) -> _CoreServices:
    """Instantiate and wire together the core application services."""
    approval_service = ApprovalService(session_factory=session_factory)
    adapter_registry = AdapterRegistry(
        approval_service=approval_service,
        event_bus=event_bus,
        session_factory=session_factory,
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


@dataclass(frozen=True)
class _OptionalServices:
    """Bundle of optional services and background handles for shutdown."""

    terminal_service: TerminalService | None
    retention_task: asyncio.Task[None]
    mcp_cleanup: Any
    voice_service: VoiceService
    voice_max_bytes: int
    cached_models_by_sdk: dict[str, list[dict[str, object]]]


async def _init_optional_services(
    app: FastAPI,
    config: CPLConfig,
    session_factory: async_sessionmaker[AsyncSession],
    services: _CoreServices,
) -> _OptionalServices:
    """Initialise terminal, voice, retention, model cache, and MCP services."""
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
        log.debug("terminal_service_enabled", max_sessions=config.terminal.max_sessions)

    # --- Model list cache ---
    # Fetch once at startup so the job-creation form renders instantly.
    # Models are keyed by SDK id so the frontend can fetch per-SDK.
    cached_models_by_sdk: dict[str, list[dict[str, object]]] = {}

    # Copilot models
    copilot_models: list[dict[str, object]] = []
    try:
        from copilot import CopilotClient

        _model_client = CopilotClient()
        await _model_client.start()
        try:
            copilot_models = [m.to_dict() for m in await _model_client.list_models()]
            log.debug("copilot_models_cached", count=len(copilot_models))
        finally:
            await _model_client.stop()
    except Exception as exc:
        log.warning("copilot_model_cache_failed", error=str(exc))
    cached_models_by_sdk["copilot"] = copilot_models

    # Claude Code models — loaded from data/claude_models.json
    _claude_models_path = Path(__file__).resolve().parent / "data" / "claude_models.json"
    try:
        import json as _json

        cached_models_by_sdk["claude"] = _json.loads(_claude_models_path.read_text())
        log.debug("claude_models_loaded", count=len(cached_models_by_sdk["claude"]))
    except Exception as exc:
        log.warning("claude_models_load_failed", error=str(exc))
        cached_models_by_sdk["claude"] = []

    # --- Voice service ---
    voice_service = VoiceService()
    # Pre-load the whisper model at startup so the first request is fast
    log.debug("voice_model_preloading", model="base.en")
    await asyncio.to_thread(voice_service._ensure_model)  # noqa: SLF001
    voice_max_bytes = VOICE_MAX_AUDIO_SIZE_MB * 1024 * 1024

    # --- Retention service ---
    retention_service = RetentionService(
        session_factory=session_factory,
        config=config,
    )

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
    mcp_app = mcp_server.streamable_http_app()
    app.mount(MCP_PATH, mcp_app)
    # Manually start the session manager's task group (sub-app lifespan
    # doesn't fire when mounted during the parent's lifespan).
    mcp_ctx = mcp_server.session_manager.run()
    await mcp_ctx.__aenter__()
    log.debug("mcp_server_mounted", path=MCP_PATH)

    return _OptionalServices(
        terminal_service=terminal_service,
        retention_task=retention_task,
        mcp_cleanup=mcp_ctx,
        voice_service=voice_service,
        voice_max_bytes=voice_max_bytes,
        cached_models_by_sdk=cached_models_by_sdk,
    )


# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage engine lifecycle — create on startup, dispose on shutdown."""
    engine = create_engine()
    session_factory = create_session_factory(engine)

    event_bus, sse_manager, dead_letter_task = _init_event_infrastructure(session_factory)

    # Wire the console dashboard (present only when stderr is an interactive TTY)
    # to the event bus so job state and progress updates appear in the live panel.
    dashboard = getattr(app.state, "dashboard", None)
    if dashboard is not None:
        event_bus.subscribe(dashboard.handle_event)

    config = load_config()
    services = await _wire_core_services(session_factory, event_bus, config)

    optional = await _init_optional_services(
        app,
        config,
        session_factory,
        services,
    )

    # Build the dishka DI container with all services as context values
    container = make_async_container(
        AppProvider(),
        RequestProvider(),
        context={
            CPLConfig: config,
            async_sessionmaker: session_factory,
            EventBus: event_bus,
            SSEManager: sse_manager,
            ApprovalService: services.approval_service,
            RuntimeService: services.runtime_service,
            MergeService: services.merge_service,
            PlatformRegistry: services.platform_registry,
            UtilitySessionService: services.utility_session,
            VoiceService: optional.voice_service,
            CachedModelsBySdk: CachedModelsBySdk(optional.cached_models_by_sdk),
            VoiceMaxBytes: VoiceMaxBytes(optional.voice_max_bytes),
        },
    )
    app.state.dishka_container = container

    # Print the startup banner now that all services are ready, then
    # activate the Rich live display so it takes over the console.
    banner_args = getattr(app.state, "banner_args", None)
    if banner_args:
        from backend.cli import _print_startup_banner

        _print_startup_banner(**banner_args)

    if dashboard is not None:
        dashboard.start()

    yield

    # Shutdown in reverse initialisation order.
    # Stop the live dashboard first so subsequent log output prints cleanly.
    if dashboard is not None:
        dashboard.stop()
    await container.close()
    await optional.mcp_cleanup.__aexit__(None, None, None)
    optional.retention_task.cancel()
    dead_letter_task.cancel()
    if optional.terminal_service is not None:
        await optional.terminal_service.shutdown()
    await services.utility_session.shutdown()
    await services.runtime_service.shutdown()
    await sse_manager.close_all()
    await engine.dispose()
