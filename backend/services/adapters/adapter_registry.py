"""Adapter registry — factory for SDK-specific agent adapters."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog

from backend.config import CPLConfig
from backend.models.domain import AgentSDK
from backend.services.adapters.integration_policy import IntegrationPolicy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.services.adapters.agent_adapter import AgentAdapterInterface
    from backend.services.events.event_bus import EventBus
    from backend.services.job.approval_service import ApprovalService

log = structlog.get_logger()


class AdapterRegistry:
    """Creates and caches the appropriate adapter for a given SDK."""

    def __init__(
        self,
        approval_service: ApprovalService | None = None,
        event_bus: EventBus | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        config: CPLConfig | None = None,
    ) -> None:
        self._approval_service = approval_service
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._adapters: dict[AgentSDK, AgentAdapterInterface] = {}
        self.policy = IntegrationPolicy(config if config is not None else CPLConfig())

    def get_adapter(self, sdk: AgentSDK | str) -> AgentAdapterInterface:
        """Return a (possibly cached) adapter instance for the given SDK."""
        sdk = self.policy.require_enabled(sdk)
        if sdk not in self._adapters:
            adapter = self._create(sdk)
            self._adapters[sdk] = adapter
        return self._adapters[sdk]

    def _create(self, sdk: AgentSDK) -> AgentAdapterInterface:
        if sdk == AgentSDK.copilot:
            if os.environ.get("CODEPLANE_E2E_FAKE_AGENT") == "1":
                from backend.services.adapters.fake_adapter import E2EFakeCopilotAdapter

                return E2EFakeCopilotAdapter(
                    approval_service=self._approval_service,
                    event_bus=self._event_bus,
                    session_factory=self._session_factory,
                )
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
