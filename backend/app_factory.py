"""FastAPI application factory for CodePlane.

Creates and configures the FastAPI app with middleware, route registration,
and static file serving.  Delegates lifecycle management to ``lifespan.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dishka.integrations.fastapi import ContainerMiddleware  # type: ignore[attr-defined]
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

from backend import __version__
from backend.api import analytics, approvals, artifacts, events, health, jobs, settings, terminal, voice, workspace
from backend.lifespan import lifespan
from backend.services.agent_adapter import SDKModelMismatchError
from backend.services.approval_service import ApprovalAlreadyResolvedError, ApprovalNotFoundError
from backend.services.job_service import JobNotFoundError, RepoNotAllowedError, StateConflictError

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _configure_middleware(
    app: FastAPI,
    *,
    dev: bool,
    tunnel_origin: str | None,
    password: str | None,
) -> None:
    """Configure CORS and authentication middleware.

    **Auth model**: when *password* is provided (tunnel mode or explicit
    ``--password``), an HTTP middleware gate is installed that protects every
    route by default.  Only the following paths are exempt:

    - ``/api/auth/*`` — the login endpoint itself
    - ``/api/health`` — liveness/readiness probe
    - ``/api/events`` — SSE stream (checked inline, not via
      ``BaseHTTPMiddleware``, because middleware buffering kills SSE)
    - Localhost requests (``127.0.0.1``, ``::1``) — same-machine access is
      unconditionally trusted

    Static frontend assets are served by the SPA fallback 404 handler
    registered in ``_mount_spa_fallback`` and are not affected by this
    middleware (they sit outside ``/api``).

    **WebSocket endpoints** are *not* protected by this middleware — Starlette
    dispatches WebSocket upgrades before HTTP middleware runs.  WS routes must
    call ``check_websocket_auth`` themselves at connect time.
    """
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
        from backend.services.auth import (
            auth_middleware,
            authenticate_login_request,
            authenticate_logout_request,
            is_request_authenticated,
            set_password,
        )

        set_password(password)

        from starlette.routing import Route

        app.routes.insert(0, Route("/api/auth/login", authenticate_login_request, methods=["POST"]))
        app.routes.insert(1, Route("/api/auth/logout", authenticate_logout_request, methods=["POST"]))

        @app.middleware("http")
        async def _auth_gate(request: Request, call_next: Callable[..., Awaitable[Response]]) -> Response:
            # SSE must bypass middleware wrapping — BaseHTTPMiddleware
            # buffers streaming responses and kills the connection.
            if request.url.path == "/api/events":
                if not is_request_authenticated(request):
                    return JSONResponse({"detail": "Authentication required"}, status_code=401)
                return await call_next(request)
            return await auth_middleware(request, call_next)


def _register_routes(app: FastAPI) -> None:
    """Register all API routers."""
    app.include_router(health.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(workspace.router, prefix="/api")
    app.include_router(voice.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    # Terminal router has its own /api/terminal prefix
    app.include_router(terminal.router)


def _register_domain_exception_handlers(app: FastAPI) -> None:
    """Map domain exceptions to HTTP error responses centrally."""

    @app.exception_handler(JobNotFoundError)
    async def _job_not_found(request: Request, exc: JobNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(StateConflictError)
    async def _state_conflict(request: Request, exc: StateConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RepoNotAllowedError)
    async def _repo_not_allowed(request: Request, exc: RepoNotAllowedError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ApprovalNotFoundError)
    async def _approval_not_found(request: Request, exc: ApprovalNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ApprovalAlreadyResolvedError)
    async def _approval_already_resolved(request: Request, exc: ApprovalAlreadyResolvedError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(SDKModelMismatchError)
    async def _sdk_model_mismatch(request: Request, exc: SDKModelMismatchError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


def _mount_spa_fallback(app: FastAPI) -> None:
    """Serve frontend static files (SPA fallback for client-side routing).

    Uses exception handler instead of middleware to avoid wrapping
    streaming responses (middleware breaks SSE).
    """
    if not _FRONTEND_DIR.is_dir():
        return

    from starlette.responses import FileResponse

    _index_html = str(_FRONTEND_DIR / "index.html")

    @app.exception_handler(404)
    async def _spa_fallback(request: Request, exc: Exception) -> Response:
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


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_app(*, dev: bool = False, tunnel_origin: str | None = None, password: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="CodePlane", version=__version__, lifespan=lifespan)

    app.add_middleware(ContainerMiddleware)
    _configure_middleware(app, dev=dev, tunnel_origin=tunnel_origin, password=password)
    _register_routes(app)
    _register_domain_exception_handlers(app)
    _mount_spa_fallback(app)

    return app
