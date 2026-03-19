"""Password-based authentication for CodePlane.

Implements TermBeam-style auth:
- Password auto-generated or set via --password / CODEPLANE_PASSWORD
- httpOnly cookie sessions with 24h expiry
- Rate-limited login endpoint (5 attempts/min/IP)
- Localhost requests bypass auth (same-machine access is trusted)
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

from starlette.requests import Request  # noqa: TC002
from starlette.responses import HTMLResponse, JSONResponse, Response  # noqa: TC002

COOKIE_NAME = "cpl_session"
LOCALHOST_ADDRS = {"127.0.0.1", "::1", "localhost"}

# Rate limiting: track failed attempts per IP
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 5  # max attempts per window

# Active session tokens
_session_tokens: set[str] = set()

# The password hash (set during startup)
_password_hash: str | None = None

# Load logo as base64 for the login page — deferred to first use
_logo_path = Path(__file__).resolve().parent.parent.parent / "docs" / "images" / "logo.png"
_logo_b64: str | None = None


def _get_logo_b64() -> str:
    """Return base64-encoded logo, reading from disk on first call."""
    global _logo_b64  # noqa: PLW0603
    if _logo_b64 is None:
        if _logo_path.is_file():
            _logo_b64 = base64.b64encode(_logo_path.read_bytes()).decode()
        else:
            _logo_b64 = ""
    return _logo_b64


def set_password(password: str) -> None:
    """Set the password for this server instance."""
    global _password_hash  # noqa: PLW0603
    _password_hash = hashlib.sha256(password.encode()).hexdigest()


def generate_password() -> str:
    """Generate a secure random password."""
    return secrets.token_urlsafe(16)


def _check_password(password: str) -> bool:
    """Constant-time password comparison."""
    if _password_hash is None:
        return False
    candidate = hashlib.sha256(password.encode()).hexdigest()
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


def _create_session_token() -> str:
    """Create a new session token."""
    token = secrets.token_hex(32)
    _session_tokens.add(token)
    return token


def is_valid_token(token: str | None) -> bool:
    """Check if a session token is valid."""
    if not token:
        return False
    return token in _session_tokens


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
    return is_valid_token(request.cookies.get("cpl_session"))


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
    if client_host and client_host in ("127.0.0.1", "::1", "localhost"):
        return True
    return is_valid_token(cookies.get("cpl_session"))


_LOGIN_HTML_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "login.html"
_LOGIN_HTML: str | None = None


def _get_login_html() -> str:
    """Return the rendered login HTML page, reading template on first call."""
    global _LOGIN_HTML  # noqa: PLW0603
    if _LOGIN_HTML is None:
        template = Template(_LOGIN_HTML_TEMPLATE_PATH.read_text())
        _LOGIN_HTML = template.safe_substitute(logo_b64=_get_logo_b64())
    return _LOGIN_HTML


async def authenticate_login_request(request: Request) -> Response:
    """Handle POST /api/auth/login — validate password, set cookie."""
    ip = request.client.host if request.client else "unknown"

    if _is_rate_limited(ip):
        return JSONResponse({"detail": "Too many attempts. Try again in a minute."}, status_code=429)

    try:
        body = await request.json()
        password = body.get("password", "")
    except Exception:
        return JSONResponse({"detail": "Invalid request"}, status_code=400)

    if not _check_password(password):
        _record_attempt(ip)
        return JSONResponse({"detail": "Invalid password"}, status_code=401)

    token = _create_session_token()
    response = JSONResponse({"ok": True})
    # Detect HTTPS: check scheme, x-forwarded-proto, or devtunnel headers
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
        or ".devtunnels.ms" in request.headers.get("host", "")
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=is_https,
        max_age=86400,  # 24 hours
        path="/",
    )
    return response


async def auth_middleware(request: Request, call_next: Any) -> Response:
    """Middleware that enforces password auth when enabled.

    - Localhost requests are always allowed (trusted)
    - /api/auth/* and /api/health are always allowed
    - Static assets are always allowed
    - Everything else requires a valid session cookie
    """
    path = request.url.path

    # Always allow auth endpoints and health check
    if path.startswith("/api/auth/") or path == "/api/health":
        return await call_next(request)  # type: ignore[no-any-return]

    # Localhost is trusted — no auth needed
    if is_localhost(request):
        return await call_next(request)  # type: ignore[no-any-return]

    # Check session cookie
    token = request.cookies.get(COOKIE_NAME)
    if is_valid_token(token):
        return await call_next(request)  # type: ignore[no-any-return]

    # Not authenticated
    if path.startswith("/api") or path.startswith("/mcp"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    # Browser request — serve login page
    return HTMLResponse(_get_login_html(), status_code=401)
