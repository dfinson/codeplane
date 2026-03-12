"""Agent adapter interface and implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from backend.models.domain import SessionConfig, SessionEvent


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


class FakeAgentAdapter(AgentAdapterInterface):
    """Test double that emits scripted events."""

    async def create_session(self, config: SessionConfig) -> str:
        return "fake-session-1"

    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        yield SessionEvent(kind="done", payload={})  # type: ignore[arg-type]

    async def send_message(self, session_id: str, message: str) -> None:
        pass

    async def abort_session(self, session_id: str) -> None:
        pass
