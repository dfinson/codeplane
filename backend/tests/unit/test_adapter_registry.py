"""Unit tests for the adapter registry and Claude adapter event translation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.domain import (
    PermissionMode,
    SessionConfig,
)
from backend.services.adapter_registry import AdapterRegistry
from backend.services.agent_adapter import AgentAdapterInterface, AgentSDK

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

            registry = AdapterRegistry()
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
        """Unknown SDK raises ValueError."""
        registry = AdapterRegistry()
        with pytest.raises(ValueError):
            registry.get_adapter("unknown_sdk")

    def test_get_adapter_passes_services(self) -> None:
        """Approval service and event bus are passed to adapter constructors."""
        approval = MagicMock()
        bus = MagicMock()

        with patch("backend.services.claude_adapter.ClaudeAdapter") as mock_claude:
            mock_claude.return_value = MagicMock()
            registry = AdapterRegistry(approval_service=approval, event_bus=bus)
            registry.get_adapter(AgentSDK.claude)

            mock_claude.assert_called_once_with(
                approval_service=approval,
                event_bus=bus,
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
    """Test the permission callback builder without a real Claude SDK."""

    @pytest.fixture
    def adapter(self):
        from backend.services.claude_adapter import ClaudeAdapter

        return ClaudeAdapter()

    @pytest.mark.asyncio
    async def test_auto_mode_approves_all(self, adapter) -> None:
        """AUTO mode approves everything."""
        from claude_code_sdk import PermissionResultAllow

        config = SessionConfig(
            workspace_path="/tmp/test",
            prompt="test",
            permission_mode=PermissionMode.auto,
        )
        callback = adapter._build_can_use_tool(config, "sess-1")
        result = await callback("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_read_only_allows_reads(self, adapter) -> None:
        """READ_ONLY mode allows read tools."""
        from claude_code_sdk import PermissionResultAllow

        config = SessionConfig(
            workspace_path="/tmp/test",
            prompt="test",
            permission_mode=PermissionMode.read_only,
        )
        callback = adapter._build_can_use_tool(config, "sess-1")
        result = await callback("Read", {"file_path": "test.py"}, None)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_read_only_denies_writes(self, adapter) -> None:
        """READ_ONLY mode denies write tools."""
        from claude_code_sdk import PermissionResultDeny

        config = SessionConfig(
            workspace_path="/tmp/test",
            prompt="test",
            permission_mode=PermissionMode.read_only,
        )
        callback = adapter._build_can_use_tool(config, "sess-1")
        result = await callback("Edit", {"file_path": "test.py"}, None)
        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_approval_required_allows_reads(self, adapter) -> None:
        """APPROVAL_REQUIRED allows read tools without prompting."""
        from claude_code_sdk import PermissionResultAllow

        config = SessionConfig(
            workspace_path="/tmp/test",
            prompt="test",
            permission_mode=PermissionMode.approval_required,
        )
        callback = adapter._build_can_use_tool(config, "sess-1")
        result = await callback("Glob", {"pattern": "**/*.py"}, None)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_approval_required_routes_bash_to_operator(self, adapter) -> None:
        """APPROVAL_REQUIRED routes Bash to the operator."""
        from claude_code_sdk import PermissionResultAllow

        approval_service = MagicMock()
        approval_service.is_trusted = MagicMock(return_value=False)
        mock_approval = MagicMock()
        mock_approval.id = "apr-1"
        approval_service.create_request = AsyncMock(return_value=mock_approval)
        approval_service.wait_for_resolution = AsyncMock(return_value="approved")

        adapter._approval_service = approval_service
        adapter._session_to_job["sess-1"] = "job-1"
        adapter._queues["sess-1"] = asyncio.Queue()

        config = SessionConfig(
            workspace_path="/tmp/test",
            prompt="test",
            permission_mode=PermissionMode.approval_required,
        )
        callback = adapter._build_can_use_tool(config, "sess-1")
        result = await callback("Bash", {"command": "make test"}, None)

        assert isinstance(result, PermissionResultAllow)
        approval_service.create_request.assert_called_once()
        approval_service.wait_for_resolution.assert_called_once_with("apr-1")

    @pytest.mark.asyncio
    async def test_trusted_job_auto_approves(self, adapter) -> None:
        """Trusted jobs skip all permission checks."""
        from claude_code_sdk import PermissionResultAllow

        approval_service = MagicMock()
        approval_service.is_trusted = MagicMock(return_value=True)

        adapter._approval_service = approval_service
        adapter._session_to_job["sess-1"] = "job-1"

        config = SessionConfig(
            workspace_path="/tmp/test",
            prompt="test",
            permission_mode=PermissionMode.approval_required,
        )
        callback = adapter._build_can_use_tool(config, "sess-1")
        result = await callback("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, PermissionResultAllow)


class TestClaudeAdapterToolSummary:
    """Test the _summarize_tool_input helper."""

    def test_bash_summary(self) -> None:
        from backend.services.claude_adapter import _summarize_tool_input

        result = _summarize_tool_input("Bash", {"command": "make test"})
        assert result == "make test"

    def test_edit_summary(self) -> None:
        from backend.services.claude_adapter import _summarize_tool_input

        result = _summarize_tool_input("Edit", {"file_path": "src/main.py"})
        assert result == "src/main.py"

    def test_web_fetch_summary(self) -> None:
        from backend.services.claude_adapter import _summarize_tool_input

        result = _summarize_tool_input("WebFetch", {"url": "https://example.com"})
        assert result == "https://example.com"

    def test_fallback_summary(self) -> None:
        from backend.services.claude_adapter import _summarize_tool_input

        result = _summarize_tool_input("CustomTool", {"key": "value"})
        assert "key" in result


class TestSDKModelValidation:
    """Test SDK-model compatibility validation."""

    def test_copilot_accepts_any_model(self) -> None:
        from backend.services.agent_adapter import validate_sdk_model

        validate_sdk_model("copilot", "gpt-4o")
        validate_sdk_model("copilot", "claude-sonnet-4-20250514")
        validate_sdk_model("copilot", "o1-preview")

    def test_claude_accepts_claude_models(self) -> None:
        from backend.services.agent_adapter import validate_sdk_model

        validate_sdk_model("claude", "claude-sonnet-4-20250514")
        validate_sdk_model("claude", "claude-3-opus-20240229")
        validate_sdk_model("claude", "claude-3-haiku-20240307")

    def test_claude_rejects_non_claude_models(self) -> None:
        from backend.services.agent_adapter import SDKModelMismatchError, validate_sdk_model

        with pytest.raises(SDKModelMismatchError, match="not compatible with the claude SDK"):
            validate_sdk_model("claude", "gpt-4o")
        with pytest.raises(SDKModelMismatchError, match="not compatible with the claude SDK"):
            validate_sdk_model("claude", "o1-preview")

    def test_none_model_always_ok(self) -> None:
        from backend.services.agent_adapter import validate_sdk_model

        validate_sdk_model("copilot", None)
        validate_sdk_model("claude", None)

    def test_empty_model_always_ok(self) -> None:
        from backend.services.agent_adapter import validate_sdk_model

        validate_sdk_model("copilot", "")
        validate_sdk_model("claude", "")

    def test_unknown_sdk_raises(self) -> None:
        from backend.services.agent_adapter import SDKModelMismatchError, validate_sdk_model

        with pytest.raises(SDKModelMismatchError, match="Unknown SDK"):
            validate_sdk_model("unknown", "gpt-4o")
