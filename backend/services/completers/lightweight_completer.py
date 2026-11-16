"""Lightweight completion — delegates to the SDK adapter.

Used for utility tasks (naming, summaries, monitoring).  All completions
route through the selected text-completion provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from backend.services.adapters.agent_adapter import UtilityCompletionError

if TYPE_CHECKING:
    from backend.services.adapters.agent_adapter import CompletionResult
    from backend.services.adapters.contracts import TextCompletionProvider

log = structlog.get_logger()


class LightweightCompleter:
    """Completer that delegates to the agent adapter.

    All calls go through the selected provider's tool-disabled ``complete()``.
    """

    def __init__(
        self,
        adapter: TextCompletionProvider,
        *,
        model: str = "claude-haiku-4-20250414",
        max_tokens: int = 256,
    ) -> None:
        self._adapter = adapter
        self._model = model
        self._max_tokens = max_tokens

    @property
    def available(self) -> bool:
        """Always available — completions go through the SDK adapter."""
        return True

    async def complete(self, prompt: str) -> CompletionResult:
        """Complete via the SDK adapter."""
        result = await self._adapter.complete(prompt, model=self._model, excluded_tools=["*"])
        if not result.text or not result.text.strip():
            raise UtilityCompletionError("Utility completion returned no text", reason="empty_response")
        return result

    async def complete_messages(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
    ) -> CompletionResult:
        """Multi-turn completion — flattened into a single prompt for the adapter."""
        flat = (
            system
            + "\n\n"
            + "\n".join(f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in messages)
        )
        result = await self._adapter.complete(flat, model=self._model, excluded_tools=["*"])
        if not result.text or not result.text.strip():
            raise UtilityCompletionError("Utility completion returned no text", reason="empty_response")
        return result

    async def close(self) -> None:
        """No-op — no resources to clean up."""
