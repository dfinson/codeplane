"""FastAPI application factory and CLI entry point."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api import approvals, artifacts, events, health, jobs, settings, terminal, voice, workspace
from backend.config import MCP_PATH, VOICE_MAX_AUDIO_SIZE_MB, load_config
from backend.persistence.database import create_engine, create_session_factory, run_migrations
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

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.models.events import DomainEvent

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

_LOG_LEVEL_MAP: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_CONSOLE_NOISE_PREFIXES: tuple[str, ...] = (
    "alembic",
    "uvicorn.access",
    "uvicorn.error",
    "mcp.server.streamable_http_manager",
    "backend.services.sse_manager",
    "backend.services.voice_service",
    "backend.services.utility_session",
)


class _ConsoleNoiseFilter(logging.Filter):
    """Keep warnings/errors on console while suppressing chatty info logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return not any(record.name.startswith(prefix) for prefix in _CONSOLE_NOISE_PREFIXES)


def setup_logging(log_file: str, console_level: str = "info") -> None:
    """Configure structlog + stdlib logging.

    Strategy
    --------
    * **File handler** — always at DEBUG verbosity so every log line is
      persisted.  Uses a rotating handler (10 MB × 5 backups).
    * **Stderr handler** — respects ``console_level`` from config (default
      info) so the terminal stays readable at runtime.
    * **structlog** — uses the same stdlib handlers so all structured context
      fields are serialised consistently.
    """
    log_path = Path(log_file).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    console_int = _LOG_LEVEL_MAP.get(console_level.lower(), logging.INFO)

    # Shared formatter — human-readable key=value pairs
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # File handler: DEBUG, rotating 10 MB × 5
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    # Stderr handler: configured level
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_int)
    console_handler.setFormatter(fmt)
    console_handler.addFilter(_ConsoleNoiseFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # let handlers decide what to suppress
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Suppress chatty third-party loggers from polluting the debug file
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


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

    # Store on app.state for access from route handlers
    app.state.event_bus = event_bus
    app.state.sse_manager = sse_manager
    app.state.runtime_service = runtime_service
    app.state.merge_service = merge_service
    app.state.platform_registry = platform_registry
    app.state.approval_service = approval_service
    app.state.adapter_registry = adapter_registry
    app.state.utility_session = utility_session
    app.state.session_factory = session_factory

    # --- Terminal service ---
    from backend.services.terminal_service import TerminalService

    terminal_service: TerminalService | None = None
    if config.terminal.enabled:
        terminal_service = TerminalService(
            max_sessions=config.terminal.max_sessions,
            default_shell=config.terminal.default_shell,
            scrollback_size_kb=config.terminal.scrollback_size_kb,
        )
        terminal.set_terminal_service(terminal_service)
        terminal.set_utility_session(utility_session)
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
        log.warning("model_cache_failed", error=str(exc))
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

    # Session factory available for route handlers that need ad-hoc sessions
    app.state.session_factory = session_factory

    # --- MCP server ---
    from backend.mcp.server import create_mcp_server

    mcp_server = create_mcp_server(
        session_factory=session_factory,
        runtime_service=runtime_service,
        approval_service=approval_service,
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
    await mcp_cleanup.__aexit__(None, None, None)
    retention_task.cancel()
    if terminal_service is not None:
        await terminal_service.shutdown()
    await utility_session.shutdown()
    await runtime_service.shutdown()
    await sse_manager.close_all()
    app.dependency_overrides.clear()
    await engine.dispose()


def create_app(*, dev: bool = False, tunnel_origin: str | None = None, password: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="CodePlane", version="0.1.0", lifespan=_lifespan)

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
            # SSE must bypass middleware wrapping — BaseHTTPMiddleware
            # buffers streaming responses and kills the connection.
            if request.url.path == "/api/events":
                # Check auth inline without wrapping
                from backend.services.auth import _is_localhost, _is_valid_token

                if not _is_localhost(request):
                    token = request.cookies.get("cpl_session")
                    if not _is_valid_token(token):
                        return JSONResponse({"detail": "Authentication required"}, status_code=401)
                return await call_next(request)
            return await auth_middleware(request, call_next)

    app.include_router(health.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(workspace.router, prefix="/api")
    app.include_router(voice.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    # Terminal router has its own /api/terminal prefix
    app.include_router(terminal.router)

    # Serve frontend static files (SPA fallback for client-side routing)
    # Uses exception handler instead of middleware to avoid wrapping
    # streaming responses (middleware breaks SSE).
    if _FRONTEND_DIR.is_dir():
        from starlette.responses import FileResponse

        _index_html = str(_FRONTEND_DIR / "index.html")

        @app.exception_handler(404)
        async def _spa_fallback(request: Any, exc: Any) -> Any:
            path = request.url.path
            if (
                request.method in ("GET", "HEAD")
                and not path.startswith(("/api", "/mcp"))
                and "\x00" not in path
                and ".." not in path
            ):
                # Serve root-level static files (favicon, logo, etc.)
                candidate = _FRONTEND_DIR / path.lstrip("/")
                if candidate.is_file():
                    return FileResponse(str(candidate))
                return FileResponse(_index_html)
            return JSONResponse({"detail": "Not found"}, status_code=404)

        app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR / "assets")), name="static-assets")

    return app


@click.group()
def cli() -> None:
    """CodePlane — control plane for coding agents."""


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
        from backend.services.setup_service import run_preflight

        if not run_preflight(port):
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

    def __init__(self, *, tunnel_url: str, tunnel_name: str, port: int, proc: Any) -> None:
        self.tunnel_url = tunnel_url
        self.tunnel_name = tunnel_name
        self.port = port
        self.proc = proc
        self._stop_event: Any = __import__("threading").Event()
        self._thread: Any = None

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
            return False

    def _restart_host(self) -> None:
        """Kill the current devtunnel host and start a fresh one."""
        import subprocess

        log.warning("tunnel_watchdog_restarting", tunnel=self.tunnel_name)

        # Kill the old process
        import contextlib

        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
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
                log.warning(
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


def _start_tunnel(port: int) -> tuple[str | None, Any]:
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
            pass

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
        pass


@cli.command()
def version() -> None:
    """Print CodePlane version."""
    click.echo("cpl 0.1.0")


@cli.command()
def setup() -> None:
    """Interactive setup wizard — check dependencies, configure data directory, authenticate."""
    from backend.services.setup_service import run_setup

    run_setup()


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def doctor(as_json: bool) -> None:
    """Full non-interactive health check — deps, auth, SDK, environment."""
    from backend.services.setup_service import run_doctor

    ok = run_doctor(as_json=as_json)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
