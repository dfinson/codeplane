"""Adapter registry — factory for SDK-specific agent adapters."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

import structlog

from backend.config import CPLConfig
from backend.models.domain import AgentSDK, SDKModelMismatchError
from backend.services.adapters.agent_adapter import UtilityCompletionError, validate_sdk_model
from backend.services.adapters.contracts import AdapterDescriptor, AdapterReadiness, Capability, TextCompletionProvider
from backend.services.adapters.integration_policy import IntegrationPolicy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.services.adapters.agent_adapter import AgentAdapterInterface, CompletionResult
    from backend.services.adapters.completion_context import CompletionContext
    from backend.services.events.event_bus import EventBus
    from backend.services.job.approval_service import ApprovalService

log = structlog.get_logger()


def _utility_receipt_id(operation_id: str, phase: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"codeplane-utility:{operation_id}:{phase}"))


class UnavailableTextCompletion:
    """An unavailable utility capability, not a fabricated execution adapter."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_message: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> CompletionResult:
        raise UtilityCompletionError(self.reason, reason="provider_unavailable")


class _RegisteredTextCompletion:
    def __init__(self, registry: AdapterRegistry, sdk: AgentSDK | str) -> None:
        self._registry = registry
        self._sdk = sdk

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_message: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> CompletionResult:
        # Cached text wrappers must not outlive a provider's enablement.
        adapter = self._registry._resolve_completion_adapter(self._sdk)
        try:
            validate_sdk_model(str(self._sdk), model)
        except SDKModelMismatchError as exc:
            raise UtilityCompletionError(str(exc), reason="model_mismatch") from exc
        from backend.services.adapters.completion_context import current_completion_context

        context = current_completion_context()
        operation_id = str(uuid.uuid4())
        if context.execution_contract is not None:
            from sqlalchemy.exc import SQLAlchemyError

            try:
                await self._registry.record_utility_receipt(
                    operation_id,
                    "requested",
                    context,
                    {"provider": str(self._sdk), "requested_model": model},
                )
            except (OSError, SQLAlchemyError) as exc:
                log.error("utility_request_persistence_failed", job_id=context.job_id, operation_id=operation_id)
                raise UtilityCompletionError(
                    "Utility was not invoked because its request could not be recorded",
                    reason="receipt_persistence_failed",
                ) from exc
        result = await adapter.complete(
            prompt,
            model=model,
            system_message=system_message,
            excluded_tools=sorted(set(excluded_tools or ()) | {"*"}),
        )
        if context.execution_contract is not None:
            result = replace(result, usage_event_id=_utility_receipt_id(operation_id, "completed"))
            try:
                await self._registry.record_utility_receipt(
                    operation_id,
                    "completed",
                    context,
                    {
                        "provider": str(self._sdk),
                        "requested_model": model,
                        "actual_model": result.model or None,
                        "usage": [amount.model_dump(mode="json") for amount in result.usage],
                    },
                )
            except (OSError, SQLAlchemyError) as exc:
                log.error("utility_receipt_persistence_failed", job_id=context.job_id, operation_id=operation_id)
                raise UtilityCompletionError(
                    "Utility ran, but its completion receipt could not be persisted",
                    reason="receipt_persistence_failed",
                    completion=result,
                ) from exc
        return result


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
            (int(match[1]) == 1 and (int(match[2]) > 0 or int(match[3]) >= 34))
            if descriptor.id == "copilot"
            else int(match[1]) == 0 and int(match[2]) == 0 and int(match[3]) >= 25
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

    def get_completion_provider(self, sdk: AgentSDK | str | None = None) -> TextCompletionProvider:
        """Bind a registered text capability; admission is checked on each actual call."""
        try:
            selected = sdk if sdk is not None else self.policy.default_sdk
            descriptor = self.describe(selected)
        except (ValueError, SDKModelMismatchError) as exc:
            raise UtilityCompletionError(str(exc), reason="provider_unavailable") from exc
        if not descriptor.utility:
            raise UtilityCompletionError("The selected adapter has no text-completion capability", reason="unsupported")
        return _RegisteredTextCompletion(self, selected)

    def _resolve_completion_adapter(self, selected: AgentSDK | str) -> AgentAdapterInterface:
        try:
            descriptor = self.describe(selected)
            self.policy.require_enabled(selected)
        except (ValueError, SDKModelMismatchError) as exc:
            raise UtilityCompletionError(str(exc), reason="provider_unavailable") from exc
        if not descriptor.utility:
            raise UtilityCompletionError("The selected adapter has no text-completion capability", reason="unsupported")
        readiness = self.dependency_readiness(selected)
        if not readiness.ready:
            raise UtilityCompletionError(
                readiness.reason or "The selected completion provider is unavailable",
                reason="provider_unavailable",
            )
        return self.get_adapter(selected)

    async def record_utility_receipt(
        self,
        operation_id: str,
        phase: str,
        context: CompletionContext,
        payload: dict[str, object],
    ) -> str:
        from backend.models.events import new_event
        from backend.models.session_event import EventMetadata
        from backend.persistence.database import serialized_write
        from backend.persistence.event_repo import EventRepository

        pin = context.execution_contract
        if self._session_factory is None or pin is None or context.job_id != pin.job_id or not context.execution_epoch:
            raise UtilityCompletionError("Governed utility provenance is unavailable", reason="provider_unavailable")
        event_id = _utility_receipt_id(operation_id, phase)
        body: dict[str, object] = {
            **payload,
            "operation_id": operation_id,
            "role": context.role,
            "admission_id": pin.admission_id,
            "budget_id": pin.budget_id,
            "contract": pin.contract.model_dump(mode="json"),
        }
        async with serialized_write(self._session_factory) as session:
            await EventRepository(session).append(
                new_event(
                    context.job_id,
                    f"utility.completion.{phase}",
                    body,
                    event_id=event_id,
                    metadata=EventMetadata(
                        source_framework="codeplane",
                        namespace=("control", "utility"),
                        execution_epoch=context.execution_epoch,
                    ),
                )
            )
        return event_id

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
