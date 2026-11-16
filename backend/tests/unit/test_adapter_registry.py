"""Unit tests for the adapter registry and Claude adapter event translation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import CPLConfig, RuntimeConfig
from backend.models.domain import (
    AgentSDK,
    SDKModelMismatchError,
    SessionConfig,
)
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.agent_adapter import AgentAdapterInterface, CompletionResult, UtilityCompletionError
from backend.tests.unit.test_approval_service import session_factory as session_factory

# ---------------------------------------------------------------------------
# AdapterRegistry tests
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    async def test_governed_utility_retains_exact_contract_model_and_usage_receipt(self, session_factory) -> None:
        from decimal import Decimal

        from backend.models.execution_contracts import ExecutionContractPin
        from backend.models.usage import UsageAmount
        from backend.persistence.event_repo import EventRepository
        from backend.services.adapters.completion_context import CompletionContext, completion_context
        from backend.tests.unit.test_execution_contracts import _contract

        pin = ExecutionContractPin(
            job_id="job-1", admission_id="admission", budget_id="job-1", contract=_contract("pinned"),
        )
        registry = AdapterRegistry(session_factory=session_factory)
        adapter = registry.get_adapter("copilot")
        usage = UsageAmount(
            amount=Decimal("1"), unit="copilot_cost_unit", producer="copilot", model="served-model",
            role="utility", job_id="job-1", execution_epoch="epoch", source="assistant.usage",
        )
        adapter.complete = AsyncMock(return_value=CompletionResult(
            text="private completion content", model="served-model", usage=(usage,),
        ))
        with completion_context(CompletionContext(
            job_id="job-1", execution_epoch="epoch", execution_contract=pin,
        )):
            result = await registry.get_completion_provider("copilot").complete(
                "private input", model="requested-model",
            )
        async with session_factory() as session:
            recorded = [row.event for row in await EventRepository(session).list_after(0, job_id="job-1")]
        assert [str(event.kind) for event in recorded] == [
            "utility.completion.requested", "utility.completion.completed",
        ]
        completed = recorded[-1]
        assert result.usage_event_id == completed.id
        assert completed.payload["admission_id"] == pin.admission_id
        assert completed.payload["contract"] == pin.contract.model_dump(mode="json")
        assert completed.payload["actual_model"] == "served-model"
        assert completed.payload["requested_model"] == "requested-model"
        assert completed.payload["usage"] == [usage.model_dump(mode="json")]
        assert "private" not in str(completed.payload)
        assert result.usage == (usage,)

    @pytest.mark.parametrize("failure_phase", ["requested", "completed"])
    async def test_utility_receipt_failure_distinguishes_no_call_from_retained_completion(
        self, session_factory, monkeypatch, failure_phase,
    ) -> None:
        from backend.models.execution_contracts import ExecutionContractPin
        from backend.services.adapters.completion_context import CompletionContext, completion_context
        from backend.tests.unit.test_execution_contracts import _contract

        pin = ExecutionContractPin(
            job_id="job-1", admission_id="admission", budget_id="job-1", contract=_contract("pinned"),
        )
        registry = AdapterRegistry(session_factory=session_factory)
        adapter = registry.get_adapter("copilot")
        adapter.complete = AsyncMock(return_value=CompletionResult(text="retained", input_tokens=7, model="served"))
        record = registry.record_utility_receipt

        async def fail(operation_id, phase, context, payload):
            if phase == failure_phase:
                raise OSError("Persistence unavailable")
            return await record(operation_id, phase, context, payload)

        monkeypatch.setattr(registry, "record_utility_receipt", fail)
        with (
            completion_context(CompletionContext(job_id="job-1", execution_epoch="epoch", execution_contract=pin)),
            pytest.raises(UtilityCompletionError) as caught,
        ):
            await registry.get_completion_provider("copilot").complete("input")
        if failure_phase == "requested":
            adapter.complete.assert_not_awaited()
            assert caught.value.completion is None
        else:
            adapter.complete.assert_awaited_once()
            assert caught.value.completion.input_tokens == 7
            assert caught.value.completion.usage_event_id is not None

    @pytest.mark.parametrize("work_sdk,utility_sdk,expected", [
        ("claude", None, "copilot"), ("copilot", "claude", "claude"),
    ])
    async def test_runtime_monitor_uses_text_binding_not_work_harness(
        self, session_factory, tmp_path, work_sdk, utility_sdk, expected,
    ) -> None:
        from backend.services.events.event_bus import EventBus
        from backend.services.job.approval_service import ApprovalService
        from backend.services.runtime import RuntimeService

        config = CPLConfig(runtime=RuntimeConfig(
            copilot_only=False, default_sdk="copilot", utility_sdk=utility_sdk, utility_model="claude-explicit",
        ))
        registry = AdapterRegistry(config=config)
        text_adapter = registry.get_adapter(expected)
        text_adapter.complete = AsyncMock(return_value=CompletionResult(text="text result"))
        runtime = RuntimeService(
            session_factory=session_factory, event_bus=EventBus(), adapter_registry=registry, config=config,
            approval_service=ApprovalService(session_factory), git_service=MagicMock(),
        )
        runtime._governance_decider = MagicMock()
        await runtime._setup_action_policy(
            "job", SessionConfig(workspace_path=str(tmp_path), prompt="", sdk=work_sdk),
            str(tmp_path), repo=str(tmp_path),
        )
        router = runtime._policy_routers["job"]
        assert registry.get_adapter(work_sdk)._policy_router["job"] is router
        assert (await router._monitor._completer.complete("context")).text == "text result"
        text_adapter.complete.assert_awaited_once_with(
            "context", model="claude-explicit", system_message=None, excluded_tools=["*"],
        )

    def test_completion_provider_resolution_does_not_create_an_execution_session(self) -> None:
        registry = AdapterRegistry()
        provider = registry.get_completion_provider("copilot")
        assert not hasattr(provider, "create_session")
        assert registry._adapters == {}

    async def test_disabled_completion_provider_does_not_construct_a_client(self) -> None:
        registry = AdapterRegistry()
        provider = registry.get_completion_provider("claude")
        with pytest.raises(UtilityCompletionError, match="disabled"):
            await provider.complete("request")
        assert registry._adapters == {}

    @pytest.mark.parametrize(
        "default_sdk,copilot_only,override,expected",
        [("claude", True, None, "copilot"), ("claude", False, None, "claude"),
         ("copilot", False, "claude", "claude"), ("claude", False, "copilot", "copilot")],
    )
    async def test_completion_uses_enabled_default_or_explicit_text_binding(
        self, default_sdk, copilot_only, override, expected,
    ) -> None:
        config = CPLConfig(runtime=RuntimeConfig(
            default_sdk=default_sdk, copilot_only=copilot_only, utility_sdk=override,
        ))
        registry = AdapterRegistry(config=config)
        adapter = MagicMock(spec=AgentAdapterInterface)
        completion = CompletionResult(text="reply", model="claude-observed", input_tokens=7)
        adapter.complete = AsyncMock(return_value=completion)
        with patch.object(registry, "_create", return_value=adapter) as create:
            provider = registry.get_completion_provider(config.runtime.utility_sdk)
            assert await provider.complete("prompt", model="claude-explicit", system_message="system") is completion
        create.assert_called_once_with(AgentSDK(expected))
        adapter.complete.assert_awaited_once_with(
            "prompt", model="claude-explicit", system_message="system", excluded_tools=["*"],
        )
        adapter.create_session.assert_not_called()

    async def test_cached_completion_binding_rechecks_enablement_without_switching_provider(self) -> None:
        config = CPLConfig(runtime=RuntimeConfig(copilot_only=False))
        registry = AdapterRegistry(config=config)
        adapter = registry.get_adapter("claude")
        adapter.complete = AsyncMock(return_value=CompletionResult(text="ok"))
        provider = registry.get_completion_provider("claude")
        config.runtime.copilot_only = True
        with pytest.raises(UtilityCompletionError, match="disabled"):
            await provider.complete("must not run")
        adapter.complete.assert_not_awaited()
        assert AgentSDK.copilot not in registry._adapters
        config.runtime.copilot_only = False
        adapter.complete.assert_not_awaited()
        assert (await provider.complete("explicit call")).text == "ok"

    async def test_completion_model_mismatch_does_not_substitute_or_call_provider(self) -> None:
        registry = AdapterRegistry(config=CPLConfig(runtime=RuntimeConfig(copilot_only=False)))
        adapter = registry.get_adapter("claude")
        adapter.complete = AsyncMock()
        provider = registry.get_completion_provider("claude")
        with pytest.raises(UtilityCompletionError, match="not compatible"):
            await provider.complete("request", model="gpt-explicit")
        adapter.complete.assert_not_awaited()

    async def test_completion_binding_preserves_cancellation(self) -> None:
        registry = AdapterRegistry()
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def wait(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        adapter = registry.get_adapter("copilot")
        adapter.complete = AsyncMock(side_effect=wait)
        provider = registry.get_completion_provider("copilot")
        pending = asyncio.create_task(provider.complete("request"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
        finally:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        assert cancelled.is_set()
        adapter.complete.assert_awaited_once()

    def test_unknown_completion_binding_is_explicitly_unavailable(self) -> None:
        registry = AdapterRegistry()
        with pytest.raises(UtilityCompletionError, match="Unknown adapter"):
            registry.get_completion_provider("not-registered")
        assert registry._adapters == {}

    async def test_missing_completion_dependency_fails_explicitly(self) -> None:
        from backend.services.adapters.agent_adapter import UtilityCompletionError
        from backend.services.adapters.contracts import AdapterReadiness

        registry = AdapterRegistry()
        provider = registry.get_completion_provider("copilot")
        with (
            patch.object(registry, "dependency_readiness", return_value=AdapterReadiness(False, "Missing dependency")),
            pytest.raises(UtilityCompletionError, match="Missing dependency"),
        ):
            await provider.complete("request")
        assert registry._adapters == {}

    def test_descriptors_and_capabilities_remain_registered_while_disabled(self) -> None:
        from backend.services.adapters.contracts import Capability

        registry = AdapterRegistry()
        assert {descriptor.id for descriptor in registry.list_descriptors()} == {"copilot", "claude"}
        assert Capability.write in registry.supported_capabilities("claude")
        assert not registry.policy.enabled("claude")
        assert registry._adapters == {}, "Metadata discovery must not construct an SDK client"

    """Tests for the AdapterRegistry factory."""

    def test_get_adapter_copilot_returns_interface(self) -> None:
        """Registry creates an adapter that implements the interface."""
        with patch("backend.services.copilot_adapter.CopilotAdapter") as mock_copilot:
            mock_adapter = MagicMock(spec=AgentAdapterInterface)
            mock_copilot.return_value = mock_adapter

            registry = AdapterRegistry()
            adapter = registry.get_adapter(AgentSDK.copilot)
            assert adapter is mock_adapter
            mock_copilot.assert_called_once()

    def test_get_adapter_claude_returns_interface(self) -> None:
        """Registry creates Claude adapter."""
        with patch("backend.services.claude_adapter.ClaudeAdapter") as mock_claude:
            mock_adapter = MagicMock(spec=AgentAdapterInterface)
            mock_claude.return_value = mock_adapter

            registry = AdapterRegistry(config=CPLConfig(runtime=RuntimeConfig(copilot_only=False)))
            adapter = registry.get_adapter(AgentSDK.claude)
            assert adapter is mock_adapter
            mock_claude.assert_called_once()

    def test_get_adapter_caches(self) -> None:
        """Second call for same SDK returns cached instance."""
        with patch("backend.services.copilot_adapter.CopilotAdapter") as mock_copilot:
            mock_adapter = MagicMock(spec=AgentAdapterInterface)
            mock_copilot.return_value = mock_adapter

            registry = AdapterRegistry()
            first = registry.get_adapter("copilot")
            second = registry.get_adapter("copilot")
            assert first is second
            # Should only be constructed once
            mock_copilot.assert_called_once()

    def test_get_adapter_unknown_raises(self) -> None:
        """Unknown SDK raises the service-level validation error."""
        registry = AdapterRegistry()
        with pytest.raises(SDKModelMismatchError):
            registry.get_adapter("unknown_sdk")

    def test_get_adapter_passes_services(self) -> None:
        """Approval service, event bus, and session_factory are passed to adapter constructors."""
        approval = MagicMock()
        bus = MagicMock()
        sf = MagicMock()

        with patch("backend.services.claude_adapter.ClaudeAdapter") as mock_claude:
            mock_claude.return_value = MagicMock()
            registry = AdapterRegistry(
                approval_service=approval,
                event_bus=bus,
                session_factory=sf,
                config=CPLConfig(runtime=RuntimeConfig(copilot_only=False)),
            )
            registry.get_adapter(AgentSDK.claude)

            mock_claude.assert_called_once_with(
                approval_service=approval,
                event_bus=bus,
                session_factory=sf,
            )

    def test_string_sdk_accepted(self) -> None:
        """get_adapter accepts a plain string and converts to AgentSDK."""
        with patch("backend.services.copilot_adapter.CopilotAdapter") as mock_copilot:
            mock_copilot.return_value = MagicMock()
            registry = AdapterRegistry()
            adapter = registry.get_adapter("copilot")
            assert adapter is not None


# ---------------------------------------------------------------------------
# AgentSDK enum tests
# ---------------------------------------------------------------------------


class TestAgentSDK:
    def test_values(self) -> None:
        assert AgentSDK.copilot == "copilot"
        assert AgentSDK.claude == "claude"

    def test_from_string(self) -> None:
        assert AgentSDK("copilot") is AgentSDK.copilot
        assert AgentSDK("claude") is AgentSDK.claude

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            AgentSDK("nonexistent")


# ---------------------------------------------------------------------------
# ClaudeAdapter unit tests (mocked SDK)
# ---------------------------------------------------------------------------


class TestClaudeAdapterPermissions:
    """Test the permission callback builder without a real Claude SDK.

    With the new action policy system, all permission decisions route through the
    policy router (or deny if no router is configured).  These tests mock the
    policy router at the adapter level.
    """

    @pytest.fixture
    def adapter(self):
        from backend.services.claude_adapter import ClaudeAdapter

        return ClaudeAdapter()

    def _config(self) -> SessionConfig:
        return SessionConfig(
            workspace_path="/tmp/test",
            prompt="test",
        )

    @pytest.mark.asyncio
    async def test_policy_router_allow(self, adapter) -> None:
        """When the policy router allows, the callback returns PermissionResultAllow."""
        from claude_code_sdk import PermissionResultAllow

        from backend.services.adapters.base_adapter import PermissionDecision

        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.allow)
        adapter._session_to_job["sess-1"] = "job-1"

        callback = adapter._build_can_use_tool(self._config(), "sess-1")
        result = await callback("Bash", {"command": "make test"}, None)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_policy_router_deny(self, adapter) -> None:
        """When the policy router denies, the callback returns PermissionResultDeny."""
        from claude_code_sdk import PermissionResultDeny

        from backend.services.adapters.base_adapter import PermissionDecision

        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.deny)
        adapter._session_to_job["sess-1"] = "job-1"

        callback = adapter._build_can_use_tool(self._config(), "sess-1")
        result = await callback("Edit", {"file_path": "/tmp/test/test.py"}, None)
        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_no_policy_router_denies(self, adapter) -> None:
        """Without a policy router, the callback denies by default."""
        from claude_code_sdk import PermissionResultDeny

        adapter._session_to_job["sess-1"] = "job-1"

        callback = adapter._build_can_use_tool(self._config(), "sess-1")
        result = await callback("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_trusted_job_auto_approves(self, adapter) -> None:
        """Trusted jobs skip all permission checks."""
        from claude_code_sdk import PermissionResultAllow

        from backend.services.adapters.base_adapter import PermissionDecision

        # Trust bypass is checked inside _evaluate_permission, so mock it
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.allow)
        adapter._session_to_job["sess-1"] = "job-1"

        callback = adapter._build_can_use_tool(self._config(), "sess-1")
        result = await callback("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, PermissionResultAllow)


class TestClaudeAdapterToolSummary:
    """Test the _build_permission_description helper."""

    def test_bash_summary(self) -> None:
        from backend.services.adapters.base_adapter import BaseAgentAdapter

        result = BaseAgentAdapter._build_permission_description("shell", "Bash", {"command": "make test"}, None)
        assert "make test" in result

    def test_edit_summary(self) -> None:
        from backend.services.adapters.base_adapter import BaseAgentAdapter

        result = BaseAgentAdapter._build_permission_description("write", "Edit", {"file_path": "src/main.py"}, None)
        assert "src/main.py" in result

    def test_web_fetch_summary(self) -> None:
        from backend.services.adapters.base_adapter import BaseAgentAdapter

        result = BaseAgentAdapter._build_permission_description("url", "WebFetch", {"url": "https://example.com"}, None)
        assert "example.com" in result

    def test_fallback_summary(self) -> None:
        from backend.services.adapters.base_adapter import BaseAgentAdapter

        result = BaseAgentAdapter._build_permission_description("custom-tool", "CustomTool", {"key": "value"}, None)
        assert "key" in result


class TestSDKModelValidation:
    """Test SDK-model compatibility validation."""

    def test_copilot_accepts_any_model(self) -> None:
        from backend.services.adapters.agent_adapter import validate_sdk_model

        validate_sdk_model("copilot", "gpt-4o")
        validate_sdk_model("copilot", "claude-sonnet-4-20250514")
        validate_sdk_model("copilot", "o1-preview")

    def test_claude_accepts_claude_models(self) -> None:
        from backend.services.adapters.agent_adapter import validate_sdk_model

        validate_sdk_model("claude", "claude-sonnet-4-20250514")
        validate_sdk_model("claude", "claude-3-opus-20240229")
        validate_sdk_model("claude", "claude-3-haiku-20240307")

    def test_claude_rejects_non_claude_models(self) -> None:
        from backend.models.domain import SDKModelMismatchError
        from backend.services.adapters.agent_adapter import validate_sdk_model

        with pytest.raises(SDKModelMismatchError, match="not compatible with the claude SDK"):
            validate_sdk_model("claude", "gpt-4o")
        with pytest.raises(SDKModelMismatchError, match="not compatible with the claude SDK"):
            validate_sdk_model("claude", "o1-preview")

    def test_none_model_always_ok(self) -> None:
        from backend.services.adapters.agent_adapter import validate_sdk_model

        validate_sdk_model("copilot", None)
        validate_sdk_model("claude", None)

    def test_empty_model_always_ok(self) -> None:
        from backend.services.adapters.agent_adapter import validate_sdk_model

        validate_sdk_model("copilot", "")
        validate_sdk_model("claude", "")

    def test_unknown_sdk_raises(self) -> None:
        from backend.models.domain import SDKModelMismatchError
        from backend.services.adapters.agent_adapter import validate_sdk_model

        with pytest.raises(SDKModelMismatchError, match="Unknown SDK"):
            validate_sdk_model("unknown", "gpt-4o")
