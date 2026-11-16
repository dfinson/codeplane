"""FastAPI application factory for CodePlane.

Creates and configures the FastAPI app with middleware, route registration,
and static file serving.  Delegates lifecycle management to ``lifespan.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from dishka.integrations.fastapi import (  # type: ignore[attr-defined]
    ContainerMiddleware,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

from backend import __version__
from backend.api import (
    analytics,
    approvals,
    artifacts,
    chats,
    credentials,
    events,
    health,
    hooks,
    job_artifacts,
    job_telemetry,
    jobs,
    metrics,
    notifications,
    policy_settings,
    preview,
    projects,
    recipe_runs,
    recipes,
    settings,
    share,
    sidecar_templates,
    terminal,
    tracker_links,
    trail,
    utility_sessions,
    voice,
    workspace,
)
from backend.lifespan import lifespan
from backend.models.domain import (
    ApprovalAlreadyResolvedError,
    ApprovalNotFoundError,
    CodePlaneError,
    InvalidStateTransitionError,
    JobNotFoundError,
    ProjectNotFoundError,
    RepoAlreadyAssignedError,
    RepoNotAllowedError,
    SDKModelMismatchError,
    StateConflictError,
    TaskLinkNotFoundError,
)
from backend.services.recipe.definition_service import RecipeNotFoundError, RecipeValidationError
from backend.services.sharing.share_service import InvalidShareTokenError

_FRONTEND_DIR = Path(__file__).resolve().parent / "web"

# Allowed WebSocket origins — populated during middleware configuration so the
# terminal WS endpoint can validate the Origin header.
_allowed_ws_origins: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def get_allowed_ws_origins() -> frozenset[str]:
    """Return the set of allowed origins for WebSocket connections."""
    return _allowed_ws_origins


def _configure_middleware(
    app: FastAPI,
    *,
    dev: bool,
    tunnel_origin: str | None,
    password: str | None,
    remote: bool | None = None,
    access_only: bool = False,
) -> None:
    """Configure security headers, CORS, and authentication middleware.

    **Security headers**: a lightweight ``@app.middleware("http")`` adds
    standard hardening headers (``X-Content-Type-Options``,
    ``X-Frame-Options``, ``Content-Security-Policy``, ``Referrer-Policy``,
    ``Cache-Control``) to every response.

    **Auth model**: remote posture or an explicit password installs the gate
    before requests can arrive. Tunnel verification never changes posture.
    Health/login bootstrap and exact read-only share routes are exempt;
    share handlers validate their own scoped tokens. Remote requests cannot
    use loopback locality as a credential. SSE authenticates before streaming.

    **WebSocket endpoints** are *not* protected by this middleware — Starlette
    dispatches WebSocket upgrades before HTTP middleware runs.  WS routes must
    call ``check_websocket_auth`` themselves at connect time.
    """
    origins: list[str] = []
    if dev:
        origins.append("http://localhost:5173")
    if tunnel_origin:
        origins.append(tunnel_origin)

    # Populate allowed WS origins — localhost variants are always allowed;
    # configured CORS origins are added so the terminal WS endpoint can
    # validate the Origin header on upgrade requests.
    global _allowed_ws_origins  # noqa: PLW0603
    ws_origins = set(origins)
    ws_origins.add("http://localhost:8080")
    ws_origins.add("http://127.0.0.1:8080")
    _allowed_ws_origins = frozenset(ws_origins)

    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
            allow_headers=["Content-Type", "Authorization", "Last-Event-ID"],
        )

    # --- Security headers middleware ---
    csp_parts = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
    ]
    if dev:
        # Allow Vite HMR WebSocket and hot-update fetches
        csp_parts.append("connect-src 'self' ws://localhost:5173 http://localhost:5173")
    if tunnel_origin:
        parsed = urlparse(tunnel_origin)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        ws_origin = f"{ws_scheme}://{parsed.netloc}"
        csp_parts.append(f"connect-src 'self' {ws_origin} {tunnel_origin}")
    csp_value = "; ".join(csp_parts)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Callable[..., Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = csp_value
        # Don't override Cache-Control for SSE or static assets that set their own
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    from backend.services.auth.middleware import configure_auth

    policy = configure_auth(
        remote=bool(tunnel_origin) if remote is None else remote,
        access_only=access_only,
        password=password,
    )
    app.state.auth_policy = policy
    if policy.enabled:
        from starlette.routing import Route

        from backend.services.auth.middleware import (
            auth_middleware,
            authenticate_login_request,
            authenticate_logout_request,
            is_request_authenticated,
        )

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
    app.include_router(job_artifacts.router, prefix="/api")
    app.include_router(job_telemetry.router, prefix="/api")
    app.include_router(utility_sessions.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(hooks.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(workspace.router, prefix="/api")
    app.include_router(voice.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    app.include_router(notifications.router, prefix="/api")
    app.include_router(terminal.router, prefix="/api")
    # Port preview proxy
    app.include_router(preview.router, prefix="/api")
    # Job sharing (read-only via share tokens)
    app.include_router(share.router, prefix="/api")
    # Agent audit trail
    app.include_router(trail.router, prefix="/api")
    # Action policy settings
    app.include_router(policy_settings.router, prefix="/api")
    app.include_router(credentials.router, prefix="/api")
    app.include_router(metrics.router, prefix="/api")
    app.include_router(sidecar_templates.router, prefix="/api")
    app.include_router(chats.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(tracker_links.router, prefix="/api")
    app.include_router(recipes.router, prefix="/api")
    app.include_router(recipe_runs.router, prefix="/api")


def _register_domain_exception_handlers(app: FastAPI) -> None:
    """Map domain exceptions to HTTP error responses centrally."""

    @app.exception_handler(RecipeNotFoundError)
    async def _recipe_not_found(request: Request, exc: RecipeNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(RecipeValidationError)
    async def _recipe_invalid(request: Request, exc: RecipeValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.result.model_dump(mode="json", by_alias=True)})

    @app.exception_handler(JobNotFoundError)
    async def _job_not_found(request: Request, exc: JobNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(StateConflictError)
    async def _state_conflict(request: Request, exc: StateConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RepoNotAllowedError)
    async def _repo_not_allowed(request: Request, exc: RepoNotAllowedError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ProjectNotFoundError)
    async def _project_not_found(request: Request, exc: ProjectNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(TaskLinkNotFoundError)
    async def _task_link_not_found(request: Request, exc: TaskLinkNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(RepoAlreadyAssignedError)
    async def _repo_already_assigned(request: Request, exc: RepoAlreadyAssignedError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ApprovalNotFoundError)
    async def _approval_not_found(request: Request, exc: ApprovalNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ApprovalAlreadyResolvedError)
    async def _approval_already_resolved(request: Request, exc: ApprovalAlreadyResolvedError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(SDKModelMismatchError)
    async def _sdk_model_mismatch(request: Request, exc: SDKModelMismatchError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(InvalidStateTransitionError)
    async def _invalid_state_transition(request: Request, exc: InvalidStateTransitionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InvalidShareTokenError)
    async def _invalid_share_token(request: Request, exc: InvalidShareTokenError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Invalid or expired share link"})

    # Fallback: catch any unhandled CodePlaneError subclass so new domain
    # errors produce a structured JSON response instead of a bare 500.
    @app.exception_handler(CodePlaneError)
    async def _domain_error_fallback(request: Request, exc: CodePlaneError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})


def _mount_spa_fallback(app: FastAPI) -> None:
    """Serve frontend static files (SPA fallback for client-side routing).

    Uses exception handler instead of middleware to avoid wrapping
    streaming responses (middleware breaks SSE).
    """
    if not _FRONTEND_DIR.is_dir():
        return

    from starlette.responses import FileResponse

    _index_html = str(_FRONTEND_DIR / "index.html")
    _no_cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }

    @app.exception_handler(404)
    async def _spa_fallback(request: Request, exc: Exception) -> Response:
        path = request.url.path
        if (
            request.method in ("GET", "HEAD")
            and not path.startswith(("/api", "/mcp", "/assets"))
            and "\x00" not in path
            and ".." not in path
        ):
            # Serve root-level static files (favicon, logo, etc.)
            candidate = _FRONTEND_DIR / path.lstrip("/")
            if candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(_index_html, headers=_no_cache_headers)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR / "assets")), name="static-assets")


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    dev: bool = False,
    tunnel_origin: str | None = None,
    password: str | None = None,
    remote: bool | None = None,
    access_only: bool = False,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="CodePlane", version=__version__, lifespan=lifespan)

    app.add_middleware(ContainerMiddleware)
    _configure_middleware(
        app, dev=dev, tunnel_origin=tunnel_origin, password=password, remote=remote, access_only=access_only
    )
    _register_routes(app)
    _register_domain_exception_handlers(app)
    _mount_spa_fallback(app)

    return app
