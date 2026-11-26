"""Application authentication, independent of tunnel reachability checks.

Remote posture is fixed at application construction and never trusts a
loopback peer: tunnel connectors use loopback too. HTTP, SSE and WebSocket
upgrades use the same session/JWT policy. Access-only disallows password
sessions. Direct-local mode preserves same-machine trust.

Internal HTTP calls use a process-only credential for the explicitly bound
loopback endpoint. It is not a browser session and is never persisted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import structlog
from starlette.requests import Request  # noqa: TC002
from starlette.responses import HTMLResponse, JSONResponse, Response  # noqa: TC002

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = structlog.get_logger()

COOKIE_NAME = "cpl_session"
LOCALHOST_ADDRS = {"127.0.0.1", "::1", "localhost"}

# Paths that bypass password auth entirely (checked by prefix or exact match).
AUTH_EXEMPT_PREFIXES: frozenset[str] = frozenset()
AUTH_EXEMPT_EXACT: frozenset[str] = frozenset({"/api/health", "/api/auth/login", "/api/auth/logout"})
INTERNAL_AUTH_HEADER = "x-codeplane-internal-token"


@dataclass(frozen=True, slots=True)
class AuthPolicy:
    remote: bool = False
    access_only: bool = False
    password_required: bool = False

    @property
    def enabled(self) -> bool:
        return self.remote or self.access_only or self.password_required


_auth_policy: AuthPolicy | None = None
_internal_token: str | None = None
_internal_origin: str | None = None


def configure_auth(*, remote: bool, access_only: bool, password: str | None) -> AuthPolicy:
    """Freeze posture before registering any application routes."""
    global _auth_policy, _internal_token, _internal_origin  # noqa: PLW0603
    global _password_hash, _password_salt  # noqa: PLW0603
    from backend.services.auth import cf_access

    if access_only and (not remote or password or not cf_access.is_configured()):
        raise ValueError("Access-only requires remote mode, configured Access verification and no password")
    _session_tokens.clear()
    _password_hash = None
    _password_salt = None
    if password:
        set_password(password)
    _auth_policy = AuthPolicy(remote=remote, access_only=access_only, password_required=bool(password))
    _internal_token = secrets.token_urlsafe(32)
    _internal_origin = None
    return _auth_policy


def _policy(policy: AuthPolicy | None = None) -> AuthPolicy:
    return (
        policy
        if isinstance(policy, AuthPolicy)
        else (_auth_policy or AuthPolicy(password_required=is_password_auth_enabled()))
    )


def configure_internal_endpoint(*, host: str, port: int) -> None:
    """Bind self-call credentials to this process's resolved listener."""
    global _internal_origin  # noqa: PLW0603
    if host in {"0.0.0.0", "127.0.0.1", "localhost"}:  # noqa: S104
        host = "127.0.0.1"
    elif host in {"::", "::1"}:
        host = "[::1]"
    else:
        raise ValueError("Internal HTTP calls require a loopback-capable listener")
    if not 0 < port <= 65535:
        raise ValueError("Internal HTTP endpoint requires the resolved listener port")
    _internal_origin = f"http://{host}:{port}"


def internal_api_url() -> str:
    if _internal_origin is None:
        raise RuntimeError("Internal HTTP endpoint has not been bound")
    return f"{_internal_origin}/api"


def internal_request_headers(url: str) -> dict[str, str]:
    """Return credentials only for our known API, never a preview target."""
    parsed = urlparse(url)
    if (
        _internal_origin is None
        or f"{parsed.scheme}://{parsed.netloc}" != _internal_origin
        or parsed.username is not None
        or parsed.path.startswith("/api/preview")
        or not (parsed.path == "/api" or parsed.path.startswith("/api/"))
        or _internal_token is None
    ):
        raise ValueError("Refusing internal credential for an untrusted destination")
    return {INTERNAL_AUTH_HEADER: _internal_token}


# Rate limiting: track failed attempts per IP.
# Window + max follows OWASP brute-force mitigation guidance: short enough to
# block automated attacks, lenient enough to avoid locking out legitimate users
# who mistype a password a few times.  5 attempts/60s matches common defaults
# in Fail2Ban and similar tools.
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60  # seconds — sliding window for attempt counting
_RATE_LIMIT_MAX = 5  # max failed attempts per window before lockout

# Session token TTL (seconds).  Tokens older than this are rejected and purged.
SESSION_TTL: float = 86400  # 24 hours

# Active session tokens: token → creation timestamp (monotonic clock)
_session_tokens: dict[str, float] = {}

# The password hash and salt (set during startup)
_password_hash: bytes | None = None
_password_salt: bytes | None = None

# Load logo as base64 for the login page — deferred to first use
_logo_path = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "images" / "logo.png"
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


def _has_cloudflare_access(request: Request) -> bool:
    """Return True when the request carries a *valid* Cloudflare Access JWT.

    Cloudflare Access sets ``Cf-Access-Jwt-Assertion`` on every request that
    has passed its identity gate.  We verify the JWT signature, audience, and
    expiration against Cloudflare's public JWKS before trusting it.

    Returns False (and the request falls through to password auth) when:
    - CF Access is not configured (``CPL_CF_ACCESS_TEAM`` / ``…_AUD`` unset)
    - The header is missing or empty
    - The JWT fails signature / audience / expiry validation
    """
    from backend.services.auth import cf_access

    if not cf_access.is_configured():
        return False
    token = request.headers.get("cf-access-jwt-assertion")
    if not token:
        return False
    return cf_access.verify_token(token)


def is_request_authenticated(request: Request) -> bool:
    """Authenticate HTTP and SSE against the app's fixed posture."""
    app = request.scope.get("app")
    policy = _policy(getattr(getattr(app, "state", None), "auth_policy", None))
    if not policy.enabled:
        return True
    if not policy.remote and is_localhost(request):
        return True
    internal = request.headers.get(INTERNAL_AUTH_HEADER)
    if (
        internal
        and _internal_token
        and is_localhost(request)
        and hmac.compare_digest(internal.encode(), _internal_token.encode())
    ):
        return True
    if _has_cloudflare_access(request):
        return True
    return not policy.access_only and is_valid_token(request.cookies.get(COOKIE_NAME))


def is_password_auth_enabled() -> bool:
    """Return True when the server has password authentication configured."""
    return _password_hash is not None


# Rate limiting for WebSocket auth failures (mirrors login rate limiter)
_ws_auth_attempts: dict[str, list[float]] = defaultdict(list)


def _is_ws_rate_limited(ip: str) -> bool:
    """Check if an IP has exceeded the WebSocket auth rate limit."""
    now = time.monotonic()
    attempts = _ws_auth_attempts[ip]
    _ws_auth_attempts[ip] = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    return len(_ws_auth_attempts[ip]) >= _RATE_LIMIT_MAX


def _record_ws_attempt(ip: str) -> None:
    _ws_auth_attempts[ip].append(time.monotonic())


def check_websocket_auth(
    *,
    client_host: str | None,
    cookies: dict[str, str],
    cf_access_jwt: str | None = None,
    policy: AuthPolicy | None = None,
) -> bool:
    """Validate authentication for a WebSocket connection.

    Mirrors HTTP posture; origin validation remains a separate check.
    """
    resolved = _policy(policy)
    if not resolved.enabled:
        return True
    if not resolved.remote and client_host and client_host in LOCALHOST_ADDRS:
        return True
    if cf_access_jwt:
        from backend.services.auth import cf_access

        if cf_access.is_configured() and cf_access.verify_token(cf_access_jwt):
            return True
    ip = client_host or "unknown"
    if _is_ws_rate_limited(ip):
        return False
    valid = not resolved.access_only and is_valid_token(cookies.get(COOKIE_NAME))
    if not valid:
        _record_ws_attempt(ip)
    return valid


_LOGIN_HTML_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "templates" / "login.html"
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
    if _policy().access_only:
        return JSONResponse({"detail": "Cloudflare Access authentication required"}, status_code=401)
    ip = _client_ip(request)

    if _is_rate_limited(ip):
        log.warning("auth_login_rate_limited", client_ip=ip)
        return JSONResponse({"detail": "Too many attempts. Try again in a minute."}, status_code=429)

    try:
        body = await request.json()
        password = body.get("password", "")
    except (json.JSONDecodeError, ValueError):
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
        samesite="strict",
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


def is_public_request(request: Request) -> bool:
    """Only bootstrap resources and exact token-validated read routes are public."""
    path = request.url.path
    gate_source = r"/api/gate/gate-[a-f0-9]{32}"
    gate_request = gate_source + r"/requests/[a-zA-Z0-9_-]{1,128}"
    if (
        request.method == "POST"
        and re.fullmatch(gate_source + r"/(requests|invoke)", path)
        or request.method in {"GET", "DELETE"}
        and re.fullmatch(gate_request, path)
        or request.method == "POST"
        and re.fullmatch(gate_request + r"/(consume|observations)", path)
    ):
        # These exact handlers require their own execution-scoped control credential,
        # including in direct-local mode. Issuance remains operator-authenticated.
        return True
    if request.method == "POST" and path in {"/api/auth/login", "/api/auth/logout"}:
        return True
    if request.method not in {"GET", "HEAD"}:
        return False
    if path == "/api/health":
        return True
    if request.method == "GET" and re.fullmatch(r"/api/share/[^/]+/(job|events|snapshot|telemetry)", path):
        # Each of these four handlers validates the scoped token before I/O.
        return True
    return bool(re.fullmatch(r"/shared/[^/]+", path) or re.fullmatch(r"/assets/[^/]+", path))


async def auth_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Enforce app posture with narrowly scoped bootstrap/share exceptions."""
    path = request.url.path
    if is_public_request(request):
        app = request.scope.get("app")
        policy = _policy(getattr(getattr(app, "state", None), "auth_policy", None))
        if policy.access_only and path.startswith(("/api/share/", "/shared/")) and not _has_cloudflare_access(request):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return await call_next(request)
    if is_request_authenticated(request):
        return await call_next(request)
    if (
        request.method == "POST"
        and re.fullmatch(r"/api/integrations/[A-Za-z0-9._~-]+/traces", path)
        and request.headers.get("x-codeplane-ingest-token")
    ):
        # This exact handler validates its source credential even in local mode.
        # Do not promote the credential into operator/SSE/WebSocket authentication.
        return await call_next(request)

    # Not authenticated
    if path.startswith("/api") or path.startswith("/mcp"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    # Browser request — serve login page
    return HTMLResponse(_get_login_html(), status_code=401)


def _reset_for_testing() -> None:
    """Reset all module-level mutable state for test isolation."""
    global _password_hash, _password_salt, _logo_b64  # noqa: PLW0603
    global _auth_policy, _internal_token, _internal_origin  # noqa: PLW0603
    _auth_policy = None
    _internal_token = None
    _internal_origin = None
    _session_tokens.clear()
    _login_attempts.clear()
    _ws_auth_attempts.clear()
    _password_hash = None
    _password_salt = None
    _logo_b64 = None
