"""Tests for password-based authentication (backend.services.auth)."""

from __future__ import annotations

import hashlib
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services import auth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    client_host: str = "192.168.1.1",
    path: str = "/api/jobs",
    cookies: dict[str, str] | None = None,
    scheme: str = "http",
    headers: dict[str, str] | None = None,
    json_body: dict[str, object] | None = None,
    json_raises: bool = False,
) -> MagicMock:
    """Build a minimal mock Starlette Request."""
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = client_host
    req.url.path = path
    req.url.scheme = scheme
    req.cookies = cookies or {}
    req.headers = headers or {}

    if json_raises:
        req.json = AsyncMock(side_effect=Exception("bad json"))
    else:
        req.json = AsyncMock(return_value=json_body or {})
    return req


def _reset_auth_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset all module-level mutable state in auth."""
    monkeypatch.setattr(auth, "_password_hash", None)
    monkeypatch.setattr(auth, "_password_salt", None)
    monkeypatch.setattr(auth, "_session_tokens", {})
    monkeypatch.setattr(auth, "_login_attempts", auth.defaultdict(list))


# ---------------------------------------------------------------------------
# set_password / generate_password / _check_password
# ---------------------------------------------------------------------------


class TestPasswordManagement:
    def test_set_password_stores_pbkdf2_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("hunter2")
        assert auth._password_hash is not None
        assert isinstance(auth._password_hash, bytes)
        assert auth._password_salt is not None
        assert isinstance(auth._password_salt, bytes)
        # Verify the stored hash matches a PBKDF2 derivation with the same salt
        expected = hashlib.pbkdf2_hmac("sha256", b"hunter2", auth._password_salt, iterations=600_000)
        assert auth._password_hash == expected

    def test_generate_password_returns_nonempty_string(self) -> None:
        pw = auth.generate_password()
        assert isinstance(pw, str)
        assert len(pw) > 10

    def test_generate_password_is_unique(self) -> None:
        passwords = {auth.generate_password() for _ in range(20)}
        assert len(passwords) == 20

    def test_check_password_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("secret")
        assert auth._check_password("secret") is True

    def test_check_password_wrong(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("secret")
        assert auth._check_password("wrong") is False

    def test_check_password_no_hash_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        assert auth._check_password("anything") is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_not_rate_limited_initially(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        assert auth._is_rate_limited("10.0.0.1") is False

    def test_rate_limited_after_max_attempts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        ip = "10.0.0.2"
        for _ in range(auth._RATE_LIMIT_MAX):
            auth._record_attempt(ip)
        assert auth._is_rate_limited(ip) is True

    def test_old_attempts_pruned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        ip = "10.0.0.3"
        # Insert attempts that are older than the window
        old_time = time.monotonic() - auth._RATE_LIMIT_WINDOW - 10
        auth._login_attempts[ip] = [old_time] * auth._RATE_LIMIT_MAX
        assert auth._is_rate_limited(ip) is False

    def test_record_attempt_appends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        ip = "10.0.0.4"
        auth._record_attempt(ip)
        auth._record_attempt(ip)
        assert len(auth._login_attempts[ip]) == 2


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


class TestSessionTokens:
    def test_create_session_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        token = auth._create_session_token()
        assert isinstance(token, str)
        assert len(token) == 64  # hex(32) = 64 chars

    def test_created_token_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        token = auth._create_session_token()
        assert auth.is_valid_token(token) is True

    def test_unknown_token_is_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        assert auth.is_valid_token("not-a-real-token") is False

    def test_none_token_is_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        assert auth.is_valid_token(None) is False

    def test_empty_string_token_is_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        assert auth.is_valid_token("") is False


# ---------------------------------------------------------------------------
# _is_localhost
# ---------------------------------------------------------------------------


class TestIsLocalhost:
    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
    def test_localhost_addresses(self, host: str) -> None:
        req = _make_request(client_host=host)
        assert auth.is_localhost(req) is True

    def test_remote_address(self) -> None:
        req = _make_request(client_host="203.0.113.5")
        assert auth.is_localhost(req) is False

    def test_no_client(self) -> None:
        req = MagicMock()
        req.client = None
        assert auth.is_localhost(req) is False


# ---------------------------------------------------------------------------
# handle_login
# ---------------------------------------------------------------------------


class TestHandleLogin:
    @pytest.mark.asyncio
    async def test_successful_login(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("correct")
        req = _make_request(json_body={"password": "correct"})
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_wrong_password_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("correct")
        req = _make_request(json_body={"password": "wrong"})
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_rate_limited_returns_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("correct")
        ip = "10.0.0.99"
        for _ in range(auth._RATE_LIMIT_MAX):
            auth._record_attempt(ip)
        req = _make_request(client_host=ip, json_body={"password": "correct"})
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("pw")
        req = _make_request(json_raises=True)
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_password_field_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("correct")
        req = _make_request(json_body={})  # no "password" key
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_sets_cookie(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("pw")
        req = _make_request(json_body={"password": "pw"})
        resp = await auth.authenticate_login_request(req)
        # JSONResponse stores raw headers; check set-cookie was called
        raw_headers = dict(resp.raw_headers)
        assert b"set-cookie" in raw_headers
        assert b"cpl_session" in raw_headers[b"set-cookie"]

    @pytest.mark.asyncio
    async def test_login_https_via_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("pw")
        req = _make_request(json_body={"password": "pw"}, scheme="https")
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 200
        raw_headers = dict(resp.raw_headers)
        assert b"secure" in raw_headers[b"set-cookie"].lower()

    @pytest.mark.asyncio
    async def test_login_https_via_forwarded_proto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("pw")
        req = _make_request(
            json_body={"password": "pw"},
            headers={"x-forwarded-proto": "https"},
        )
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 200
        raw_headers = dict(resp.raw_headers)
        assert b"secure" in raw_headers[b"set-cookie"].lower()

    @pytest.mark.asyncio
    async def test_login_https_via_forwarded_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("pw")
        req = _make_request(
            json_body={"password": "pw"},
            headers={"forwarded": "for=203.0.113.8;proto=https;host=example.devtunnels.ms"},
        )
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 200
        raw_headers = dict(resp.raw_headers)
        assert b"secure" in raw_headers[b"set-cookie"].lower()

    @pytest.mark.asyncio
    async def test_login_https_via_devtunnel_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("pw")
        req = _make_request(
            json_body={"password": "pw"},
            headers={"host": "codeplane-8080.usw2.devtunnels.ms"},
        )
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 200
        raw_headers = dict(resp.raw_headers)
        assert b"secure" in raw_headers[b"set-cookie"].lower()

    @pytest.mark.asyncio
    async def test_wrong_password_records_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("correct")
        ip = "10.0.0.55"
        req = _make_request(client_host=ip, json_body={"password": "wrong"})
        await auth.authenticate_login_request(req)
        assert len(auth._login_attempts[ip]) == 1

    @pytest.mark.asyncio
    async def test_login_no_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("pw")
        req = _make_request(json_body={"password": "wrong"})
        req.client = None
        resp = await auth.authenticate_login_request(req)
        # client is None => ip = "unknown"; wrong password => 401
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# auth_middleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    @pytest.mark.asyncio
    async def test_auth_endpoints_always_allowed(self) -> None:
        req = _make_request(path="/api/auth/login", client_host="203.0.113.1")
        sentinel = object()
        call_next = AsyncMock(return_value=sentinel)
        resp = await auth.auth_middleware(req, call_next)
        assert resp is sentinel

    @pytest.mark.asyncio
    async def test_health_endpoint_always_allowed(self) -> None:
        req = _make_request(path="/api/health", client_host="203.0.113.1")
        sentinel = object()
        call_next = AsyncMock(return_value=sentinel)
        resp = await auth.auth_middleware(req, call_next)
        assert resp is sentinel

    @pytest.mark.asyncio
    async def test_localhost_bypasses_auth(self) -> None:
        req = _make_request(path="/api/jobs", client_host="127.0.0.1")
        sentinel = object()
        call_next = AsyncMock(return_value=sentinel)
        resp = await auth.auth_middleware(req, call_next)
        assert resp is sentinel

    @pytest.mark.asyncio
    async def test_valid_cookie_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        token = auth._create_session_token()
        req = _make_request(
            path="/api/jobs",
            client_host="203.0.113.1",
            cookies={"cpl_session": token},
        )
        sentinel = object()
        call_next = AsyncMock(return_value=sentinel)
        resp = await auth.auth_middleware(req, call_next)
        assert resp is sentinel

    @pytest.mark.asyncio
    async def test_no_cookie_api_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        req = _make_request(path="/api/jobs", client_host="203.0.113.1")
        call_next = AsyncMock()
        resp = await auth.auth_middleware(req, call_next)
        assert resp.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_cookie_mcp_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        req = _make_request(path="/mcp/something", client_host="203.0.113.1")
        call_next = AsyncMock()
        resp = await auth.auth_middleware(req, call_next)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_cookie_browser_returns_login_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        req = _make_request(path="/", client_host="203.0.113.1")
        call_next = AsyncMock()
        resp = await auth.auth_middleware(req, call_next)
        assert resp.status_code == 401
        assert b"CodePlane" in resp.body

    @pytest.mark.asyncio
    async def test_invalid_cookie_api_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        req = _make_request(
            path="/api/jobs",
            client_host="203.0.113.1",
            cookies={"cpl_session": "bogus-token"},
        )
        call_next = AsyncMock()
        resp = await auth.auth_middleware(req, call_next)
        assert resp.status_code == 401
        call_next.assert_not_called()


# ---------------------------------------------------------------------------
# #3 — PBKDF2 password hashing (salt + iterations)
# ---------------------------------------------------------------------------


class TestPBKDF2Hashing:
    def test_set_password_produces_bytes_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("test-pw")
        assert isinstance(auth._password_hash, bytes)
        assert isinstance(auth._password_salt, bytes)
        assert len(auth._password_salt) == 16

    def test_set_password_uses_unique_salt_each_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("pw1")
        salt1 = auth._password_salt
        _reset_auth_state(monkeypatch)
        auth.set_password("pw1")
        salt2 = auth._password_salt
        assert salt1 != salt2

    def test_same_password_different_salt_different_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("same-pw")
        hash1 = auth._password_hash
        _reset_auth_state(monkeypatch)
        auth.set_password("same-pw")
        hash2 = auth._password_hash
        assert hash1 != hash2

    def test_pbkdf2_derivation_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("verify-me")
        expected = hashlib.pbkdf2_hmac("sha256", b"verify-me", auth._password_salt, iterations=600_000)
        assert auth._password_hash == expected

    def test_check_password_with_no_salt_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        monkeypatch.setattr(auth, "_password_hash", b"some-bytes")
        monkeypatch.setattr(auth, "_password_salt", None)
        assert auth._check_password("anything") is False


# ---------------------------------------------------------------------------
# #5 — Session invalidation (logout)
# ---------------------------------------------------------------------------


class TestSessionInvalidation:
    def test_invalidate_existing_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        token = auth._create_session_token()
        assert auth.is_valid_token(token) is True
        result = auth.invalidate_session(token)
        assert result is True
        assert auth.is_valid_token(token) is False

    def test_invalidate_nonexistent_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        result = auth.invalidate_session("nonexistent-token")
        assert result is False

    def test_invalidate_none_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        result = auth.invalidate_session(None)
        assert result is False

    @pytest.mark.asyncio
    async def test_logout_clears_cookie(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("pw")
        token = auth._create_session_token()
        req = _make_request(cookies={"cpl_session": token})
        resp = await auth.authenticate_logout_request(req)
        assert resp.status_code == 200
        raw_headers = dict(resp.raw_headers)
        assert b"set-cookie" in raw_headers
        cookie_header = raw_headers[b"set-cookie"].decode()
        assert "cpl_session" in cookie_header
        assert auth.is_valid_token(token) is False

    @pytest.mark.asyncio
    async def test_logout_without_cookie_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        req = _make_request()
        resp = await auth.authenticate_logout_request(req)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# #6 — Rate limiting by X-Forwarded-For
# ---------------------------------------------------------------------------


class TestClientIPExtraction:
    def test_client_ip_from_x_forwarded_for(self) -> None:
        req = _make_request(
            client_host="10.0.0.1",
            headers={"x-forwarded-for": "203.0.113.50, 10.0.0.1"},
        )
        assert auth._client_ip(req) == "203.0.113.50"

    def test_client_ip_single_forwarded_for(self) -> None:
        req = _make_request(
            client_host="10.0.0.1",
            headers={"x-forwarded-for": "203.0.113.99"},
        )
        assert auth._client_ip(req) == "203.0.113.99"

    def test_client_ip_falls_back_to_client_host(self) -> None:
        req = _make_request(client_host="192.168.1.5")
        assert auth._client_ip(req) == "192.168.1.5"

    def test_client_ip_no_client(self) -> None:
        req = _make_request()
        req.client = None
        req.headers = {}
        assert auth._client_ip(req) == "unknown"

    def test_client_ip_empty_forwarded_for(self) -> None:
        req = _make_request(
            client_host="10.0.0.1",
            headers={"x-forwarded-for": ""},
        )
        assert auth._client_ip(req) == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_rate_limiting_uses_forwarded_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("correct")
        real_ip = "203.0.113.77"
        proxy_ip = "10.0.0.1"
        for _ in range(auth._RATE_LIMIT_MAX):
            auth._record_attempt(real_ip)
        req = _make_request(
            client_host=proxy_ip,
            json_body={"password": "correct"},
            headers={"x-forwarded-for": f"{real_ip}, {proxy_ip}"},
        )
        resp = await auth.authenticate_login_request(req)
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_failed_login_records_forwarded_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_auth_state(monkeypatch)
        auth.set_password("correct")
        real_ip = "203.0.113.88"
        proxy_ip = "10.0.0.1"
        req = _make_request(
            client_host=proxy_ip,
            json_body={"password": "wrong"},
            headers={"x-forwarded-for": real_ip},
        )
        await auth.authenticate_login_request(req)
        assert len(auth._login_attempts[real_ip]) == 1
        assert len(auth._login_attempts[proxy_ip]) == 0


# ---------------------------------------------------------------------------
# #10 — CSP header on login page
# ---------------------------------------------------------------------------


class TestLoginPageCSP:
    def test_login_html_contains_csp_meta_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(auth, "_LOGIN_HTML", None)
        html = auth._get_login_html()
        assert "Content-Security-Policy" in html
        assert "default-src 'none'" in html
        assert "script-src" in html
        assert "connect-src 'self'" in html
