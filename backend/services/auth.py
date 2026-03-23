"""Password-based authentication for CodePlane.

Auth architecture
-----------------
Authentication is enforced via **Starlette HTTP middleware** registered in
``app_factory.py``.  When a password is configured (tunnel mode or explicit
``--password`` / ``CODEPLANE_PASSWORD``), *every* incoming HTTP request passes
through ``auth_middleware`` before reaching any route handler.

**Exempt paths** (no session cookie required):
- ``/api/auth/*`` — login endpoint itself
- ``/api/health``  — health-check probe
- Static frontend assets served by the SPA fallback handler

**Localhost bypass**: requests originating from ``127.0.0.1``, ``::1``, or
``localhost`` are unconditionally trusted and never challenged.  This allows
same-machine tools and CLIs to access the API without credentials.

**WebSocket auth**: WebSocket upgrades are *not* wrapped by the HTTP
middleware (Starlette handles them on a different code-path).  Instead, each
WebSocket endpoint calls ``check_websocket_auth`` at connect time, passing the
client host and cookies extracted from the upgrade request.  The logic mirrors
the middleware — localhost is trusted, otherwise a valid ``cpl_session`` cookie
is required.

**Session tokens**: on successful login a cryptographic token is generated,
stored server-side in ``_session_tokens``, and returned to the browser as an
``httpOnly`` cookie.  Tokens expire after ``SESSION_TTL`` seconds (default
24 h); stale tokens are purged lazily during validation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import urlparse

import structlog
from starlette.requests import Request  # noqa: TC002
from starlette.responses import HTMLResponse, JSONResponse, Response  # noqa: TC002

log = structlog.get_logger()

COOKIE_NAME = "cpl_session"
LOCALHOST_ADDRS = {"127.0.0.1", "::1", "localhost"}

# Rate limiting: track failed attempts per IP
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 5  # max attempts per window

# Session token TTL (seconds).  Tokens older than this are rejected and purged.
SESSION_TTL: float = 86400  # 24 hours

# Active session tokens: token → creation timestamp (monotonic clock)
_session_tokens: dict[str, float] = {}

# The password hash and salt (set during startup)
_password_hash: bytes | None = None
_password_salt: bytes | None = None

# Load logo as base64 for the login page — deferred to first use
_logo_path = Path(__file__).resolve().parent.parent.parent / "docs" / "images" / "logo.png"
_logo_b64: str | None = None


def _get_logo_b64() -> str:
    """Return base64-encoded logo, reading from disk on first call."""
    global _logo_b64  # noqa: PLW0603
    if _logo_b64 is None:
        _logo_b64 = base64.b64encode(_logo_path.read_bytes()).decode() if _logo_path.is_file() else ""
    return _logo_b64


def set_password(password: str) -> None:
    """Set the password for this server instance."""
    global _password_hash, _password_salt  # noqa: PLW0603
    _password_salt = secrets.token_bytes(16)
    _password_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), _password_salt, iterations=600_000)


def generate_password() -> str:
    """Generate a secure random password."""
    return secrets.token_urlsafe(16)


def _check_password(password: str) -> bool:
    """Constant-time password comparison using PBKDF2."""
    if _password_hash is None or _password_salt is None:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), _password_salt, iterations=600_000)
    return hmac.compare_digest(candidate, _password_hash)


def _is_rate_limited(ip: str) -> bool:
    """Check if an IP has exceeded the login rate limit."""
    now = time.monotonic()
    attempts = _login_attempts[ip]
    # Prune old attempts
    _login_attempts[ip] = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    return len(_login_attempts[ip]) >= _RATE_LIMIT_MAX


def _record_attempt(ip: str) -> None:
    """Record a login attempt for rate limiting."""
    _login_attempts[ip].append(time.monotonic())


def _cleanup_expired_tokens() -> None:
    """Remove tokens whose age exceeds ``SESSION_TTL``."""
    now = time.monotonic()
    expired = [t for t, created_at in _session_tokens.items() if now - created_at > SESSION_TTL]
    for t in expired:
        del _session_tokens[t]


def _create_session_token() -> str:
    """Create a new session token and record its creation time."""
    token = secrets.token_hex(32)
    _session_tokens[token] = time.monotonic()
    return token


def is_valid_token(token: str | None) -> bool:
    """Check if a session token is valid and not expired.

    Lazily cleans up expired tokens on each call.
    """
    if not token:
        return False
    _cleanup_expired_tokens()
    return token in _session_tokens


def invalidate_session(token: str | None) -> bool:
    """Remove a session token, returning True if it existed."""
    if not token:
        return False
    return _session_tokens.pop(token, None) is not None


def is_localhost(request: Request) -> bool:
    """Check if the request comes from localhost (trusted)."""
    client = request.client
    if client is None:
        return False
    host = client.host
    return host in LOCALHOST_ADDRS


def is_request_authenticated(request: Request) -> bool:
    """Check if a request is authenticated via localhost or valid session cookie.

    Returns True when auth is not enabled, the request is from localhost,
    or the request carries a valid session cookie.
    """
    if not is_password_auth_enabled():
        return True
    if is_localhost(request):
        return True
    return is_valid_token(request.cookies.get(COOKIE_NAME))


def is_password_auth_enabled() -> bool:
    """Return True when the server has password authentication configured."""
    return _password_hash is not None


def check_websocket_auth(*, client_host: str | None, cookies: dict[str, str]) -> bool:
    """Validate authentication for a WebSocket connection.

    Mirrors the HTTP middleware logic: if password auth is not enabled
    everyone is allowed; localhost is trusted; otherwise a valid
    ``cpl_session`` cookie is required.
    """
    if not is_password_auth_enabled():
        return True
    if client_host and client_host in LOCALHOST_ADDRS:
        return True
    return is_valid_token(cookies.get(COOKIE_NAME))


_LOGIN_HTML_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "login.html"
_LOGIN_HTML: str | None = None


def _get_login_html() -> str:
    """Return the rendered login HTML page, reading template on first call."""
    global _LOGIN_HTML  # noqa: PLW0603
    if _LOGIN_HTML is None:
        template = Template(_LOGIN_HTML_TEMPLATE_PATH.read_text())
        _LOGIN_HTML = template.safe_substitute(logo_b64=_get_logo_b64())
    return _LOGIN_HTML


def _header_indicates_https(value: str | None) -> bool:
    """Return True when a forwarded header value indicates HTTPS transport."""
    if not value:
        return False

    for part in value.split(","):
        normalized = part.strip().lower()
        if not normalized:
            continue
        if normalized == "https":
            return True
        if "proto=https" in normalized:
            return True
    return False


def _origin_uses_https(value: str | None) -> bool:
    """Return True when an Origin/Referer header points at an HTTPS URL."""
    if not value:
        return False

    with __import__("contextlib").suppress(ValueError):
        return urlparse(value).scheme.lower() == "https"
    return False


def _is_https_request(request: Request) -> bool:
    """Best-effort HTTPS detection for deployments behind a tunnel relay."""
    if request.url.scheme == "https":
        return True

    headers = request.headers
    if _header_indicates_https(headers.get("x-forwarded-proto")):
        return True
    if _header_indicates_https(headers.get("forwarded")):
        return True
    if _origin_uses_https(headers.get("origin")):
        return True
    if _origin_uses_https(headers.get("referer")):
        return True

    host = (headers.get("x-forwarded-host") or headers.get("host") or "").split(":", 1)[0].lower()
    return host.endswith(".devtunnels.ms")


def _client_ip(request: Request) -> str:
    """Extract the real client IP, respecting X-Forwarded-For behind tunnels."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First entry is the original client
        ip = forwarded.split(",")[0].strip()
        if ip:
            return ip
    return request.client.host if request.client else "unknown"


async def authenticate_login_request(request: Request) -> Response:
    """Handle POST /api/auth/login — validate password, set cookie."""
    ip = _client_ip(request)

    if _is_rate_limited(ip):
        log.warning("auth_login_rate_limited", client_ip=ip)
        return JSONResponse({"detail": "Too many attempts. Try again in a minute."}, status_code=429)

    try:
        body = await request.json()
        password = body.get("password", "")
    except Exception:
        return JSONResponse({"detail": "Invalid request"}, status_code=400)

    if not _check_password(password):
        _record_attempt(ip)
        log.warning("auth_login_failed", client_ip=ip)
        return JSONResponse({"detail": "Invalid password"}, status_code=401)

    token = _create_session_token()
    log.info("auth_login_success", client_ip=ip)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_is_https_request(request),
        max_age=86400,  # 24 hours
        path="/",
    )
    return response


async def authenticate_logout_request(request: Request) -> Response:
    """Handle POST /api/auth/logout — invalidate session cookie."""
    token = request.cookies.get(COOKIE_NAME)
    invalidate_session(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(key=COOKIE_NAME, path="/")
    log.info("auth_logout", client_ip=_client_ip(request))
    return response


async def auth_middleware(request: Request, call_next: Any) -> Response:
    """Middleware that enforces password auth when enabled.

    Registered by ``app_factory._configure_middleware`` when a password is set.
    All HTTP requests (except WebSocket upgrades, which are handled separately
    by ``check_websocket_auth``) pass through this middleware.

    **Bypass rules** (checked in order):

    1. ``/api/auth/*`` and ``/api/health`` — always allowed so the login
       endpoint and health probe remain reachable.
    2. Localhost (``127.0.0.1``, ``::1``, ``localhost``) — trusted, no cookie
       needed.
    3. Valid ``cpl_session`` cookie — normal authenticated browser session.

    If none of the above match, API/MCP routes receive a 401 JSON response and
    browser requests receive the login HTML page.
    """
    path = request.url.path

    # Always allow auth endpoints and health check
    if path.startswith("/api/auth/") or path == "/api/health":
        return await call_next(request)  # type: ignore[no-any-return]

    # Localhost is trusted — no auth needed
    if is_localhost(request):
        client_ip = request.client.host if request.client else "unknown"
        log.debug("auth_localhost_bypass", client_ip=client_ip, path=path)
        return await call_next(request)  # type: ignore[no-any-return]

    # Check session cookie
    token = request.cookies.get(COOKIE_NAME)
    if is_valid_token(token):
        log.debug("auth_token_valid", path=path)
        return await call_next(request)  # type: ignore[no-any-return]

    # Not authenticated
    if path.startswith("/api") or path.startswith("/mcp"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    # Browser request — serve login page
    return HTMLResponse(_get_login_html(), status_code=401)
