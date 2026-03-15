"""Execution strategy interface and default implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from backend.models.domain import SessionConfig, SessionEvent
    from backend.services.agent_adapter import AgentAdapterInterface


class ExecutionStrategy(ABC):
    """Defines how a job's task is executed within its worktree."""

    @abstractmethod
    async def execute(
        self,
        config: SessionConfig,
        adapter: AgentAdapterInterface,
    ) -> AsyncIterator[SessionEvent]:
        """Run the strategy and yield session events as they occur."""
        yield  # type: ignore[misc]

    @abstractmethod
    async def send_message(self, message: str) -> None:
        """Inject an operator message into the running execution."""

    @abstractmethod
    async def abort(self) -> None:
        """Abort the running execution."""


class SingleAgentExecutor(ExecutionStrategy):
    """Executes a job using a single agent session."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._adapter: AgentAdapterInterface | None = None

    @property
    def session_id(self) -> str | None:
        """SDK session ID set by the adapter during execute(), available from the first event."""
        return self._session_id

    async def execute(
        self,
        config: SessionConfig,
        adapter: AgentAdapterInterface,
    ) -> AsyncIterator[SessionEvent]:
        self._adapter = adapter
        self._session_id = await adapter.create_session(config)
        async for event in adapter.stream_events(self._session_id):
            yield event

    async def send_message(self, message: str) -> None:
        if self._adapter and self._session_id:
            await self._adapter.send_message(self._session_id, message)

    async def abort(self) -> None:
        if self._adapter and self._session_id:
            await self._adapter.abort_session(self._session_id)


# --- Strategy Registry ---

from backend.models.api_schemas import StrategyKind  # noqa: E402

STRATEGY_REGISTRY: dict[StrategyKind, type[ExecutionStrategy]] = {
    StrategyKind.single_agent: SingleAgentExecutor,
}
