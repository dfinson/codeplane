"""Narrator completer — dedicated path for long-form story generation.

All completions route through the agent adapter (Copilot SDK).
Implements ``Completable`` so it can be injected directly into
``StoryService``.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.services.adapters.agent_adapter import UtilityCompletionError

if TYPE_CHECKING:
    from backend.services.adapters.contracts import TextCompletionProvider

log = structlog.get_logger()

# Pricing data for looking up model output limits
_PRICING_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "model_pricing.json"


def _lookup_max_output_tokens(model: str) -> int | None:
    """Read the model's max_output_tokens from the pricing dataset."""
    try:
        data = json.loads(_PRICING_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    entry = data.get(model)
    if not entry:
        normalized = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", model.lower())).strip("-")
        entry = data.get(normalized)
    if entry and isinstance(entry.get("max_output_tokens"), (int, float)):
        return int(entry["max_output_tokens"])
    return None


class NarratorCompleter:
    """Completer for long-form narrative generation.

    Delegates to ``adapter.complete()`` which routes through the Copilot SDK.
    No API keys, no direct HTTP.
    """

    def __init__(
        self,
        adapter: TextCompletionProvider,
        *,
        model: str = "claude-haiku-4-5",
    ) -> None:
        self._adapter = adapter
        self._model = model
        self._max_output_tokens: int | None = _lookup_max_output_tokens(model)

    @property
    def available(self) -> bool:
        """Always available — completions go through the SDK adapter."""
        return True

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, prompt: str, timeout: float = 120.0) -> str:
        """Generate long-form narrative text via the SDK adapter."""
        system_msg = (
            "You are a technical writer. Respond ONLY with the requested text content. "
            "Do not use any tools, do not read or write files, do not run commands. "
            "Just produce the written content directly."
        )
        try:
            result = await asyncio.wait_for(
                self._adapter.complete(
                    prompt,
                    model=self._model,
                    system_message=system_msg,
                    excluded_tools=["*"],
                ),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise UtilityCompletionError("Narration timed out", reason="timeout") from exc
        if not result.text or not result.text.strip():
            raise UtilityCompletionError("Narration returned no text", reason="empty_response")
        return result.text

    async def close(self) -> None:
        """No-op — no resources to clean up."""
