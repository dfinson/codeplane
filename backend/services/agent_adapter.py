"""Agent adapter interface — the contract all SDK adapters must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from backend.models.domain import SessionConfig, SessionEvent


class AgentSDK(StrEnum):
    """Supported agent SDK backends."""

    copilot = "copilot"
    claude = "claude"


# Model prefixes accepted by each SDK.  An empty tuple means "any model".
_SDK_MODEL_PREFIXES: dict[AgentSDK, tuple[str, ...]] = {
    AgentSDK.copilot: (),  # Copilot proxies to multiple providers — any model
    AgentSDK.claude: ("claude-",),  # Claude SDK only supports Anthropic models
}


class SDKModelMismatchError(ValueError):
    """Raised when a model is incompatible with the selected SDK."""


def validate_sdk_model(sdk: str, model: str | None) -> None:
    """Raise SDKModelMismatchError if *model* is incompatible with *sdk*.

    No-op when model is None/empty (SDK will use its default).
    """
    if not model:
        return
    try:
        sdk_enum = AgentSDK(sdk)
    except ValueError:
        raise SDKModelMismatchError(f"Unknown SDK: {sdk!r}") from None
    prefixes = _SDK_MODEL_PREFIXES.get(sdk_enum, ())
    if prefixes and not model.startswith(prefixes):
        allowed = ", ".join(f"{p}*" for p in prefixes)
        raise SDKModelMismatchError(
            f"Model {model!r} is not compatible with the {sdk} SDK. Accepted model prefixes: {allowed}"
        )


class AgentAdapterInterface(ABC):
    """Wraps the agent runtime behind a generic interface."""

    @abstractmethod
    async def create_session(self, config: SessionConfig) -> str:
        """Create a session, return session_id."""

    @abstractmethod
    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        """Stream events from a running session."""
        yield  # type: ignore[misc]

    @abstractmethod
    async def send_message(self, session_id: str, message: str) -> None:
        """Send a follow-up message into a running session."""

    @abstractmethod
    async def abort_session(self, session_id: str) -> None:
        """Abort the current message processing. Session remains valid."""

    @abstractmethod
    async def complete(self, prompt: str) -> str:
        """Non-agentic single-turn completion. Returns the full response text."""
        return ""
