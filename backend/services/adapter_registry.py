"""Adapter registry — factory for SDK-specific agent adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from backend.services.agent_adapter import AgentAdapterInterface, AgentSDK

if TYPE_CHECKING:
    from backend.services.approval_service import ApprovalService
    from backend.services.event_bus import EventBus

log = structlog.get_logger()


class AdapterRegistry:
    """Creates and caches the appropriate adapter for a given SDK."""

    def __init__(
        self,
        approval_service: ApprovalService | None = None,
        event_bus: EventBus | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self._approval_service = approval_service
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._adapters: dict[AgentSDK, AgentAdapterInterface] = {}

    def get_adapter(self, sdk: AgentSDK | str) -> AgentAdapterInterface:
        """Return a (possibly cached) adapter instance for the given SDK."""
        if isinstance(sdk, str):
            sdk = AgentSDK(sdk)
        if sdk not in self._adapters:
            self._adapters[sdk] = self._create(sdk)
        return self._adapters[sdk]

    def _create(self, sdk: AgentSDK) -> AgentAdapterInterface:
        if sdk == AgentSDK.copilot:
            from backend.services.copilot_adapter import CopilotAdapter

            return CopilotAdapter(
                approval_service=self._approval_service,
                event_bus=self._event_bus,
                session_factory=self._session_factory,
            )
        if sdk == AgentSDK.claude:
            from backend.services.claude_adapter import ClaudeAdapter

            return ClaudeAdapter(
                approval_service=self._approval_service,
                event_bus=self._event_bus,
                session_factory=self._session_factory,
            )
        raise ValueError(f"Unknown SDK: {sdk}")
