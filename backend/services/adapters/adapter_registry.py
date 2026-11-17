"""Adapter registry — factory for SDK-specific agent adapters."""

from __future__ import annotations

import os
import re
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

import structlog

from backend.config import CPLConfig
from backend.models.domain import AgentSDK
from backend.services.adapters.contracts import AdapterDescriptor, AdapterReadiness, Capability
from backend.services.adapters.integration_policy import IntegrationPolicy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.services.adapters.agent_adapter import AgentAdapterInterface
    from backend.services.events.event_bus import EventBus
    from backend.services.job.approval_service import ApprovalService

log = structlog.get_logger()

_DESCRIPTORS: dict[str, AdapterDescriptor] = {
    "copilot": AdapterDescriptor(
        id="copilot",
        provider_family="github-copilot",
        supported_versions=(">=1.0.34,<2",),
        channels=frozenset({"sdk-events", "sdk-control", "sdk-tool-hooks"}),
        hooks=("on_pre_tool_use", "on_permission_request"),
        ownership="managed-sdk-session",
        steering="message-admission",
        cancellation="abort-and-release",
        interception="before-native-tool",
        identity_sources=("sdk-session-id", "tool-call-id"),
        usage_units=frozenset(
            {
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "premium_requests",
                "copilot_cost_unit",
            }
        ),
        capabilities=frozenset(Capability),
        utility=True,
    ),
    "claude": AdapterDescriptor(
        id="claude",
        provider_family="anthropic-claude",
        supported_versions=(">=0.0.25,<0.1",),
        channels=frozenset({"sdk-events", "sdk-control", "sdk-tool-hooks"}),
        hooks=("PreToolUse", "can_use_tool"),
        ownership="managed-sdk-session",
        steering="message-admission",
        cancellation="abort-and-release",
        interception="before-native-tool",
        identity_sources=("sdk-session-id", "tool-use-id"),
        usage_units=frozenset({"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "USD"}),
        capabilities=frozenset(Capability),
        utility=True,
    ),
}


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

    def describe(self, sdk: AgentSDK | str) -> AdapterDescriptor:
        try:
            return _DESCRIPTORS[str(sdk)]
        except KeyError as exc:
            raise ValueError(f"Unknown adapter: {sdk}") from exc

    def list_descriptors(self) -> tuple[AdapterDescriptor, ...]:
        """Registration is independent of enablement and never constructs clients."""
        return tuple(_DESCRIPTORS.values())

    def supported_capabilities(self, sdk: AgentSDK | str) -> frozenset[Capability]:
        return self.describe(sdk).capabilities

    def dependency_readiness(self, sdk: AgentSDK | str) -> AdapterReadiness:
        descriptor = self.describe(sdk)
        package = "github-copilot-sdk" if descriptor.id == "copilot" else "claude-code-sdk"
        try:
            installed = version(package)
        except PackageNotFoundError:
            return AdapterReadiness(False, f"Missing provider dependency: {package}")
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", installed)
        compatible = match is not None and (
            int(match[1]) == 1 and (int(match[2]), int(match[3])) >= (0, 34)
            if descriptor.id == "copilot"
            else (int(match[1]), int(match[2])) == (0, 0) and int(match[3]) >= 25
        )
        return AdapterReadiness(
            compatible, None if compatible else "Provider version is outside the supported release range", installed
        )

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
