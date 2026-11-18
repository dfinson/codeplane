"""Tests for WebSocket authentication on the terminal endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

import backend.services.auth.middleware as auth_mod
from backend.services.auth.middleware import check_websocket_auth, set_password


@pytest.fixture(autouse=True)
def _reset_auth_state() -> Generator[None, None, None]:
    """Ensure each test starts with clean auth state."""
    auth_mod._reset_for_testing()
    yield
    auth_mod._reset_for_testing()


class TestCheckWebsocketAuth:
    """Unit tests for check_websocket_auth()."""

    def test_no_password_configured_allows_anyone(self) -> None:
        auth_mod._password_hash = None
        assert check_websocket_auth(client_host="8.8.8.8", cookies={}) is True

    def test_password_enabled_rejects_remote_without_cookie(self) -> None:
        set_password("secret")
        assert check_websocket_auth(client_host="8.8.8.8", cookies={}) is False

    def test_password_enabled_allows_localhost_ipv4(self) -> None:
        set_password("secret")
        assert check_websocket_auth(client_host="127.0.0.1", cookies={}) is True

    def test_password_enabled_allows_localhost_ipv6(self) -> None:
        set_password("secret")
        assert check_websocket_auth(client_host="::1", cookies={}) is True

    def test_password_enabled_allows_valid_session_cookie(self) -> None:
        set_password("secret")
        token = auth_mod._create_session_token()
        assert check_websocket_auth(client_host="8.8.8.8", cookies={"cpl_session": token}) is True

    def test_password_enabled_rejects_invalid_session_cookie(self) -> None:
        set_password("secret")
        assert check_websocket_auth(client_host="8.8.8.8", cookies={"cpl_session": "bogus"}) is False

    def test_none_client_host_falls_through_to_cookie_check(self) -> None:
        set_password("secret")
        token = auth_mod._create_session_token()
        assert check_websocket_auth(client_host=None, cookies={"cpl_session": token}) is True
        assert check_websocket_auth(client_host=None, cookies={}) is False

    @patch("backend.services.auth.cf_access.is_configured", return_value=True)
    @patch("backend.services.auth.cf_access.verify_token", return_value=True)
    def test_cf_access_jwt_bypasses_auth(self, _mock_verify, _mock_configured) -> None:
        set_password("secret")
        assert (
            check_websocket_auth(
                client_host="203.0.113.1",
                cookies={},
                cf_access_jwt="eyJhbGciOiJSUzI1NiJ9.valid.sig",
            )
            is True
        )

    def test_empty_cf_access_jwt_does_not_bypass(self) -> None:
        set_password("secret")
        assert (
            check_websocket_auth(
                client_host="203.0.113.1",
                cookies={},
                cf_access_jwt="",
            )
            is False
        )

    @patch("backend.services.auth.cf_access.is_configured", return_value=True)
    @patch("backend.services.auth.cf_access.verify_token", return_value=False)
    def test_forged_cf_access_jwt_does_not_bypass(self, _mock_verify, _mock_configured) -> None:
        set_password("secret")
        assert (
            check_websocket_auth(
                client_host="203.0.113.1",
                cookies={},
                cf_access_jwt="eyJhbGciOiJSUzI1NiJ9.forged.sig",
            )
            is False
        )
