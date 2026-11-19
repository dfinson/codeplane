"""Tests for backend.services.completers.narrator_completer — token lookup and delegation."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.completers.narrator_completer import (
    NarratorCompleter,
    _lookup_max_output_tokens,
)

# ── _lookup_max_output_tokens ──


class TestLookupMaxOutputTokens:
    def test_known_model(self, tmp_path: Path):
        pricing = {"claude-sonnet-4-20250514": {"max_output_tokens": 16384}}
        pricing_file = tmp_path / "pricing.json"
        pricing_file.write_text(json.dumps(pricing))
        with patch("backend.services.completers.narrator_completer._PRICING_PATH", pricing_file):
            result = _lookup_max_output_tokens("claude-sonnet-4-20250514")
        assert result == 16384

    def test_unknown_model(self, tmp_path: Path):
        pricing = {"claude-sonnet-4-20250514": {"max_output_tokens": 16384}}
        pricing_file = tmp_path / "pricing.json"
        pricing_file.write_text(json.dumps(pricing))
        with patch("backend.services.completers.narrator_completer._PRICING_PATH", pricing_file):
            result = _lookup_max_output_tokens("unknown-model")
        assert result is None

    def test_normalized_lookup(self, tmp_path: Path):
        pricing = {"claude-haiku-4-5": {"max_output_tokens": 8192}}
        pricing_file = tmp_path / "pricing.json"
        pricing_file.write_text(json.dumps(pricing))
        with patch("backend.services.completers.narrator_completer._PRICING_PATH", pricing_file):
            result = _lookup_max_output_tokens("claude-haiku-4-5")
        assert result == 8192

    def test_missing_file(self, tmp_path: Path):
        pricing_file = tmp_path / "nonexistent.json"
        with patch("backend.services.completers.narrator_completer._PRICING_PATH", pricing_file):
            result = _lookup_max_output_tokens("any-model")
        assert result is None

    def test_invalid_json(self, tmp_path: Path):
        pricing_file = tmp_path / "bad.json"
        pricing_file.write_text("not json")
        with patch("backend.services.completers.narrator_completer._PRICING_PATH", pricing_file):
            result = _lookup_max_output_tokens("any-model")
        assert result is None

    def test_missing_max_output_tokens_key(self, tmp_path: Path):
        pricing = {"claude-sonnet-4-20250514": {"cost_per_token": 0.001}}
        pricing_file = tmp_path / "pricing.json"
        pricing_file.write_text(json.dumps(pricing))
        with patch("backend.services.completers.narrator_completer._PRICING_PATH", pricing_file):
            result = _lookup_max_output_tokens("claude-sonnet-4-20250514")
        assert result is None


# ── NarratorCompleter ──


class TestNarratorCompleter:
    def test_always_available(self):
        adapter = AsyncMock()
        completer = NarratorCompleter(adapter, model="claude-haiku-4-5")
        assert completer.available is True

    @pytest.mark.asyncio()
    async def test_complete_delegates_to_adapter(self):
        from backend.services.adapters.agent_adapter import CompletionResult

        adapter = AsyncMock()
        adapter.complete.return_value = CompletionResult(
            text="narrative text", input_tokens=50, output_tokens=200, model="claude-haiku-4-5"
        )
        completer = NarratorCompleter(adapter, model="claude-haiku-4-5")
        result = await completer.complete("Write a story")
        assert result == "narrative text"
        adapter.complete.assert_awaited_once_with(
            "Write a story",
            model="claude-haiku-4-5",
            system_message=(
                "You are a technical writer. Respond ONLY with the requested text content. "
                "Do not use any tools, do not read or write files, do not run commands. "
                "Just produce the written content directly."
            ),
            excluded_tools=["*"],
        )

    @pytest.mark.asyncio()
    async def test_complete_reports_empty_response(self):
        from backend.services.adapters.agent_adapter import CompletionResult, UtilityCompletionError

        adapter = AsyncMock()
        adapter.complete.return_value = CompletionResult(text=None, input_tokens=10, output_tokens=0, model="test")
        completer = NarratorCompleter(adapter, model="test")
        with pytest.raises(UtilityCompletionError, match="Narration returned no text") as failure:
            await completer.complete("prompt")
        assert failure.value.reason == "empty_response"

    @pytest.mark.asyncio()
    async def test_close_is_noop(self):
        adapter = AsyncMock()
        completer = NarratorCompleter(adapter, model="test")
        await completer.close()  # should not raise
