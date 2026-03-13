"""Password-based authentication for Tower.

Implements TermBeam-style auth:
- Password auto-generated or set via --password / TOWER_PASSWORD
- httpOnly cookie sessions with 24h expiry
- Rate-limited login endpoint (5 attempts/min/IP)
- Localhost requests bypass auth (same-machine access is trusted)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from typing import Any

from starlette.requests import Request  # noqa: TC002
from starlette.responses import HTMLResponse, JSONResponse, Response  # noqa: TC002

# Rate limiting: track failed attempts per IP
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 5  # max attempts per window

# Active session tokens
_session_tokens: set[str] = set()

# The password hash (set during startup)
_password_hash: str | None = None


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


def _is_valid_token(token: str | None) -> bool:
    """Check if a session token is valid."""
    if not token:
        return False
    return token in _session_tokens


def _is_localhost(request: Request) -> bool:
    """Check if the request comes from localhost (trusted)."""
    client = request.client
    if client is None:
        return False
    host = client.host
    return host in ("127.0.0.1", "::1", "localhost")


_LOGIN_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tower — Login</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0d1117; color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh;
  }
  .login-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 32px; width: 360px; max-width: 90vw;
  }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 8px; text-align: center; }
  .subtitle { color: #8b949e; font-size: 13px; text-align: center; margin-bottom: 24px; }
  label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; }
  input[type=password] {
    width: 100%; padding: 10px 12px; background: #0d1117;
    border: 1px solid #30363d; border-radius: 6px; color: #e6edf3;
    font-size: 14px; font-family: inherit;
  }
  input:focus { outline: none; border-color: #58a6ff; }
  button {
    width: 100%; margin-top: 16px; padding: 10px; background: #238636;
    border: 1px solid #2ea043; border-radius: 6px; color: #fff;
    font-size: 14px; font-weight: 500; cursor: pointer;
  }
  button:hover { background: #2ea043; }
  .error { color: #f85149; font-size: 13px; margin-top: 12px; text-align: center; display: none; }
  .error.show { display: block; }
</style>
</head>
<body>
<div class="login-card">
  <h1>Tower</h1>
  <p class="subtitle">Enter the password printed in your terminal</p>
  <form id="login-form">
    <label for="password">Password</label>
    <input type="password" id="password" name="password" autofocus autocomplete="current-password" />
    <button type="submit">Sign in</button>
    <p class="error" id="error"></p>
  </form>
</div>
<script>
  const form = document.getElementById("login-form");
  const pw = document.getElementById("password");
  const err = document.getElementById("error");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.className = "error";
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw.value }),
      });
      if (res.ok) {
        window.location.href = "/";
      } else {
        const data = await res.json();
        err.textContent = data.detail || "Invalid password";
        err.className = "error show";
        pw.value = "";
        pw.focus();
      }
    } catch {
      err.textContent = "Connection failed";
      err.className = "error show";
    }
  });
</script>
</body>
</html>
"""


async def handle_login(request: Request) -> Response:
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
        key="tower_session",
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
    if _is_localhost(request):
        return await call_next(request)  # type: ignore[no-any-return]

    # Check session cookie
    token = request.cookies.get("tower_session")
    if _is_valid_token(token):
        return await call_next(request)  # type: ignore[no-any-return]

    # Not authenticated
    if path.startswith("/api") or path.startswith("/mcp"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    # Browser request — serve login page
    return HTMLResponse(_LOGIN_HTML, status_code=401)
