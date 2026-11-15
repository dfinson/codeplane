"""Application lifespan — startup and shutdown management for CodePlane.

Handles database initialisation, service wiring, background tasks, and
graceful shutdown.  Extracted from main.py to keep concerns separated.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from dishka import make_async_container
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend import config as backend_config
from backend.config import MCP_PATH, VOICE_MAX_AUDIO_SIZE_MB, CPLConfig, load_config
from backend.di import AppProvider, CachedModelsBySdk, RequestProvider, VoiceMaxBytes
from backend.models.events import EventKind, SessionEvent, new_event
from backend.persistence.database import create_engine, create_session_factory, run_migrations, serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.step_repo import StepRepository
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.platform_adapter import PlatformRegistry
from backend.services.analytics.model_pricing import ModelPricingService
from backend.services.artifacts.diff_service import DiffService
from backend.services.completers.narrator_completer import NarratorCompleter
from backend.services.completers.summarization_service import SummarizationService
from backend.services.completers.voice_service import VoiceService
from backend.services.events.event_bus import EventBus
from backend.services.events.sse_manager import SSEManager
from backend.services.git.git_service import GitService
from backend.services.job.approval_service import ApprovalService
from backend.services.job.retention_service import RetentionService
from backend.services.merge_service import MergeService
from backend.services.runtime import RuntimeService
from backend.services.sharing.push_service import PushService
from backend.services.sharing.share_service import ShareService
from backend.services.sharing.vapid_keys import get_or_create_vapid_keys
from backend.services.sidecar.dispatcher import SidecarDispatcher
from backend.services.sidecar.session import SidecarSessionManager
from backend.services.steps.persistence import StepPersistenceSubscriber
from backend.services.steps.tracker import StepTracker
from backend.services.task_execution_service import TaskExecutionService
from backend.services.terminal.terminal_service import TerminalService
from backend.services.tracker_write_service import TrackerWriteService

# Tracks fire-and-forget background tasks so they can be awaited on shutdown.
_ephemeral_tasks: set[asyncio.Task[Any]] = set()  # noqa: WPS407


def _fire_and_forget(coro: Any, *, name: str) -> asyncio.Task[Any]:
    """Schedule a coroutine as a tracked background task."""
    task: asyncio.Task[Any] = asyncio.create_task(coro, name=name)
    _ephemeral_tasks.add(task)
    task.add_done_callback(_ephemeral_tasks.discard)
    return task


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession
    from traceforge.types import TitleUpdate

    from backend.services.completers.copilot_steer import CopilotSteerClient
    from backend.services.events.ingest_service import IngestService
    from backend.services.events.telemetry_subscriber import TelemetrySubscriber


log = structlog.get_logger()


# Set by the restart helper on the replacement process's environment before
# exec (SPEC.md CAP-5; matches backend/services/dev_restart/restart_helper.py's
# ``env["CODEPLANE_RESTART_REQUEST_ID"] = request_id``). Absent on every normal
# ``cpl up`` launch, which is exactly what keeps readiness-marker publication —
# and the lazy import of the shared restart-protocol module below — out of the
# ordinary startup path.
_RESTART_REQUEST_ID_ENV = "CODEPLANE_RESTART_REQUEST_ID"


def _build_copilot_steer_client() -> CopilotSteerClient | None:
    copilot_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if copilot_token:
        from backend.services.completers.copilot_steer import CopilotSteerClient

        return CopilotSteerClient(copilot_token)
    return None


async def _publish_restart_readiness(
    request_id: str,
    recovery_task: asyncio.Task[Any],
    remote_check_task: asyncio.Task[Any] | None,
) -> None:
    """Write the atomic restart-ready marker once recovery and deferred remote validation succeed.

    Runs only for a restart-triggered startup (``request_id`` is only ever
    non-empty when the replacement process was launched by the restart
    helper). The marker is the developer's signal that startup recovery *and*
    any deferred remote validation for this run both already completed
    *successfully* — not merely that the port is reachable (SPEC.md CAP-5).
    If either step fails or is cancelled, the marker is deliberately **not**
    written and the failure is re-raised (no broad failure-tolerance catch
    that publishes readiness anyway) — the helper's readiness wait should time
    out and report a real failure rather than observe a false "ready" signal.
    Diagnostic detail (which phase failed, why) is redacted structlog output
    only; per ARCHITECTURE-SPINE.md AD-12 and the locked wire contract, the
    marker itself carries nothing but ``{requestId, pid, writtenAt}`` — no
    error text, tokens, or hostnames.
    """
    try:
        await recovery_task
    except asyncio.CancelledError:
        log.warning("restart_readiness_recovery_cancelled", request_id=request_id)
        raise
    except Exception as exc:
        log.warning(
            "restart_readiness_recovery_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise

    if remote_check_task is not None:
        try:
            await remote_check_task
        except asyncio.CancelledError:
            log.warning("restart_readiness_remote_check_cancelled", request_id=request_id)
            raise
        except Exception as exc:
            # A failed/killed remote check (e.g. the Cloudflare Access
            # shutdown path) means this startup is not actually ready —
            # distinguished from a recovery failure only by which log event
            # fires; the marker is withheld in both cases.
            log.warning(
                "restart_readiness_remote_check_failed",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise

    # Lazy, function-local import: normal startup (no restart request id)
    # never imports the shared restart-protocol module at all. When a real
    # restart-triggered startup reaches this point but the module is still
    # missing, this fails loudly (ImportError) rather than falling back to a
    # duplicated marker-path/JSON implementation — see coordination with the
    # restart-protocol integration slice (backend/services/dev_restart/).
    from backend.services.dev_restart.restart_protocol import get_request_paths, write_json_atomic

    paths = get_request_paths(request_id)
    write_json_atomic(
        paths.ready,
        {
            "requestId": request_id,
            "pid": os.getpid(),
            "writtenAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    log.info(
        "restart_readiness_published",
        request_id=request_id,
        pid=os.getpid(),
    )


async def _deferred_cloudflare_access_check(tunnel_handle: Any, app: Any) -> None:
    """Verify Cloudflare Access is active after the server starts accepting connections.

    Uses two strategies in order:
    1. Cloudflare SDK — queries the Zero Trust Access API directly (requires
       CLOUDFLARE_API_TOKEN with Access:Read permission).
    2. HTTP probe fallback — probes through the tunnel looking for a CF Access
       redirect (retries on transient connection errors).

    If neither strategy can confirm Access is active, shuts down to prevent
    unprotected exposure.
    """
    import urllib.error
    import urllib.request

    banner_args = getattr(app.state, "banner_args", {})
    local_port = banner_args.get("port", 8080)
    tunnel_url = tunnel_handle.origin
    hostname = tunnel_url.removeprefix("https://").rstrip("/")

    # --- Strategy 1: Cloudflare SDK ---
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if api_token:
        account_id = _extract_account_id_from_tunnel_token()
        if account_id:
            try:
                from cloudflare import AsyncCloudflare

                client = AsyncCloudflare(api_token=api_token)
                apps = await client.zero_trust.access.applications.list(account_id=account_id)
                for access_app in apps:
                    # Check if any Access application covers this hostname
                    app_domain = getattr(access_app, "domain", None)
                    app_name = getattr(access_app, "name", "")
                    if app_domain and app_domain == hostname:
                        log.info("cloudflare_access_verified", url=tunnel_url, method="sdk", app_name=app_name)
                        return
                    # Also check self_hosted_domains for multi-domain apps
                    self_hosted = getattr(access_app, "self_hosted_domains", None) or []
                    if hostname in self_hosted:
                        log.info("cloudflare_access_verified", url=tunnel_url, method="sdk", app_name=app_name)
                        return
                log.warning(
                    "cf_access_sdk_no_match",
                    hostname=hostname,
                    msg="No Access application found covering this hostname via API",
                )
            except Exception as exc:
                log.warning(
                    "cf_access_sdk_failed",
                    error=str(exc),
                    type=type(exc).__name__,
                    msg="Falling back to HTTP probe",
                )

    # --- Strategy 2: HTTP probe with retries ---
    # Wait for the local server to be ready (it just yielded, so should be immediate)
    for _ in range(20):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{local_port}/api/health", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    break
        except Exception:
            pass
        await asyncio.sleep(0.5)

    # Probe the tunnel URL for Cloudflare Access.
    # Use a non-redirecting opener so we can inspect the 302 from Access
    # instead of following it to the login page (which returns 200 with
    # no CF-Access-Domain header).
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any) -> None:  # noqa: PLR0913
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(f"{tunnel_url}/api/health", method="HEAD")
            req.add_header("User-Agent", "cpl-preflight/1.0")
            resp = opener.open(req, timeout=10)  # noqa: S310
            log.info("cf_access_probe_response", status=resp.status, headers=dict(resp.headers))
            if resp.headers.get("CF-Access-Domain"):
                log.info("cloudflare_access_verified", url=tunnel_url, method="http_probe")
                return
            # Got a 2xx without CF-Access-Domain — no Access gate
            break
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location", "")
            log.info(
                "cf_access_probe_httperror",
                status=exc.code,
                location=location,
                headers=dict(exc.headers) if exc.headers else {},
            )
            if "cloudflareaccess.com" in location or exc.headers.get("CF-Access-Domain"):
                log.info("cloudflare_access_verified", url=tunnel_url, method="http_probe")
                return
            # Non-Access HTTP error (e.g. 502 from tunnel not yet routing) — retry
            if attempt < max_attempts - 1:
                log.info("cf_access_probe_retrying", attempt=attempt + 1, status=exc.code)
                await asyncio.sleep(2)
                continue
            break
        except Exception as exc:
            # Connection errors, timeouts — transient, retry
            log.warning("cf_access_probe_exception", error=str(exc), type=type(exc).__name__, attempt=attempt + 1)
            if attempt < max_attempts - 1:
                await asyncio.sleep(2)
                continue
            break

    # No Access gate detected — refuse to serve unprotected
    log.critical(
        "cloudflare_access_not_detected",
        url=tunnel_url,
        msg="No Cloudflare Access gate detected. Shutting down to prevent unprotected exposure.",
    )
    tunnel_handle.close()
    _request_graceful_shutdown()


def _request_graceful_shutdown() -> None:
    """Ask this process to shut down, portably.

    ``os.kill(os.getpid(), signal.SIGTERM)`` is a hard ``TerminateProcess``
    call on Windows: the process dies immediately, so uvicorn never runs its
    graceful shutdown and the lifespan shutdown handlers (worktree cleanup,
    lease release, DB flush) never run at all. Raising the installed SIGTERM
    handler in-process keeps the same shutdown path on every platform, and
    ``os.kill`` remains the fallback when no handler is installed.
    """
    import signal

    handler = signal.getsignal(signal.SIGTERM)
    if getattr(handler, "codeplane_absorbs_signal", False):
        # ``cli._neutralize_fatal_termination_signals`` installs a handler that
        # swallows the signal so cleanup can run; calling it would return
        # without shutting anything down. Restore the default first so the
        # ``os.kill`` below is guaranteed to stop this process — this path is
        # the emergency stop for an unprotected public exposure, where staying
        # up is the worse outcome.
        with contextlib.suppress(ValueError, OSError, RuntimeError):
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
    elif callable(handler):
        try:
            handler(signal.SIGTERM, None)
            return
        except Exception:  # noqa: BLE001 - fall through to the hard stop below
            log.warning("graceful_shutdown_handler_failed", exc_info=True)

    os.kill(os.getpid(), signal.SIGTERM)


def _extract_account_id_from_tunnel_token() -> str | None:
    """Extract the Cloudflare account ID from the tunnel token (base64 JSON with 'a' field)."""
    import base64

    token = os.environ.get("CPL_CLOUDFLARE_TUNNEL_TOKEN")
    if not token:
        return None
    try:
        # Token is base64-encoded JSON: {"a": "<account_id>", "t": "<tunnel_id>", "s": "..."}
        decoded = base64.b64decode(token + "==")
        data = json.loads(decoded)
        account_id: str | None = data.get("a")
        return account_id
    except Exception:
        return None


def _print_qr_code(url: str) -> None:
    """Print a QR code for *url* to the console (best-effort)."""
    try:
        import io

        import qrcode
        from rich.align import Align
        from rich.console import Console
        from rich.text import Text

        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)

        console = Console(stderr=True)
        console.print()
        console.print(Align.center(Text(buf.getvalue().rstrip("\n"))))
        console.print(Align.center(Text.from_markup(f"Scan to open: [bold]{url}[/bold]")))
        console.print()
    except ImportError:
        log.debug("qrcode_not_available")


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
    sidecar_sessions: SidecarSessionManager
    sidecar_dispatcher: SidecarDispatcher
    narrator_completer: NarratorCompleter
    runtime_service: RuntimeService
    git_service: GitService
    diff_service: DiffService
    step_tracker: StepTracker
    task_execution_service: TaskExecutionService
    tracker_write_service: TrackerWriteService


def _schedule_execution_recovery(
    services: _CoreServices,
    telemetry_subscriber: TelemetrySubscriber,
    ingest_service: IngestService,
) -> asyncio.Task[None]:
    """Bind constructed consumers before any recovered SDK can emit events."""
    services.runtime_service.set_telemetry_subscriber(telemetry_subscriber)
    services.runtime_service.set_ingest_service(ingest_service)

    async def recover() -> None:
        await services.approval_service.recover_pending_approvals()
        await services.task_execution_service.recover_on_startup()
        await services.tracker_write_service.recover_on_startup()
        await services.runtime_service.recover_on_startup()
        services.tracker_write_service.wake()

    return asyncio.create_task(recover(), name="recover-on-startup")


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
    dead_letter: asyncio.Queue[tuple[SessionEvent, int]] = asyncio.Queue()

    # Persist-then-broadcast subscriber: keeps the storage-local SSE resume
    # cursor separate from the canonical TraceForge event envelope.
    async def _persist_and_broadcast(event: SessionEvent) -> None:
        # message.delta events are ephemeral streaming chunks — broadcast
        # immediately without writing to DB (the complete agent message
        # that follows is the canonical persisted record).
        if event.kind == EventKind.message_delta:
            await sse_manager.broadcast_domain_event(event)
            return

        # System-level events (no job association) cannot be persisted to the
        # events table (job_id FK constraint). Broadcast only.
        if not event.session_id:
            await sse_manager.broadcast_domain_event(event)
            return

        try:
            db_id = await _persist_event_with_retry(
                event=event,
                session_factory=session_factory,
            )
        except OperationalError:
            if "_codeplane_source" in event.payload or event.kind in (
                EventKind.verification_started,
                EventKind.verification_result,
            ):
                raise
            log.error(
                "event_persist_failed_queued_for_retry",
                event_id=event.id,
                job_id=event.session_id,
                kind=str(event.kind),
            )
            dead_letter.put_nowait((event, 0))
            # Broadcast anyway so the SSE stream doesn't silently drop the
            # event; the client will get it without a storage cursor, so replay
            # won't cover it, but it's better than silence.
            await sse_manager.broadcast_domain_event(event)
            return
        await sse_manager.broadcast_domain_event(event, storage_cursor=db_id)

    async def _dead_letter_retry_loop() -> None:
        """Background task: retry persisting events that failed initially."""
        while True:
            try:
                event, attempt = await asyncio.wait_for(dead_letter.get(), timeout=_DEAD_LETTER_RETRY_INTERVAL_S)
            except TimeoutError:
                continue  # normal wait_for timeout — poll again
            except asyncio.CancelledError:
                return

            try:
                await _persist_event_with_retry(
                    event=event,
                    session_factory=session_factory,
                )
                log.info(
                    "dead_letter_event_persisted",
                    event_id=event.id,
                    job_id=event.session_id,
                    retry_attempt=attempt + 1,
                )
            except OperationalError:
                next_attempt = attempt + 1
                if next_attempt < _DEAD_LETTER_MAX_RETRIES:
                    dead_letter.put_nowait((event, next_attempt))
                    log.warning(
                        "dead_letter_retry_failed",
                        event_id=event.id,
                        job_id=event.session_id,
                        attempt=next_attempt,
                    )
                else:
                    log.error(
                        "dead_letter_event_permanently_lost",
                        event_id=event.id,
                        job_id=event.session_id,
                        kind=str(event.kind),
                    )

    event_bus.subscribe(_persist_and_broadcast, required=True)

    # Step persistence subscriber — persists step_started/step_completed events
    step_repo = StepRepository(session_factory)
    step_persistence = StepPersistenceSubscriber(step_repo)
    event_bus.subscribe(step_persistence)

    retry_task = asyncio.create_task(_dead_letter_retry_loop(), name="dead-letter-retry")
    return event_bus, sse_manager, retry_task


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


async def _persist_event_with_retry(
    *,
    event: SessionEvent,
    session_factory: async_sessionmaker[AsyncSession],
    max_attempts: int = _EVENT_PERSIST_MAX_ATTEMPTS,
    retry_delay_s: float = _EVENT_PERSIST_RETRY_DELAY_S,
) -> int | None:
    """Persist *event*, returning its autoincrement DB id (the SSE resume cursor)."""
    for attempt in range(max_attempts):
        try:
            async with serialized_write(session_factory) as session:
                repo = EventRepository(session)
                return await repo.append(event)
        except OperationalError as exc:
            if not _is_sqlite_lock_error(exc) or attempt == max_attempts - 1:
                raise
            log.warning(
                "event_persist_retrying_after_sqlite_lock",
                event_id=event.id,
                job_id=event.session_id,
                attempt=attempt + 1,
            )
            await asyncio.sleep(retry_delay_s * (attempt + 1))
    return None


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
        config=config,
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

    # --- Sidecar session manager (per-job dedicated utility sessions) ---
    utility_adapter = adapter_registry.get_adapter(adapter_registry.policy.default_sdk)
    sidecar_sessions = SidecarSessionManager(
        adapter=utility_adapter,
        model=config.runtime.utility_model,
    )
    log.debug("sidecar_sessions_starting", model=config.runtime.utility_model, sdk=config.runtime.default_sdk)
    await sidecar_sessions.start()

    # --- Sidecar dispatcher (trigger evaluation + pipeline execution) ---
    # The gate handler is wired after RuntimeService is created (below).
    sidecar_dispatcher = SidecarDispatcher(
        session_manager=sidecar_sessions,
        event_bus=event_bus,
        session_factory=session_factory,
    )
    await sidecar_dispatcher.start()
    event_bus.subscribe(sidecar_dispatcher.handle_event)

    # --- Preflight curator (pre-job context curation: memory + structural) ---
    from backend.services.tools.preflight_curator import PreflightCurator

    preflight_curator = PreflightCurator(adapter=utility_adapter, model=config.runtime.utility_model)

    # --- Narrator completer (dedicated long-form story generation) ---
    narrator_completer = NarratorCompleter(
        adapter=utility_adapter,
        model=config.runtime.utility_model,
    )

    summarization_service = SummarizationService(
        session_factory=session_factory,
        adapter=sidecar_sessions,
    )

    # Plan-step orchestration is now handled by TrailService (unified timeline)
    # ProgressTrackingService has been retired.

    # StepTracker is shared between RuntimeService (managed pre-step context)
    # and the EventProcessor funnel (wired in lifespan once TrailService exists).
    step_tracker = StepTracker(
        event_bus=event_bus,
        git_service=git_service,
    )

    runtime_service = RuntimeService(
        session_factory=session_factory,
        event_bus=event_bus,
        adapter_registry=adapter_registry,
        config=config,
        approval_service=approval_service,
        diff_service=diff_service,
        git_service=git_service,
        merge_service=merge_service,
        summarization_service=summarization_service,
        platform_registry=platform_registry,
        sidecar_sessions=sidecar_sessions,
        step_tracker=step_tracker,
        sidecar_dispatcher=sidecar_dispatcher,
    )

    tracker_write_service = TrackerWriteService(
        approval_service,
        session_factory=session_factory,
        event_bus=event_bus,
    )
    task_execution_service = TaskExecutionService(
        session_factory,
        config,
        git_service=git_service,
        approval_service=approval_service,
        tracker_write_service=tracker_write_service,
    )

    # Wire the preflight curator
    runtime_service.set_preflight_curator(preflight_curator)

    # Wire the sidecar gate handler — pauses agent tools on reject/hold verdicts.
    async def _gate_handler(job_id: str, sidecar_name: str, verdict: str, reason: str) -> None:
        await runtime_service.handle_sidecar_gate(job_id, sidecar_name, verdict, reason)

    sidecar_dispatcher.set_gate_handler(_gate_handler)

    # Wire the sidecar agent message handler — injects sidecar output into the agent conversation.
    async def _agent_message_handler(job_id: str, message: str) -> None:
        await runtime_service.inject_sidecar_message(job_id, message)

    sidecar_dispatcher.set_agent_message_handler(_agent_message_handler)

    # NOTE: Orphan recovery is deferred until the trail service is subscribed
    # to the event bus.  This prevents a race where SessionResumed events
    # would be published before the trail node builder is listening.

    return _CoreServices(
        approval_service=approval_service,
        adapter_registry=adapter_registry,
        platform_registry=platform_registry,
        merge_service=merge_service,
        sidecar_sessions=sidecar_sessions,
        sidecar_dispatcher=sidecar_dispatcher,
        narrator_completer=narrator_completer,
        runtime_service=runtime_service,
        git_service=git_service,
        diff_service=diff_service,
        step_tracker=step_tracker,
        task_execution_service=task_execution_service,
        tracker_write_service=tracker_write_service,
    )


@dataclass(frozen=True)
class _OptionalServices:
    """Bundle of optional services and background handles for shutdown."""

    terminal_service: TerminalService | None
    retention_task: asyncio.Task[None]
    mcp_task: asyncio.Task[None]
    mcp_stop_event: asyncio.Event
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

    # --- Terminal service ---
    terminal_service = None
    if config.terminal.enabled:
        terminal_service = TerminalService(
            max_sessions=config.terminal.max_sessions,
            default_shell=config.terminal.default_shell,
            scrollback_size_kb=config.terminal.scrollback_size_kb,
        )
        services.runtime_service.set_terminal_service(terminal_service)
        log.debug("terminal_service_enabled", max_sessions=config.terminal.max_sessions)

    # --- Model list cache ---
    # Fetch once at startup so the job-creation form renders instantly.
    # Models are keyed by SDK id so the frontend can fetch per-SDK.
    cached_models_by_sdk: dict[str, list[dict[str, object]]] = {}

    # Copilot models — retry up to 3 times since auth tokens may not be ready immediately
    copilot_models: list[dict[str, object]] = []
    for _attempt in range(3):
        try:
            from backend.services.copilot_adapter._models import fetch_copilot_models_raw

            copilot_models = await fetch_copilot_models_raw()
            log.debug("copilot_models_cached", count=len(copilot_models))
            break  # success
        except Exception as exc:
            if _attempt < 2:
                log.debug("copilot_model_cache_retry", attempt=_attempt + 1, error=str(exc))
                await asyncio.sleep(2)
            else:
                log.warning("copilot_model_cache_failed", error=str(exc))
    cached_models_by_sdk["copilot"] = copilot_models

    # Claude Code models — loaded from data/claude_models.json
    _claude_models_path = Path(__file__).resolve().parent / "data" / "claude_models.json"
    try:
        import json as _json

        cached_models_by_sdk["claude"] = _json.loads(_claude_models_path.read_text())
        log.debug("claude_models_loaded", count=len(cached_models_by_sdk["claude"]))
    except (json.JSONDecodeError, OSError) as exc:
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
    #
    # The MCP SDK's streamable_http_app() returns a Starlette app whose lifespan
    # calls session_manager.run() — an anyio task group context manager.  When
    # FastAPI mounts this sub-app it merges the sub-app lifespan into the parent
    # chain.  Because FastAPI's merged lifespan yields (to let the app serve),
    # the anyio task-group scope ends up being entered and exited in different
    # task contexts, causing:
    #
    #   RuntimeError: Attempted to exit cancel scope in a different task
    #
    # Fix: wrap the sub-app in an ASGI middleware that intercepts lifespan
    # events (standard ASGI protocol), preventing the sub-app's own lifespan
    # from firing.  We then manage session_manager.run() ourselves in a
    # dedicated asyncio task where the anyio scope stays within one task.
    #
    from backend.mcp.server import create_mcp_server

    mcp_server = create_mcp_server(
        session_factory=session_factory,
        runtime_service=services.runtime_service,
        approval_service=services.approval_service,
        merge_service=services.merge_service,
        sidecar_sessions=services.sidecar_sessions,
        task_execution_service=services.task_execution_service,
        tracker_write_service=services.tracker_write_service,
    )
    mcp_app = mcp_server.streamable_http_app()

    class _StripLifespan:
        """ASGI wrapper that handles lifespan events as no-ops.

        All HTTP/WebSocket scopes pass through to the wrapped app unchanged.
        Lifespan scopes are acknowledged immediately without delegating to the
        wrapped app, preventing it from running its own lifespan logic.
        """

        __slots__ = ("_app",)

        def __init__(self, wrapped_app: Any) -> None:
            self._app = wrapped_app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "lifespan":
                await receive()  # lifespan.startup
                await send({"type": "lifespan.startup.complete"})
                await receive()  # lifespan.shutdown
                await send({"type": "lifespan.shutdown.complete"})
                return
            await self._app(scope, receive, send)

    app.mount(MCP_PATH, _StripLifespan(mcp_app))

    # Run the session manager in a dedicated asyncio task.  This keeps the
    # anyio task-group scope entirely within one task for its full lifetime.
    mcp_stop_event = asyncio.Event()
    mcp_started_event = asyncio.Event()

    async def _run_mcp_session_manager() -> None:
        async with mcp_server.session_manager.run():
            mcp_started_event.set()
            await mcp_stop_event.wait()

    mcp_task = asyncio.create_task(_run_mcp_session_manager(), name="mcp-session-mgr")
    await mcp_started_event.wait()
    log.debug("mcp_server_mounted", path=MCP_PATH)

    return _OptionalServices(
        terminal_service=terminal_service,
        retention_task=retention_task,
        mcp_task=mcp_task,
        mcp_stop_event=mcp_stop_event,
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
    await asyncio.to_thread(run_migrations)
    from backend.services.analytics.telemetry import init_telemetry

    init_telemetry()

    # Install a custom asyncio exception handler to suppress benign
    # InvalidStateError from the Copilot SDK's JSON-RPC transport.
    # The SDK reader thread schedules call_soon_threadsafe(future.set_result)
    # which can race with _fail_pending_requests on session teardown.
    _loop = asyncio.get_running_loop()
    _default_handler = _loop.get_exception_handler()

    def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, asyncio.InvalidStateError):
            log.debug("asyncio_invalid_state_suppressed", message=context.get("message", ""))
            return
        if _default_handler is not None:
            _default_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    _loop.set_exception_handler(_asyncio_exception_handler)

    engine = create_engine()
    session_factory = create_session_factory(engine)

    event_bus, sse_manager, dead_letter_task = _init_event_infrastructure(session_factory)
    app.state.event_bus = event_bus

    # Wire the console dashboard (present only when stderr is an interactive TTY)
    # to the event bus so job state and progress updates appear in the live panel.
    dashboard = getattr(app.state, "dashboard", None)
    if dashboard is not None:
        event_bus.subscribe(dashboard.handle_event)

    config = load_config()

    from backend.services.auth.middleware import configure_internal_endpoint

    launch = getattr(app.state, "banner_args", None) or {}
    configure_internal_endpoint(
        host=launch.get("host", config.server.host),
        port=launch.get("port", config.server.port),
    )

    services = await _wire_core_services(session_factory, event_bus, config)

    optional = await _init_optional_services(
        app,
        config,
        session_factory,
        services,
    )

    # --- Push notification service ---
    vapid_keys = get_or_create_vapid_keys(backend_config.get_codeplane_dir())
    push_service = PushService(
        vapid_private_key=vapid_keys["private_key"],
        vapid_public_key=vapid_keys["public_key"],
        session_factory=session_factory,
    )
    await push_service.load_from_db()

    async def _push_subscriber(event: SessionEvent) -> None:
        """Send push notifications for actionable and terminal job events."""
        if event.kind == EventKind.approval_requested:
            desc = event.payload.get("description", "Action requires your approval")
            await push_service.notify(
                title="Approval needed",
                body=str(desc),
                tag=f"approval-{event.payload.get('approval_id', event.session_id)}",
                url=f"/jobs/{event.session_id}",
            )
        elif event.kind == EventKind.batch_approval_requested:
            desc = event.payload.get("description", "Batch action requires your approval")
            await push_service.notify(
                title="Batch approval needed",
                body=str(desc),
                tag=f"batch-approval-{event.session_id}",
                url=f"/jobs/{event.session_id}",
            )
        elif event.kind == EventKind.merge_conflict:
            files = event.payload.get("conflict_files", [])
            body = f"{len(files)} conflicting file(s)" if files else "Merge conflict detected"
            await push_service.notify(
                title="Merge conflict",
                body=body,
                tag=f"conflict-{event.session_id}",
                url=f"/jobs/{event.session_id}",
            )
        elif event.kind == EventKind.job_review:
            pr_url = event.payload.get("pr_url", "")
            body = f"PR ready: {pr_url}" if pr_url else "Job ready for review"
            await push_service.notify(
                title="Job ready for review",
                body=body,
                tag=f"review-{event.session_id}",
                url=f"/jobs/{event.session_id}",
            )
        elif event.kind == EventKind.stall_detected:
            await push_service.notify(
                title="Job stalled",
                body="Agent may be stuck",
                tag=f"stall-{event.session_id}",
                url=f"/jobs/{event.session_id}",
            )
        elif event.kind == EventKind.job_completed:
            await push_service.notify(
                title="Job completed",
                body=str(event.payload.get("resolution", "done")),
                tag=f"job-{event.session_id}",
                url=f"/jobs/{event.session_id}",
            )
        elif event.kind == EventKind.job_failed:
            reason = event.payload.get("reason", "unknown error")
            await push_service.notify(
                title="Job failed",
                body=str(reason),
                tag=f"job-{event.session_id}",
                url=f"/jobs/{event.session_id}",
            )

    event_bus.subscribe(_push_subscriber)

    # --- Motivation summarization service (background) ---
    from backend.services.story.motivation import MotivationService

    motivation_service = MotivationService(
        session_factory=session_factory,
        completer=services.sidecar_sessions,
    )
    motivation_task = asyncio.create_task(motivation_service.drain_loop(), name="motivation-drain")

    # --- Trail service (agent audit trail) ---
    from backend.services.trail import TrailService

    trail_service = TrailService(
        session_factory=session_factory,
        event_bus=event_bus,
        sidecar_sessions=services.sidecar_sessions,
        config=config.trail,
    )
    event_bus.subscribe(trail_service.handle_event)
    services.runtime_service.set_trail_service(trail_service)
    trail_task = asyncio.create_task(trail_service.drain_loop(), name="trail-enrichment-drain")

    # --- EventProcessor (the one funnel) ---
    # Both producers (managed SDK adapters via RuntimeService, and the imported
    # ingest sources) route their native traceforge.SessionEvent through this
    # single processor: TF enrichment (classification, visibility, phases,
    # duration, risk), tool_display derivation, diff triggering, turn_id
    # synthesis, step/trail annotation, EventBus publishing, and title
    # inference (boundary + ONNX model → TitleUpdate → turn_summary events).
    from traceforge.enricher import Enricher as TFEnricher
    from traceforge.pipeline import EventPipeline as TFEventPipeline
    from traceforge.sinks.callback import CallbackSink as TFCallbackSink

    from backend.services.events.event_processor import RECONSTRUCTING_EVENTS, EventProcessor
    from backend.services.events.turn_summary import build_turn_summary_payload

    tf_enricher = TFEnricher(
        flush_on_session_end=True,
    )

    async def _on_title_update(update: TitleUpdate) -> None:
        """Convert a TraceForge TitleUpdate to a CodePlane turn_summary event.

        Delegates the field mapping to ``build_turn_summary_payload`` (pure,
        unit-tested). Session-kind titles map to ``None`` and are skipped.
        """
        if RECONSTRUCTING_EVENTS.get():
            return
        payload = build_turn_summary_payload(update)
        if payload is None:
            return
        import uuid

        event = new_event(
            session_id=update.session_id,
            timestamp=datetime.now(UTC),
            kind=EventKind.turn_summary,
            payload=payload,
            event_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"codeplane-title-v1:{update.session_id}:{update.segment_id}:{update.kind}:{update.version}",
                )
            ),
        )
        if not await _event_is_persisted(event):
            await event_bus.publish(event)

    async def _event_is_persisted(event: SessionEvent) -> bool:
        from backend.persistence.event_repo import EventRepository

        async with session_factory() as session:
            return await EventRepository(session).contains(event)

    title_sink = TFCallbackSink(on_title_update=_on_title_update)
    title_pipeline = TFEventPipeline(
        sinks=[title_sink],
        enricher=None,  # already enriched inline
        enable_phase=False,
        enable_boundary=True,
        enable_title=True,
    )

    event_processor = EventProcessor(
        event_bus=event_bus,
        diff_service=services.diff_service,
        step_tracker=services.step_tracker,
        trail_service=trail_service,
        enricher=tf_enricher,
        title_pipeline=title_pipeline,
        persisted=_event_is_persisted,
    )
    services.runtime_service.set_event_processor(event_processor)

    # --- Governance substrate (traceforge.governance decision + accrual) ---
    # One process-wide decider owns a SEPARATE durable store (``governance.db``,
    # TraceForge's own alembic — never touches CodePlane's ``alembic_version``) and
    # the three preset pipelines. The decision path is read-only (preflight on a
    # detached clone); the accrual path is driven by a bus subscriber that advances
    # durable budget/taint only for EXECUTED tool calls. The USD spend reader is a
    # synchronous read-only closure over CodePlane's ``data.db``.
    #
    # This MUST be wired before ``recover_on_startup`` is scheduled below —
    # recovery synchronously starts any job that was ``running`` at the last
    # shutdown, and job start requires the governance decider. Previously this
    # block ran much later in startup, so every recovered job's start would
    # raise "governance decider not wired" and silently fail, leaving the job
    # stuck showing "running" in the DB with no actual session behind it.
    from backend.services.action_policy.cost_ceiling import make_job_spend_reader
    from backend.services.action_policy.governance import GovernanceDecider, load_usd_ceilings
    from backend.services.events.governance_subscriber import GovernanceSubscriber

    governance_decider = GovernanceDecider(
        db_path=backend_config.get_codeplane_dir() / "governance.db",
        spend_reader=make_job_spend_reader(backend_config.get_codeplane_dir() / "data.db"),
        usd_ceilings=await load_usd_ceilings(session_factory),
    )
    governance_subscriber = GovernanceSubscriber(governance_decider)
    governance_subscriber.subscribe(event_bus)
    services.runtime_service.set_governance(governance_decider, governance_subscriber)

    # Recovery follows completion subscriber registration below.

    # --- Sidecar context providers ---
    # Each provider is a closure over the owning service, matching the
    # ContextProvider signature: Callable[[str], Awaitable[dict[str, Any] | None]]
    from backend.persistence.job_repo import JobRepository

    async def _trigger_event_provider(job_id: str) -> dict[str, Any] | None:
        # Trigger event data arrives via extra_context (merged after providers).
        # This provider exists so the name resolves without a warning.
        return {}

    async def _job_prompt_provider(job_id: str) -> dict[str, Any] | None:
        async with session_factory() as session:
            job = await JobRepository(session).get(job_id)
        if job is None:
            return None
        return {"job_prompt": job.prompt or ""}

    async def _job_diff_provider(job_id: str) -> dict[str, Any] | None:
        async with session_factory() as session:
            job = await JobRepository(session).get(job_id)
        if job is None or not job.worktree_path or not job.base_ref:
            return None
        files = await services.diff_service.calculate_diff(job.worktree_path, job.base_ref)
        summary = "\n".join(f"{f.status.value} {f.path}" for f in files)
        return {"job_diff": summary}

    async def _recent_messages_provider(job_id: str) -> dict[str, Any] | None:
        state = trail_service.get_job_state(job_id)
        if state is None:
            return None
        return {"recent_messages": "\n".join(state.recent_messages)}

    async def _worktree_path_provider(job_id: str) -> dict[str, Any] | None:
        async with session_factory() as session:
            job = await JobRepository(session).get(job_id)
        if job is None or not job.worktree_path:
            return None
        return {"worktree_path": job.worktree_path}

    services.sidecar_dispatcher.register_context("trigger_event", _trigger_event_provider)
    services.sidecar_dispatcher.register_context("job_prompt", _job_prompt_provider)
    services.sidecar_dispatcher.register_context("job_diff", _job_diff_provider)
    services.sidecar_dispatcher.register_context("recent_messages", _recent_messages_provider)
    services.sidecar_dispatcher.register_context("worktree_path", _worktree_path_provider)

    async def _spawn_dependent_task_links(event: SessionEvent) -> None:
        if event.kind not in {EventKind.job_completed, EventKind.job_failed, EventKind.job_canceled}:
            return
        job_id = event.session_id
        if not job_id:
            return

        async def _run_spawn() -> None:
            try:
                execution = services.task_execution_service
                if event.kind == EventKind.job_completed:
                    jobs = await execution.handle_job_completed(job_id, resolution=event.payload.get("resolution"))
                else:
                    await execution.handle_job_failed(job_id)
                    jobs = []
                for job in jobs:

                    async def _setup_and_start(spawned: Any = job) -> None:
                        try:
                            await services.runtime_service.setup_and_start(spawned)
                        except Exception:
                            log.exception("task_link_spawn_setup_failed", job_id=spawned.id)

                    _fire_and_forget(
                        _setup_and_start(),
                        name=f"task-link-spawn-{job.id[:8]}",
                    )
            except Exception:
                log.exception("task_link_completion_failed", job_id=job_id)

        _fire_and_forget(_run_spawn(), name=f"task-link-completion-{job_id[:8]}")

    event_bus.subscribe(_spawn_dependent_task_links)

    # --- IngestService (operator message routing) ---
    from backend.services.events.ingest_service import IngestService

    steer_client = _build_copilot_steer_client()

    # --- ModelPricingService (runtime-fetched LLM pricing) ---
    model_pricing_service = ModelPricingService(
        cache_path=backend_config.get_codeplane_dir() / "model_pricing_cache.json",
        refresh_interval_hours=config.pricing.refresh_interval_hours,
    )
    await model_pricing_service.refresh()
    model_pricing_service.start_background_refresh()

    # --- TelemetrySubscriber (read-side telemetry persistence) ---
    # Persists TelemetrySummary/Spans/file-access rows off the canonical
    # event bus for BOTH producers (managed + imported), replacing the
    # per-producer EventPipeline telemetry writes. Per-message cost is computed
    # via ModelPricingService only when the SDK/mapping did not supply one.
    from backend.services.events.telemetry_subscriber import TelemetrySubscriber

    def _telemetry_schedule_write(coro: Coroutine[Any, Any, None]) -> None:
        _fire_and_forget(coro, name="telemetry-write")

    telemetry_subscriber = TelemetrySubscriber(
        session_factory=session_factory,
        schedule_write=_telemetry_schedule_write,
        model_pricing=model_pricing_service,
    )
    telemetry_subscriber.subscribe(event_bus)
    services.runtime_service.set_telemetry_subscriber(telemetry_subscriber)

    # --- Story pre-generation drain loop (background) ---
    from backend.services.story.service import StoryService as _StoryServiceCls

    story_drain_service = _StoryServiceCls(
        completer=services.narrator_completer,
        session_factory=session_factory,
        model_pricing=model_pricing_service,
        git_service=services.git_service,
    )
    _story_drain_task = asyncio.create_task(story_drain_service.drain_loop(), name="story-drain")

    # --- Copilot ingest source (auto-discover Copilot --remote sessions) ---
    # TF-native ingestion: FileWatchSource tail + MappedJsonAdapter parse feeds
    # traceforge.SessionEvent straight into the shared EventProcessor funnel.
    from backend.services.ingest.copilot_source import SessionStateWatcher

    session_state_watcher = SessionStateWatcher(
        event_processor=event_processor,
        runtime_service=services.runtime_service,
        session_factory=session_factory,
        config=config,
        git_service=services.git_service,
        steer_client=steer_client,
    )
    await session_state_watcher.start()

    # --- Claude ingest source (auto-discover Claude CLI sessions) ---
    from backend.services.ingest.claude_source import ClaudeSessionStateWatcher

    claude_session_watcher = ClaudeSessionStateWatcher(
        event_processor=event_processor,
        runtime_service=services.runtime_service,
        session_factory=session_factory,
        config=config,
        git_service=services.git_service,
        model_pricing=model_pricing_service,
    )
    if services.adapter_registry.policy.enabled("claude"):
        await claude_session_watcher.start()

    ingest_service = IngestService(
        session_factory=session_factory,
        steer_client=steer_client,
        claude_watcher=claude_session_watcher,
        session_state_watcher=session_state_watcher,
    )
    recovery_task = _schedule_execution_recovery(services, telemetry_subscriber, ingest_service)
    await recovery_task

    # --- Share service ---
    share_service = ShareService()

    from backend.services.tracker_sync_service import TrackerSyncService

    tracker_sync_service = TrackerSyncService(
        session_factory=session_factory,
    )
    tracker_sync_service.start()

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
            SidecarSessionManager: services.sidecar_sessions,
            SidecarDispatcher: services.sidecar_dispatcher,
            NarratorCompleter: services.narrator_completer,
            VoiceService: optional.voice_service,
            CachedModelsBySdk: CachedModelsBySdk(optional.cached_models_by_sdk),
            VoiceMaxBytes: VoiceMaxBytes(optional.voice_max_bytes),
            PushService: push_service,
            ShareService: share_service,
            TrailService: trail_service,
            TerminalService: optional.terminal_service,
            IngestService: ingest_service,
            ModelPricingService: model_pricing_service,
            SessionStateWatcher: session_state_watcher,
            ClaudeSessionStateWatcher: claude_session_watcher,
            TrackerSyncService: tracker_sync_service,
            TrackerWriteService: services.tracker_write_service,
            TaskExecutionService: services.task_execution_service,
        },
    )
    app.state.dishka_container = container

    # Activate the Rich live display — connection info is shown
    # persistently in the dashboard header.  Print the QR code once
    # before the live display takes over the terminal.
    banner_args = getattr(app.state, "banner_args", None)
    if banner_args:
        tunnel_url = banner_args.get("tunnel_url")
        local_url = f"http://{banner_args.get('host', '127.0.0.1')}:{banner_args.get('port', 8080)}"
        _print_qr_code(tunnel_url or local_url)
    if dashboard is not None:
        if banner_args:
            host = banner_args.get("host", "127.0.0.1")
            port = banner_args.get("port", 8080)
            dashboard.set_server_info(
                server_url=f"http://{host}:{port}",
                tunnel_url=banner_args.get("tunnel_url"),
                password=banner_args.get("password"),
            )
        dashboard.start()

    # --- Deferred Cloudflare Access check ---
    # The probe goes through the tunnel to the origin, so it can only run
    # after the server is accepting connections.  Scheduled as a background
    # task — it runs once the event loop resumes after yield.
    tunnel_handle = getattr(app.state, "tunnel_handle", None)
    remote_check_task: asyncio.Task[Any] | None = None
    if tunnel_handle is not None and tunnel_handle.origin and tunnel_handle.provider.value == "cloudflare":
        remote_check_task = _fire_and_forget(
            _deferred_cloudflare_access_check(tunnel_handle, app),
            name="cloudflare-access-check",
        )

    # --- Restart readiness marker (SPEC.md CAP-5) ---
    # Only present on a restart-triggered launch — the helper sets this on
    # the replacement process's environment before exec. Absent on every
    # normal ``cpl up`` startup, so the lazy restart-protocol import above
    # is never reached outside an actual restart.
    restart_request_id = os.environ.get(_RESTART_REQUEST_ID_ENV)
    if restart_request_id:
        _fire_and_forget(
            _publish_restart_readiness(restart_request_id, recovery_task, remote_check_task),
            name="publish-restart-readiness",
        )

    yield

    # Shutdown in reverse initialisation order.
    # Wrap the entire sequence so individual teardown failures don't produce
    # cascading tracebacks visible to the operator (they still get logged).
    # Stop the live dashboard first so subsequent log output prints cleanly.
    if dashboard is not None:
        dashboard.stop()

    async def _quiet_shutdown() -> None:
        """Run teardown steps, suppressing noisy exceptions."""
        await services.tracker_write_service.close()
        await tracker_sync_service.stop()
        await container.close()
        optional.mcp_stop_event.set()
        await optional.mcp_task
        optional.retention_task.cancel()
        motivation_task.cancel()
        trail_task.cancel()
        dead_letter_task.cancel()
        if optional.terminal_service is not None:
            await optional.terminal_service.shutdown()
        # Stop SessionStateWatcher and steer client
        await session_state_watcher.stop()
        await claude_session_watcher.stop()
        await model_pricing_service.stop()
        if steer_client is not None:
            await steer_client.close()
        # Drain any in-flight ephemeral background tasks before tearing down services.
        if _ephemeral_tasks:
            await asyncio.gather(*_ephemeral_tasks, return_exceptions=True)
        await services.sidecar_dispatcher.shutdown()
        await services.sidecar_sessions.shutdown()
        await services.runtime_service.shutdown()
        await asyncio.gather(*(adapter.shutdown() for adapter in services.adapter_registry._adapters.values()))
        # Flush event processor (global enricher + title pipeline drain) AFTER
        # runtime stops pushing events. Safe because no new push() calls occur.
        await event_processor.shutdown()
        sse_manager.close_all()
        await engine.dispose()

    try:
        await asyncio.wait_for(_quiet_shutdown(), timeout=45.0)
    except TimeoutError:
        log.error("shutdown_timeout", msg="Shutdown exceeded its 45s resource-drain deadline")
    except (asyncio.CancelledError, Exception) as exc:
        log.debug("shutdown_interrupted", error=str(exc))
