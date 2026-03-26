"""Conversation ledger for prompt composition tracking.

Tracks the exact token count of every message added to the conversation,
enabling per-turn prompt composition breakdown (system prompt vs history
vs tool results vs file contents vs overhead).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum


class MessageCategory(StrEnum):
    """Category of a conversation message for prompt composition."""

    agent = "agent"
    operator = "operator"
    tool_result = "tool_result"
    file_content = "file_content"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """A single message recorded in the conversation ledger."""

    role: str
    category: MessageCategory
    tokens: int


@dataclass(frozen=True, slots=True)
class PromptComposition:
    """Token breakdown of a single LLM turn's input."""

    system_tokens: int
    history_tokens: int
    tool_result_tokens: int
    file_content_tokens: int
    overhead_tokens: int
    sdk_reported_total: int


class ConversationLedger:
    """Tracks token counts per conversation segment.

    Token counts are provided by the caller, computed via the
    model-appropriate tokenizer before insertion.
    """

    def __init__(self) -> None:
        self._system_prompt_tokens: int = 0
        self._messages: list[LedgerEntry] = []

    def set_system_prompt(self, token_count: int) -> None:
        """Called once at session init with the exact system prompt token count."""
        self._system_prompt_tokens = token_count

    def record_message(
        self,
        role: str,
        category: MessageCategory,
        token_count: int,
    ) -> None:
        """Record a message with its pre-computed token count."""
        self._messages.append(LedgerEntry(role=role, category=category, tokens=token_count))

    def composition_at_turn(self, sdk_reported_input_tokens: int) -> PromptComposition:
        """Compute composition breakdown for the current turn.

        The SDK-reported input_tokens is the ground truth total.
        The ledger sum accounts for message content; any delta is
        SDK formatting overhead (role tags, separators, etc.).
        """
        by_category: dict[str, int] = defaultdict(int)
        for entry in self._messages:
            by_category[entry.category] += entry.tokens

        ledger_total = self._system_prompt_tokens + sum(by_category.values())
        overhead = sdk_reported_input_tokens - ledger_total

        return PromptComposition(
            system_tokens=self._system_prompt_tokens,
            history_tokens=by_category.get("agent", 0) + by_category.get("operator", 0),
            tool_result_tokens=by_category.get("tool_result", 0),
            file_content_tokens=by_category.get("file_content", 0),
            overhead_tokens=overhead,
            sdk_reported_total=sdk_reported_input_tokens,
        )

    @property
    def total_messages(self) -> int:
        """Number of messages recorded."""
        return len(self._messages)

    @property
    def total_tokens(self) -> int:
        """Total tokens across all messages (excluding system prompt)."""
        return sum(e.tokens for e in self._messages)


# ---------------------------------------------------------------------------
# Token counting strategies — all local, all synchronous
# ---------------------------------------------------------------------------


class TokenCounter:
    """Abstract token counter interface."""

    def count(self, text: str) -> int:
        raise NotImplementedError


class TiktokenCounter(TokenCounter):
    """For OpenAI/Copilot models. Uses the exact BPE tokenizer the API uses.

    tiktoken does NOT support Claude model names — calling
    encoding_for_model('claude-*') raises KeyError. This counter
    is only valid for gpt-*/o1-* models.
    """

    def __init__(self, model: str) -> None:
        import tiktoken

        self._enc = tiktoken.encoding_for_model(model)

    def count(self, text: str) -> int:
        return len(self._enc.encode(text))


class ClaudeTokenCounter(TokenCounter):
    """For Claude models. Uses the anthropic-tokenizer package.

    A Rust-compiled local BPE tokenizer with a 65,000-token vocabulary
    matching Claude's actual tokenization.
    """

    def count(self, text: str) -> int:
        import anthropic_tokenizer  # type: ignore[import-untyped]

        return int(anthropic_tokenizer.count_tokens(text))


def make_counter(model: str) -> TokenCounter:
    """Select the correct local tokenizer based on the model name.

    tiktoken.encoding_for_model() raises KeyError for unknown models.
    We use this as a definitive signal to route:
    - If tiktoken recognizes the model → use tiktoken (OpenAI models)
    - Otherwise, if model name contains 'claude' → use ClaudeTokenCounter
    - Otherwise → raise ValueError (unknown model, cannot tokenize)
    """
    try:
        return TiktokenCounter(model)
    except Exception:
        pass

    if "claude" in model.lower():
        return ClaudeTokenCounter()

    raise ValueError(f"No local tokenizer available for model {model!r}. Cannot compute per-segment token breakdown.")
