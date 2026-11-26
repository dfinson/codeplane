"""Tests for Copilot client construction helpers."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from types import ModuleType

from backend.services.copilot_adapter._client import create_copilot_client


def test_create_copilot_client_uses_plain_constructor(monkeypatch) -> None:
    class _FakeCopilotClient:
        def __init__(self, config: object | None = None) -> None:
            self.created = True
            self.config = config

    fake_module = ModuleType("copilot")
    fake_module.CopilotClient = _FakeCopilotClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "copilot", fake_module)
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")

    client = create_copilot_client()

    assert isinstance(client, _FakeCopilotClient)
    assert client.config is None


def test_real_sdk_teardown_sanitizes_explicit_diagnostics_without_hiding_concurrent_ambient_errors():
    script = textwrap.dedent("""
        import asyncio
        import io
        import logging
        import sys
        import structlog
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from copilot import CopilotClient, RuntimeConnection
        from backend.services.copilot_adapter._client import stop_copilot_client

        def offline(event, arguments):
            if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen"}:
                raise AssertionError("Teardown validation must not start a CLI or use the network")

        async def main():
            output = io.StringIO()
            logging.basicConfig(level=logging.DEBUG, stream=output, force=True)
            structlog.configure(processors=[
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ])
            async def stop(explicit):
                marker = "explicit-fake-token-sentinel" if explicit else "ambient-error-sentinel"
                client = CopilotClient(connection=RuntimeConnection.for_stdio(path=sys.executable))
                session = SimpleNamespace(
                    session_id="offline",
                    disconnect=AsyncMock(side_effect=RuntimeError(marker)),
                    _mark_disconnected=Mock(),
                )
                client._sessions["offline"] = session
                await stop_copilot_client(client, timeout=1, explicit_account=explicit)
                session.disconnect.assert_awaited_once()
                session._mark_disconnected.assert_called_once()
            await asyncio.gather(stop(True), stop(False))
            text = output.getvalue()
            assert "explicit-fake-token-sentinel" not in text, text
            assert "diagnostic withheld for explicit inference account" in text, text
            assert "ambient-error-sentinel" in text, text
            logging.getLogger("copilot.client").debug("after-teardown-sentinel")
            assert "after-teardown-sentinel" in output.getvalue()

        with asyncio.Runner() as runner:
            runner.get_loop()
            sys.addaudithook(offline)
            runner.run(main())
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_structured_completion_uses_the_real_sdk_catalog_cache_without_a_second_rpc():
    script = textwrap.dedent("""
        import asyncio
        import sys
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from copilot import CopilotClient, RuntimeConnection
        from backend.services.copilot_adapter import CopilotAdapter
        from backend.services.adapters.completion_context import CompletionContext, completion_context

        def offline(event, arguments):
            if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen"}:
                raise AssertionError("This SDK cache regression must not start a CLI or use the network")
        async def main():
            client = CopilotClient(connection=RuntimeConnection.for_stdio(path=sys.executable))
            rpc = AsyncMock(return_value={"models": [{
                "id": "approved-model", "name": "Offline fixture",
                "capabilities": {"supports": {"vision": False}, "limits": {
                    "max_prompt_tokens": 136000, "max_context_window_tokens": 200000,
                    "max_output_tokens": 64000
                }},
                "policy": {"state": "enabled", "terms": ""}
            }]})
            client._client = SimpleNamespace(request=rpc)
            client.start = AsyncMock()
            client.stop = AsyncMock()
            assert (await client.list_models())[0].id == "approved-model"
            rpc.side_effect = AssertionError("The populated SDK catalog cache must be reused")
            callbacks = []
            session = SimpleNamespace(on=callbacks.append, abort=AsyncMock())
            async def response(*args, **kwargs):
                for callback in callbacks:
                    callback(SimpleNamespace(
                        type=SimpleNamespace(value="assistant.usage"),
                        data=SimpleNamespace(to_dict=lambda: {"model": "approved-model", "inputTokens": 7})
                    ))
                return SimpleNamespace(data=SimpleNamespace(to_dict=lambda: {"content": "{}"}))
            session.send_and_wait = AsyncMock(side_effect=response)
            client.create_session = AsyncMock(return_value=session)
            adapter = CopilotAdapter()
            schema = {"type": "object", "additionalProperties": False}
            with patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client):
                with completion_context(CompletionContext(role="assessment", response_schema=schema)):
                    result = await adapter.complete("offline fixture", model="approved-model")
            assert result.text == "{}" and result.model == "approved-model" and result.input_tokens == 7
            rpc.assert_awaited_once_with("models.list", {})
            client.start.assert_awaited_once()
            client.create_session.assert_awaited_once()
            assert client.create_session.await_args.kwargs["model"] == "approved-model"
            session.send_and_wait.assert_awaited_once()
            assert session.send_and_wait.await_args.kwargs["response_schema"] == {
                "type": "object", "properties": {}, "additionalProperties": False
            }
            client.stop.assert_awaited_once()
            assert not adapter._sessions and not adapter._clients and not adapter._opening_tasks
        with asyncio.Runner() as runner:
            runner.get_loop()
            sys.addaudithook(offline)
            runner.run(main())
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
