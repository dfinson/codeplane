"""Unit tests for the adapter registry and Claude adapter event translation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import CPLConfig, RuntimeConfig
from backend.models.domain import (
    AgentSDK,
    SDKModelMismatchError,
    SessionConfig,
)
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.agent_adapter import AgentAdapterInterface

# ---------------------------------------------------------------------------
# AdapterRegistry tests
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
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
