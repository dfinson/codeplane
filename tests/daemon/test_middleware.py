"""Tests for daemon/middleware.py module.

Covers:
- REPO_HEADER constant
- RepoHeaderMiddleware class
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codeplane.daemon.middleware import REPO_HEADER, RepoHeaderMiddleware


class TestRepoHeader:
    """Tests for REPO_HEADER constant."""

    def test_header_name(self) -> None:
        """Header name is correct."""
        assert REPO_HEADER == "X-CodePlane-Repo"

    def test_header_is_string(self) -> None:
        """Header is a string."""
        assert isinstance(REPO_HEADER, str)


class TestRepoHeaderMiddleware:
    """Tests for RepoHeaderMiddleware class."""

    @pytest.fixture
    def mock_app(self) -> MagicMock:
        """Create mock ASGI app."""
        return MagicMock()

    def test_init_resolves_path(self, mock_app: MagicMock, tmp_path: Path) -> None:
        """Middleware resolves the repo root path."""
        middleware = RepoHeaderMiddleware(mock_app, tmp_path)
        assert middleware.repo_root == tmp_path.resolve()

    def test_stores_app(self, mock_app: MagicMock, tmp_path: Path) -> None:
        """Middleware stores the wrapped app."""
        middleware = RepoHeaderMiddleware(mock_app, tmp_path)
        assert middleware.app is mock_app

    @pytest.mark.asyncio
    async def test_passes_through_non_http(self, mock_app: MagicMock, tmp_path: Path) -> None:
        """Passes through non-HTTP scopes unchanged."""
        middleware = RepoHeaderMiddleware(mock_app, tmp_path)

        scope = {"type": "websocket"}
        receive = MagicMock()
        send = MagicMock()

        # Make app awaitable
        async def mock_call(s: dict, r: MagicMock, se: MagicMock) -> None:
            pass

        mock_app.side_effect = mock_call

        await middleware(scope, receive, send)

        mock_app.assert_called_once_with(scope, receive, send)

    @pytest.mark.asyncio
    async def test_adds_header_to_http_response(self, tmp_path: Path) -> None:
        """Adds X-CodePlane-Repo header to HTTP responses."""
        captured_message: dict = {}

        async def mock_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                captured_message.update(message)

        async def mock_app(_scope: dict, _receive: MagicMock, send: MagicMock) -> None:
            await send({"type": "http.response.start", "headers": []})
            await send({"type": "http.response.body", "body": b"OK"})

        middleware = RepoHeaderMiddleware(mock_app, tmp_path)

        scope = {"type": "http"}
        receive = MagicMock()

        await middleware(scope, receive, mock_send)

        # Check header was added
        headers = captured_message.get("headers", [])
        header_names = [h[0] for h in headers]
        assert REPO_HEADER.lower().encode() in header_names

    @pytest.mark.asyncio
    async def test_preserves_existing_headers(self, tmp_path: Path) -> None:
        """Preserves existing response headers."""
        captured_message: dict = {}

        async def mock_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                captured_message.update(message)

        async def mock_app(_scope: dict, _receive: MagicMock, send: MagicMock) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "headers": [(b"content-type", b"application/json")],
                }
            )

        middleware = RepoHeaderMiddleware(mock_app, tmp_path)

        await middleware({"type": "http"}, MagicMock(), mock_send)

        headers = captured_message.get("headers", [])
        header_names = [h[0] for h in headers]

        # Both original and injected headers present
        assert b"content-type" in header_names
        assert REPO_HEADER.lower().encode() in header_names

    @pytest.mark.asyncio
    async def test_header_value_is_repo_path(self, tmp_path: Path) -> None:
        """Header value is the resolved repo path."""
        captured_message: dict = {}

        async def mock_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                captured_message.update(message)

        async def mock_app(_scope: dict, _receive: MagicMock, send: MagicMock) -> None:
            await send({"type": "http.response.start", "headers": []})

        middleware = RepoHeaderMiddleware(mock_app, tmp_path)

        await middleware({"type": "http"}, MagicMock(), mock_send)

        headers = dict(captured_message.get("headers", []))
        header_key = REPO_HEADER.lower().encode()

        assert header_key in headers
        assert headers[header_key] == str(tmp_path.resolve()).encode()
