"""Tests for backend.services.completers.copilot_steer — CopilotSteerClient."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.services.completers import copilot_steer
from backend.services.completers.copilot_steer import CopilotSteerClient


@pytest.fixture()
async def client():
    instance = CopilotSteerClient(github_token="test-token-123")
    yield instance
    await instance.close()


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
            assert body == {"content": "hello", "type": "user_message"}

    async def test_published_sender_protocol_with_mock_transport(self, client: CopilotSteerClient):
        requests = []
        content = "Preserve the original instruction.\nIncluding this line."

        def respond(request):
            body = json.loads(request.content)
            requests.append((request.method, str(request.url), body))
            expected = ({"content": content, "type": "user_message"}, {"type": "abort"})
            return httpx.Response(204 if body in expected else 400)

        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
        await client.send_message("published-task", content)
        await client.abort("published-task")
        endpoint = "https://api.enterprise.githubcopilot.com/agents/tasks/published-task/steer"
        assert requests == [
            ("POST", endpoint, {"content": content, "type": "user_message"}),
            ("POST", endpoint, {"type": "abort"}),
        ]

    @pytest.mark.asyncio()
    async def test_send_message_http_error(self, client: CopilotSteerClient):
        mock_resp = httpx.Response(500, request=httpx.Request("POST", "https://x"))
        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(copilot_steer.NativeDeliveryError) as error:
                await client.send_message("task-2", "hello")
            assert error.value.uncertain is False

    @pytest.mark.asyncio()
    async def test_send_message_network_error(self, client: CopilotSteerClient):
        with patch.object(client._client, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            with pytest.raises(copilot_steer.NativeDeliveryError) as error:
                await client.send_message("task-3", "hello")
            assert error.value.uncertain is False


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
            with pytest.raises(copilot_steer.NativeDeliveryError) as error:
                await client.abort("task-2")
            assert error.value.uncertain is False

    @pytest.mark.asyncio()
    async def test_abort_network_error(self, client: CopilotSteerClient):
        with patch.object(client._client, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            with pytest.raises(copilot_steer.NativeDeliveryError) as error:
                await client.abort("task-3")
            assert error.value.uncertain is False


class TestCheckAlive:
    @pytest.mark.asyncio()
    async def test_cloud_existence_is_unknown_cli_liveness(self, client: CopilotSteerClient):
        mock_resp = httpx.Response(200, request=httpx.Request("GET", "https://x"))
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            assert await client.check_alive("task-1") is None

    @pytest.mark.asyncio()
    async def test_not_found_is_not_authoritative_termination(self, client: CopilotSteerClient):
        mock_resp = httpx.Response(404, request=httpx.Request("GET", "https://x"))
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            assert await client.check_alive("task-2") is None

    @pytest.mark.asyncio()
    async def test_network_failure_leaves_liveness_unknown(self, client: CopilotSteerClient):
        with patch.object(client._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            assert await client.check_alive("task-3") is None


class TestNativeBindingTransport:
    @pytest.mark.parametrize("operation", ["message", "abort"])
    async def test_http_failure_preserves_only_sanitized_status(self, client, operation):
        response = httpx.Response(
            404,
            json={"message": "PRIVATE-BODY", "owner": "PRIVATE-OWNER"},
            request=httpx.Request("POST", "https://fixture.invalid"),
        )
        with (
            patch.object(client._client, "post", new_callable=AsyncMock, return_value=response) as post,
            patch("backend.services.completers.copilot_steer.log") as logger,
        ):
            with pytest.raises(copilot_steer.NativeDeliveryError) as error:
                if operation == "message":
                    await client.send_message("published-task", "PRIVATE-PROMPT")
                else:
                    await client.abort("published-task")
            assert error.value.uncertain is False
            assert getattr(error.value, "status_code", None) == 404
            assert "404" in str(error.value)
            post.assert_awaited_once()
            logger.warning.assert_called_once()
            assert logger.warning.call_args.kwargs == {"task_id": "published-task", "status_code": 404}
            diagnostics = str(error.value) + str(logger.mock_calls)
            for private in ("PRIVATE-BODY", "PRIVATE-OWNER", "PRIVATE-PROMPT", "test-token-123"):
                assert private not in diagnostics

    @pytest.mark.parametrize("operation", ["message", "abort"])
    async def test_lost_receipt_is_uncertain_and_never_retried(self, client, operation):
        with (
            patch.object(
                client._client, "post", new_callable=AsyncMock, side_effect=httpx.ReadTimeout("PRIVATE-DIAGNOSTIC")
            ) as post,
            patch("backend.services.completers.copilot_steer.log") as logger,
        ):
            with pytest.raises(copilot_steer.NativeDeliveryError) as error:
                if operation == "message":
                    await client.send_message("published-task", "fixture")
                else:
                    await client.abort("published-task")
            assert error.value.uncertain is True
            post.assert_awaited_once()
            assert "PRIVATE-DIAGNOSTIC" not in str(logger.mock_calls)
            assert all(not call.kwargs.get("exc_info") for call in logger.mock_calls)

    async def test_unknown_liveness_never_issues_diagnostic_get(self, client):
        with (
            patch.object(client._client, "get", new_callable=AsyncMock) as get,
            patch("backend.services.completers.copilot_steer.log") as logger,
        ):
            assert await client.check_alive("published-task") is None
            get.assert_not_awaited()
            assert not logger.mock_calls


class TestClose:
    @pytest.mark.asyncio()
    async def test_close(self, client: CopilotSteerClient):
        await client.close()
