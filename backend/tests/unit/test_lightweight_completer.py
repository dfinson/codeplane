"""Tests for backend.services.completers.lightweight_completer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.services.completers.lightweight_completer import LightweightCompleter


class TestAvailable:
    def test_always_available(self):
        adapter = AsyncMock()
        c = LightweightCompleter(adapter, model="test-model")
        assert c.available is True


class TestComplete:
    @pytest.mark.asyncio()
    async def test_delegates_to_adapter(self):
        from backend.services.adapters.agent_adapter import CompletionResult

        adapter = AsyncMock()
        adapter.complete.return_value = CompletionResult(text="result", input_tokens=10, output_tokens=5, model="test")
        c = LightweightCompleter(adapter, model="test-model")
        result = await c.complete("test prompt")
        assert result.text == "result"
        adapter.complete.assert_awaited_once_with("test prompt", model="test-model", excluded_tools=["*"])


class TestCompleteMessages:
    @pytest.mark.asyncio()
    async def test_flattens_messages(self):
        from backend.services.adapters.agent_adapter import CompletionResult

        adapter = AsyncMock()
        adapter.complete.return_value = CompletionResult(text="reply", input_tokens=20, output_tokens=10, model="test")
        c = LightweightCompleter(adapter, model="test-model")
        result = await c.complete_messages(
            system="You are helpful.",
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
        )
        assert result.text == "reply"
        call_arg = adapter.complete.call_args[0][0]
        assert "You are helpful." in call_arg
        assert "User: Hello" in call_arg
        assert "Assistant: Hi" in call_arg
        assert adapter.complete.call_args.kwargs == {"model": "test-model", "excluded_tools": ["*"]}


class TestClose:
    @pytest.mark.asyncio()
    async def test_close_is_noop(self):
        adapter = AsyncMock()
        c = LightweightCompleter(adapter, model="test")
        await c.close()  # should not raise
