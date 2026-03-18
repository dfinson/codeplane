"""Tests for MCP server tool handlers — unit-level with mocked services."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.mcp.server import create_mcp_server
from backend.models.domain import Job

# ── Helpers ──────────────────────────────────────────────────────────


def _make_job(**overrides: object) -> Job:
    defaults: dict = dict(
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


def _make_approval(**overrides: object) -> MagicMock:
    defaults = dict(
        id="apr-1",
        job_id="job-123",
        description="Run deploy?",
        proposed_action="deploy",
        requested_at=datetime.now(UTC),
        resolved_at=None,
        resolution=None,
    )
    defaults.update(overrides)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _make_artifact(**overrides: object) -> MagicMock:
    defaults = dict(
        id="art-1",
        job_id="job-123",
        name="diff.patch",
        type="diff_snapshot",
        mime_type="text/plain",
        size_bytes=1024,
        phase="agent_reasoning",
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _tool(mcp_server, name: str):
    """Look up a registered tool function by name."""
    return mcp_server._tool_manager._tools[name].fn


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_session_factory():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()

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


# ── Server creation ─────────────────────────────────────────────────


class TestMCPServerCreation:
    def test_creates_server_with_tools(self, mcp_server) -> None:
        assert mcp_server is not None

    def test_server_has_name(self, mcp_server) -> None:
        assert mcp_server.name == "CodePlane"

    def test_all_tools_registered(self, mcp_server) -> None:
        tools = mcp_server._tool_manager._tools
        expected = {
            "codeplane_job",
            "codeplane_approval",
            "codeplane_workspace",
            "codeplane_artifact",
            "codeplane_settings",
            "codeplane_repo",
            "codeplane_health",
        }
        assert expected.issubset(set(tools.keys()))


# ── Job tool ─────────────────────────────────────────────────────────


class TestJobTool:
    @pytest.mark.asyncio
    async def test_create_success(self, mcp_server) -> None:
        job = _make_job(state="queued")
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.create_job = AsyncMock(return_value=job)
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(
                action="create", repo="/test/repo", prompt="Fix bug"
            )
            assert result["id"] == "job-123"
            assert result["state"] == "queued"

    @pytest.mark.asyncio
    async def test_create_missing_params(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_job")(
            action="create", repo=None, prompt=None
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list(self, mcp_server) -> None:
        jobs = [_make_job(), _make_job(id="job-456")]
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.list_jobs = AsyncMock(return_value=(jobs, None, False))
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(action="list")
            assert len(result["items"]) == 2
            assert result["has_more"] is False

    @pytest.mark.asyncio
    async def test_get_success(self, mcp_server) -> None:
        job = _make_job()
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(
                action="get", job_id="job-123"
            )
            assert result["id"] == "job-123"

    @pytest.mark.asyncio
    async def test_get_not_found(self, mcp_server) -> None:
        from backend.services.job_service import JobNotFoundError

        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(side_effect=JobNotFoundError("nope"))
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(
                action="get", job_id="missing"
            )
            assert "error" in result

    @pytest.mark.asyncio
    async def test_get_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_job")(
            action="get", job_id=None
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_cancel(self, mcp_server) -> None:
        job = _make_job(state="cancelled")
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.cancel_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(
                action="cancel", job_id="job-123"
            )
            assert result["state"] == "cancelled"

    @pytest.mark.asyncio
    async def test_rerun(self, mcp_server) -> None:
        job = _make_job(id="job-new", state="queued")
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.rerun_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(
                action="rerun", job_id="job-123"
            )
            assert result["id"] == "job-new"

    @pytest.mark.asyncio
    async def test_message_success(self, mcp_server, mock_runtime) -> None:
        result = await _tool(mcp_server, "codeplane_job")(
            action="message", job_id="job-123", content="hello"
        )
        assert "seq" in result
        mock_runtime.send_message.assert_awaited_once_with("job-123", "hello")

    @pytest.mark.asyncio
    async def test_message_missing_params(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_job")(
            action="message", job_id=None, content=None
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_message_too_long(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_job")(
            action="message", job_id="job-1", content="x" * 10001
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_message_not_running(
        self, mcp_server, mock_runtime
    ) -> None:
        mock_runtime.send_message = AsyncMock(return_value=False)
        result = await _tool(mcp_server, "codeplane_job")(
            action="message", job_id="job-1", content="hi"
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_action(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_job")(action="explode")
        assert "error" in result
        assert "Unknown action" in result["error"]


# ── Approval tool ────────────────────────────────────────────────────


class TestApprovalTool:
    @pytest.mark.asyncio
    async def test_list(self, mcp_server, mock_approval) -> None:
        mock_approval.list_for_job = AsyncMock(
            return_value=[_make_approval()]
        )
        result = await _tool(mcp_server, "codeplane_approval")(
            action="list", job_id="job-123"
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "apr-1"

    @pytest.mark.asyncio
    async def test_list_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_approval")(
            action="list", job_id=None
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_resolve_approve(self, mcp_server, mock_approval) -> None:
        resolved = _make_approval(
            resolved_at=datetime.now(UTC), resolution="approved"
        )
        mock_approval.resolve = AsyncMock(return_value=resolved)

        result = await _tool(mcp_server, "codeplane_approval")(
            action="resolve", approval_id="apr-1", resolution="approved"
        )
        assert result["resolution"] == "approved"

    @pytest.mark.asyncio
    async def test_resolve_reject(self, mcp_server, mock_approval) -> None:
        resolved = _make_approval(
            resolved_at=datetime.now(UTC), resolution="rejected"
        )
        mock_approval.resolve = AsyncMock(return_value=resolved)

        result = await _tool(mcp_server, "codeplane_approval")(
            action="resolve", approval_id="apr-1", resolution="rejected"
        )
        assert result["resolution"] == "rejected"

    @pytest.mark.asyncio
    async def test_resolve_invalid_resolution(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_approval")(
            action="resolve", approval_id="apr-1", resolution="maybe"
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_resolve_missing_params(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_approval")(
            action="resolve", approval_id=None, resolution=None
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_action(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_approval")(
            action="nope"
        )
        assert "error" in result


# ── Workspace tool ───────────────────────────────────────────────────


class TestWorkspaceTool:
    @pytest.mark.asyncio
    async def test_list(self, mcp_server, tmp_path) -> None:
        (tmp_path / "hello.txt").write_text("hi")
        job = _make_job(worktree_path=str(tmp_path))
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_workspace")(
                action="list", job_id="job-123"
            )
            assert "items" in result
            names = [e["path"] for e in result["items"]]
            assert "hello.txt" in names

    @pytest.mark.asyncio
    async def test_read(self, mcp_server, tmp_path) -> None:
        (tmp_path / "readme.md").write_text("# Hello")
        job = _make_job(worktree_path=str(tmp_path))
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_workspace")(
                action="read", job_id="job-123", path="readme.md"
            )
            assert result["content"] == "# Hello"

    @pytest.mark.asyncio
    async def test_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_workspace")(
            action="list", job_id=None
        )
        assert "error" in result


# ── Artifact tool ────────────────────────────────────────────────────


class TestArtifactTool:
    @pytest.mark.asyncio
    async def test_list(self, mcp_server) -> None:
        art = _make_artifact()
        with (
            patch("backend.mcp.server.ArtifactService") as mock_svc_cls,
            patch("backend.mcp.server.ArtifactRepository"),
        ):
            svc = AsyncMock()
            svc.list_for_job = AsyncMock(return_value=[art])
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_artifact")(
                action="list", job_id="job-123"
            )
            assert len(result["items"]) == 1
            assert result["items"][0]["name"] == "diff.patch"

    @pytest.mark.asyncio
    async def test_list_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_artifact")(
            action="list", job_id=None
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get(self, mcp_server) -> None:
        art = _make_artifact()
        with (
            patch("backend.mcp.server.ArtifactService") as mock_svc_cls,
            patch("backend.mcp.server.ArtifactRepository"),
        ):
            svc = AsyncMock()
            svc.get = AsyncMock(return_value=art)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_artifact")(
                action="get", artifact_id="art-1"
            )
            assert result["id"] == "art-1"

    @pytest.mark.asyncio
    async def test_get_not_found(self, mcp_server) -> None:
        with (
            patch("backend.mcp.server.ArtifactService") as mock_svc_cls,
            patch("backend.mcp.server.ArtifactRepository"),
        ):
            svc = AsyncMock()
            svc.get = AsyncMock(return_value=None)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_artifact")(
                action="get", artifact_id="missing"
            )
            assert "error" in result

    @pytest.mark.asyncio
    async def test_get_missing_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_artifact")(
            action="get", artifact_id=None
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_action(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_artifact")(
            action="bad"
        )
        assert "error" in result


# ── Settings tool ────────────────────────────────────────────────────


class TestSettingsTool:
    @pytest.mark.asyncio
    async def test_get(self, mcp_server) -> None:
        with patch("backend.mcp.server.SettingsResponse") as mock_resp:
            mock_resp.return_value.model_dump.return_value = {
                "max_concurrent_jobs": 3,
                "completion_strategy": "merge",
            }
            result = await _tool(mcp_server, "codeplane_settings")(
                action="get"
            )
            assert "max_concurrent_jobs" in result

    @pytest.mark.asyncio
    async def test_update(self, mcp_server) -> None:
        with (
            patch("backend.config.save_config"),
            patch("backend.mcp.server.SettingsResponse") as mock_resp,
        ):
            mock_resp.return_value.model_dump.return_value = {
                "max_concurrent_jobs": 5,
                "completion_strategy": "merge",
            }
            result = await _tool(mcp_server, "codeplane_settings")(
                action="update", max_concurrent_jobs=5
            )
            assert result["max_concurrent_jobs"] == 5

    @pytest.mark.asyncio
    async def test_invalid_action(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_settings")(
            action="delete"
        )
        assert "error" in result


# ── Repo tool ────────────────────────────────────────────────────────


class TestRepoTool:
    @pytest.mark.asyncio
    async def test_list(self, mcp_server) -> None:
        with patch("backend.mcp.server.load_config") as mock_cfg:
            cfg = MagicMock()
            cfg.repos = ["/test/repo"]
            mock_cfg.return_value = cfg

            result = await _tool(mcp_server, "codeplane_repo")(
                action="list"
            )
            assert result["items"] == ["/test/repo"]

    @pytest.mark.asyncio
    async def test_get(self, mcp_server) -> None:
        with (
            patch("backend.mcp.server.load_config") as mock_cfg,
            patch("backend.mcp.server.GitService") as mock_git_cls,
        ):
            cfg = MagicMock()
            cfg.repos = ["/test/repo"]
            mock_cfg.return_value = cfg
            git = AsyncMock()
            git.get_origin_url = AsyncMock(
                return_value="https://github.com/test/repo.git"
            )
            git.get_default_branch = AsyncMock(return_value="main")
            mock_git_cls.return_value = git

            result = await _tool(mcp_server, "codeplane_repo")(
                action="get", repo_path="/test/repo"
            )
            assert result["path"] == "/test/repo"
            assert result["base_branch"] == "main"

    @pytest.mark.asyncio
    async def test_get_missing_path(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_repo")(
            action="get", repo_path=None
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_register_local(self, mcp_server, tmp_path) -> None:
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        with (
            patch("backend.mcp.server.load_config") as mock_cfg,
            patch("backend.mcp.server.GitService") as mock_git_cls,
            patch("backend.mcp.server.register_repo"),
        ):
            cfg = MagicMock()
            cfg.repos = []
            mock_cfg.return_value = cfg
            git = AsyncMock()
            git.validate_repo = AsyncMock(return_value=True)
            mock_git_cls.return_value = git
            mock_git_cls.is_remote_url = MagicMock(return_value=False)

            result = await _tool(mcp_server, "codeplane_repo")(
                action="register", source=str(repo_dir)
            )
            assert result["cloned"] is False

    @pytest.mark.asyncio
    async def test_remove(self, mcp_server) -> None:
        with (
            patch("backend.mcp.server.load_config"),
            patch("backend.mcp.server.unregister_repo"),
        ):
            result = await _tool(mcp_server, "codeplane_repo")(
                action="remove", repo_path="/test/repo"
            )
            assert result["status"] == "removed"

    @pytest.mark.asyncio
    async def test_remove_missing_path(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_repo")(
            action="remove", repo_path=None
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_action(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_repo")(action="purge")
        assert "error" in result


# ── Health tool ──────────────────────────────────────────────────────


class TestHealthTool:
    @pytest.mark.asyncio
    async def test_check(self, mcp_server) -> None:
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.count_active_jobs = AsyncMock(return_value=2)
            svc.count_queued_jobs = AsyncMock(return_value=1)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_health")(
                action="check"
            )
            assert result["status"] == "healthy"
            assert result["active_jobs"] == 2
            assert result["queued_jobs"] == 1

    @pytest.mark.asyncio
    async def test_cleanup(self, mcp_server) -> None:
        with (
            patch("backend.mcp.server.GitService") as mock_git_cls,
            patch("backend.mcp.server.load_config") as mock_cfg,
        ):
            cfg = MagicMock()
            cfg.repos = ["/test/repo"]
            mock_cfg.return_value = cfg
            git = AsyncMock()
            git.cleanup_worktrees = AsyncMock(return_value=3)
            mock_git_cls.return_value = git

            result = await _tool(mcp_server, "codeplane_health")(
                action="cleanup"
            )
            assert result["removed"] == 3

    @pytest.mark.asyncio
    async def test_invalid_action(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_health")(action="boom")
        assert "error" in result
