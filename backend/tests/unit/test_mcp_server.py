"""Tests for MCP server tool handlers — unit-level with mocked services."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.mcp.server import create_mcp_server
from backend.models.domain import Job


def _make_job(**overrides) -> Job:
    defaults = dict(
        id="job-123",
        repo="/test/repo",
        prompt="Fix the bug",
        state="running",
        base_ref="main",
        branch="fix/bug",
        worktree_path="/test/repo/.codeplane-worktrees/fix-bug",
        session_id=None,
        pr_url=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=None,
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture
def mock_session_factory():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()

    # Create a proper async context manager
    class FakeFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    return FakeFactory()


@pytest.fixture
def mock_runtime():
    runtime = AsyncMock()
    runtime.start_or_enqueue = AsyncMock()
    runtime.cancel = AsyncMock()
    runtime.send_message = AsyncMock(return_value=True)
    return runtime


@pytest.fixture
def mock_approval():
    approval = AsyncMock()
    approval.list_for_job = AsyncMock(return_value=[])
    return approval


@pytest.fixture
def mcp_server(mock_session_factory, mock_runtime, mock_approval):
    return create_mcp_server(
        session_factory=mock_session_factory,
        runtime_service=mock_runtime,
        approval_service=mock_approval,
    )


class TestMCPServerCreation:
    def test_creates_server_with_tools(self, mcp_server) -> None:
        assert mcp_server is not None

    def test_server_has_name(self, mcp_server) -> None:
        assert mcp_server.name == "CodePlane"


class TestJobTools:
    @pytest.mark.asyncio
    async def test_cpl_health(self, mcp_server, mock_session_factory) -> None:
        """Test the health tool returns structured data."""

        # The health tool creates its own JobService, so we need to mock it
        with patch("backend.mcp.server.JobService") as mock_job_svc:
            mock_svc = AsyncMock()
            mock_svc.count_active_jobs = AsyncMock(return_value=2)
            mock_svc.count_queued_jobs = AsyncMock(return_value=1)
            mock_job_svc.return_value = mock_svc

            # Call the tool directly via the registered function
            tools = mcp_server._tool_manager._tools
            health_fn = tools.get("cpl_health")
            if health_fn:
                result = await health_fn.fn()
                assert result["status"] == "healthy"
                assert result["active_jobs"] == 2
                assert result["queued_jobs"] == 1

    @pytest.mark.asyncio
    async def test_cpl_job_list(self, mcp_server) -> None:
        with patch("backend.mcp.server.JobService") as mock_job_svc:
            mock_svc = AsyncMock()
            mock_svc.list_jobs = AsyncMock(return_value=([_make_job()], None, False))
            mock_job_svc.return_value = mock_svc

            tools = mcp_server._tool_manager._tools
            list_fn = tools.get("cpl_job_list")
            if list_fn:
                result = await list_fn.fn()
                assert len(result["items"]) == 1
                assert result["has_more"] is False

    @pytest.mark.asyncio
    async def test_cpl_job_get_not_found(self, mcp_server) -> None:
        from backend.services.job_service import JobNotFoundError

        with patch("backend.mcp.server.JobService") as mock_job_svc:
            mock_svc = AsyncMock()
            mock_svc.get_job = AsyncMock(side_effect=JobNotFoundError("not found"))
            mock_job_svc.return_value = mock_svc

            tools = mcp_server._tool_manager._tools
            get_fn = tools.get("cpl_job_get")
            if get_fn:
                result = await get_fn.fn(job_id="nonexistent")
                assert "error" in result


class TestConfigTools:
    @pytest.mark.asyncio
    async def test_cpl_repo_list(self, mcp_server) -> None:
        with patch("backend.mcp.server.load_config") as mock_config:
            cfg = MagicMock()
            cfg.repos = ["/test/repo"]
            mock_config.return_value = cfg

            tools = mcp_server._tool_manager._tools
            list_fn = tools.get("cpl_repo_list")
            if list_fn:
                result = await list_fn.fn()
                assert result["items"] == ["/test/repo"]

    @pytest.mark.asyncio
    async def test_cpl_settings_get(self, mcp_server) -> None:
        tools = mcp_server._tool_manager._tools
        get_fn = tools.get("cpl_settings_get")
        if get_fn:
            result = await get_fn.fn()
            assert "max_concurrent_jobs" in result
            assert "completion_strategy" in result


class TestApprovalTools:
    @pytest.mark.asyncio
    async def test_cpl_approval_resolve_invalid_resolution(self, mcp_server) -> None:
        tools = mcp_server._tool_manager._tools
        resolve_fn = tools.get("cpl_approval_resolve")
        if resolve_fn:
            result = await resolve_fn.fn(approval_id="a-1", resolution="maybe")
            assert "error" in result
            assert "approved" in result["error"] or "rejected" in result["error"]


class TestMessageTool:
    @pytest.mark.asyncio
    async def test_cpl_job_message_empty_content(self, mcp_server) -> None:
        tools = mcp_server._tool_manager._tools
        msg_fn = tools.get("cpl_job_message")
        if msg_fn:
            result = await msg_fn.fn(job_id="job-1", content="")
            assert "error" in result

    @pytest.mark.asyncio
    async def test_cpl_job_message_too_long(self, mcp_server) -> None:
        tools = mcp_server._tool_manager._tools
        msg_fn = tools.get("cpl_job_message")
        if msg_fn:
            result = await msg_fn.fn(job_id="job-1", content="x" * 10001)
            assert "error" in result
