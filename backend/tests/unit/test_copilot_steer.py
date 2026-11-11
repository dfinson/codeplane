"""Tests for backend.services.completers.copilot_steer — CopilotSteerClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.services.completers.copilot_steer import CopilotSteerClient


@pytest.fixture()
def client():
    return CopilotSteerClient(github_token="test-token-123")


class TestRuntimeContract:
    async def test_rejected_send_is_not_success(self, client):
        response = httpx.Response(403, request=httpx.Request("POST", "https://fixture.invalid"))
        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
            rejected = False
            try:
                await client.send_message("session-1", "fixture")
            except RuntimeError:
                rejected = True
            assert rejected
        await client.close()

    async def test_unknown_liveness_does_not_confirm_live_execution(self, client):
        with patch.object(client._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("fixture")):
            assert await client.check_alive("session-1") is None
        await client.close()


class TestCopilotSteerClientInit:
    def test_client_created_with_auth_header(self, client: CopilotSteerClient):
        assert client._token == "test-token-123"
        assert client._client is not None

    def test_client_headers(self, client: CopilotSteerClient):
        headers = client._client.headers
        assert headers["authorization"] == "Bearer test-token-123"
        assert headers["content-type"] == "application/json"


class TestSendMessage:
    @pytest.mark.asyncio()
    async def test_send_message_success(self, client: CopilotSteerClient):
        mock_resp = httpx.Response(200, request=httpx.Request("POST", "https://x"))
        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await client.send_message("task-1", "hello")
            mock_post.assert_awaited_once()
            body = mock_post.call_args[1]["json"]
            assert body == {"content": "hello", "type": "user"}

    @pytest.mark.asyncio()
    async def test_send_message_http_error(self, client: CopilotSteerClient):
        mock_resp = httpx.Response(500, request=httpx.Request("POST", "https://x"))
        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            await client.send_message("task-2", "hello")

    @pytest.mark.asyncio()
    async def test_send_message_network_error(self, client: CopilotSteerClient):
        with patch.object(client._client, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            await client.send_message("task-3", "hello")


class TestAbort:
    @pytest.mark.asyncio()
    async def test_abort_success(self, client: CopilotSteerClient):
        mock_resp = httpx.Response(200, request=httpx.Request("POST", "https://x"))
        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await client.abort("task-1")
            body = mock_post.call_args[1]["json"]
            assert body == {"type": "abort"}

    @pytest.mark.asyncio()
    async def test_abort_http_error(self, client: CopilotSteerClient):
        mock_resp = httpx.Response(403, request=httpx.Request("POST", "https://x"))
        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            await client.abort("task-2")

    @pytest.mark.asyncio()
    async def test_abort_network_error(self, client: CopilotSteerClient):
        with patch.object(client._client, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            await client.abort("task-3")


class TestCheckAlive:
    @pytest.mark.asyncio()
    async def test_alive_returns_true_on_200(self, client: CopilotSteerClient):
        mock_resp = httpx.Response(200, request=httpx.Request("GET", "https://x"))
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            assert await client.check_alive("task-1") is True

    @pytest.mark.asyncio()
    async def test_alive_returns_false_on_404(self, client: CopilotSteerClient):
        mock_resp = httpx.Response(404, request=httpx.Request("GET", "https://x"))
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            assert await client.check_alive("task-2") is False

    @pytest.mark.asyncio()
    async def test_alive_returns_true_on_network_error(self, client: CopilotSteerClient):
        with patch.object(client._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            assert await client.check_alive("task-3") is True


class TestClose:
    @pytest.mark.asyncio()
    async def test_close(self, client: CopilotSteerClient):
        await client.close()
