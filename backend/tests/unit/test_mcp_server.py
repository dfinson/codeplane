"""Tests for MCP server tool handlers — unit-level with mocked services."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.mcp.server import create_mcp_server
from backend.tests.unit.conftest import make_job
from backend.tests.unit.test_ingest_sources import _TASK_ID
from backend.tests.unit.test_ingest_sources import native_control_stack as native_control_stack

# ── Helpers ──────────────────────────────────────────────────────────


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


@pytest.fixture(autouse=True)
def internal_transport():
    from backend.services.auth import middleware

    # Routing regressions also run on the revision before internal transport authentication existed.
    configure_auth = getattr(middleware, "configure_auth", None)
    configure_internal_endpoint = getattr(middleware, "configure_internal_endpoint", None)
    if configure_auth is not None:
        configure_auth(remote=False, access_only=False, password=None)
    if configure_internal_endpoint is not None:
        configure_internal_endpoint(host="127.0.0.1", port=8080)
    yield
    middleware._reset_for_testing()


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
    runtime.send_operator_message = AsyncMock(return_value=True)
    return runtime


@pytest.fixture
def mock_approval():
    approval = AsyncMock()
    approval.list_for_job = AsyncMock(return_value=[])
    return approval


@pytest.fixture
def mock_merge_service():
    from backend.services.merge_service import MergeResult, MergeStatus

    merge = AsyncMock()
    merge.request_pr_for_job = AsyncMock(
        return_value=MergeResult(status=MergeStatus.pr_created, strategy="pr", pr_url="https://example.com/pr/1")
    )
    return merge


@pytest.fixture
def mcp_server(mock_session_factory, mock_runtime, mock_approval, mock_merge_service):
    return create_mcp_server(
        session_factory=mock_session_factory,
        runtime_service=mock_runtime,
        approval_service=mock_approval,
        merge_service=mock_merge_service,
    )


# ── Server creation ─────────────────────────────────────────────────


class TestRuntimeContract:
    async def test_message_uses_source_aware_command(self, mcp_server, mock_runtime):
        mock_runtime.send_operator_message = AsyncMock(return_value=True)
        result = await _tool(mcp_server, "codeplane_job")(action="message", job_id="native-job", content="fixture")
        assert mock_runtime.send_operator_message.await_count == 1
        mock_runtime.send_message.assert_not_awaited()
        assert "error" not in result


class TestNativeBindingMCP:
    @pytest.mark.parametrize("action", ["message", "cancel"])
    @pytest.mark.parametrize("case", ["accepted", "missing", "mismatch", "off", "rejected", "uncertain"])
    async def test_native_routing_and_typed_failure(self, mcp_server, mock_runtime, native_control_stack, action, case):
        import json

        import httpx

        stack = native_control_stack
        if case == "missing":
            stack.workspace.unlink()
        elif case in {"mismatch", "off"}:
            data = json.loads(stack.workspace.read_text(encoding="utf-8"))
            data["mc_session_id" if case == "mismatch" else "remote_steerable"] = (
                _TASK_ID if case == "mismatch" else False
            )
            stack.workspace.write_text(json.dumps(data), encoding="utf-8")
        elif case == "rejected":
            stack.post.return_value = httpx.Response(
                404, text="PRIVATE-BODY", request=httpx.Request("POST", "https://fixture.invalid")
            )
        elif case == "uncertain":
            stack.post.side_effect = httpx.ReadTimeout("PRIVATE-DIAGNOSTIC")
        cancel = AsyncMock(return_value=make_job(id=stack.job.id, state="canceled"))
        stack.runtime._make_job_service = MagicMock(return_value=MagicMock(cancel_job=cancel))
        mock_runtime.send_operator_message.side_effect = stack.runtime.send_operator_message
        mock_runtime.cancel_job.side_effect = stack.runtime.cancel_job
        result = await _tool(mcp_server, "codeplane_job")(
            action=action, job_id=stack.job.id, content="fixture" if action == "message" else None
        )
        if case == "accepted":
            assert "error" not in result
            assert "seq" in result if action == "message" else result["state"] == "canceled"
            if action == "cancel":
                cancel.assert_awaited_once_with(stack.job.id)
        else:
            assert "error" in result
            assert "PRIVATE" not in result["error"]
            if case == "rejected":
                assert "404" in result["error"]
            if case == "uncertain":
                assert "unknown" in result["error"]
            cancel.assert_not_awaited()
        if case in {"missing", "mismatch", "off"}:
            stack.post.assert_not_awaited()
        else:
            stack.post.assert_awaited_once_with(
                f"https://api.enterprise.githubcopilot.com/agents/tasks/{_TASK_ID}/steer",
                json={"content": "fixture", "type": "user_message"} if action == "message" else {"type": "abort"},
            )
        stack.runtime.send_message.assert_not_awaited()
        stack.runtime._resume_orphaned.assert_not_awaited()


class TestMCPServerCreation:
    @pytest.mark.asyncio
    async def test_project_tool_rejects_source_ingestion(self, mcp_server) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        tool = next(item for item in await mcp_server.list_tools() if item.name == "codeplane_project")
        assert "ingest_tasks" not in tool.inputSchema["properties"]["action"]["enum"]
        assert "ingest_tasks" not in (tool.description or "")
        with pytest.raises(ToolError, match="ingest_tasks"):
            await mcp_server.call_tool("codeplane_project", {"action": "ingest_tasks", "project_id": "project"})

    def test_creates_server_with_tools(self, mcp_server) -> None:
        assert mcp_server is not None

    def test_server_has_name(self, mcp_server) -> None:
        assert mcp_server.name == "CodePlane"

    def test_all_tools_registered(self, mcp_server) -> None:
        tools = mcp_server._tool_manager._tools
        expected = {
            "codeplane_job",
            "codeplane_pr",
            "codeplane_tracker",
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
        job = make_job(id="job-123", repo="/test/repo", state="queued")
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.create_job = AsyncMock(return_value=job)
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(action="create", repo="/test/repo", prompt="Fix bug")
            assert result["id"] == "job-123"
            assert result["state"] == "queued"

    @pytest.mark.asyncio
    async def test_create_missing_params(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_job")(action="create", repo=None, prompt=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list(self, mcp_server) -> None:
        jobs = [make_job(id="job-123", repo="/test/repo"), make_job(id="job-456", repo="/test/repo")]
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
        job = make_job(id="job-123", repo="/test/repo")
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(action="get", job_id="job-123")
            assert result["id"] == "job-123"

    @pytest.mark.asyncio
    async def test_get_not_found(self, mcp_server) -> None:
        from backend.models.domain import JobNotFoundError

        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(side_effect=JobNotFoundError("nope"))
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(action="get", job_id="missing")
            assert "error" in result

    @pytest.mark.asyncio
    async def test_get_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_job")(action="get", job_id=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_cancel(self, mcp_server, mock_runtime) -> None:
        job = make_job(id="job-123", repo="/test/repo", state="canceled")
        mock_runtime.cancel_job.return_value = job
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.cancel_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(action="cancel", job_id="job-123")
            assert result["state"] == "canceled"

    @pytest.mark.asyncio
    async def test_rerun(self, mcp_server) -> None:
        job = make_job(id="job-new", repo="/test/repo", state="queued")
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.rerun_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_job")(action="rerun", job_id="job-123")
            assert result["id"] == "job-new"

    @pytest.mark.asyncio
    async def test_message_success(self, mcp_server, mock_runtime) -> None:
        result = await _tool(mcp_server, "codeplane_job")(action="message", job_id="job-123", content="hello")
        assert "seq" in result
        mock_runtime.send_operator_message.assert_awaited_once_with("job-123", "hello")

    @pytest.mark.asyncio
    async def test_message_missing_params(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_job")(action="message", job_id=None, content=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_message_too_long(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_job")(action="message", job_id="job-1", content="x" * 10001)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_message_not_running(self, mcp_server, mock_runtime) -> None:
        mock_runtime.send_operator_message = AsyncMock(return_value=False)
        result = await _tool(mcp_server, "codeplane_job")(action="message", job_id="job-1", content="hi")
        assert "error" in result


# ── PR tool (CAP-14) ─────────────────────────────────────────────────


class TestPrTool:
    @pytest.mark.asyncio
    async def test_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_pr")(job_id=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_job_not_found(self, mcp_server) -> None:
        from backend.models.domain import JobNotFoundError

        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(side_effect=JobNotFoundError("nope"))
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_pr")(job_id="missing")
            assert "error" in result

    @pytest.mark.asyncio
    async def test_success(self, mcp_server, mock_merge_service) -> None:
        job = make_job(id="job-123", repo="/test/repo", branch="feature/x")
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_pr")(job_id="job-123")

            assert result["pr_url"] == "https://example.com/pr/1"
            assert result["status"] == "pr_created"
            mock_merge_service.request_pr_for_job.assert_awaited_once_with(job)

    @pytest.mark.asyncio
    async def test_service_error_propagated(self, mcp_server, mock_merge_service) -> None:
        from backend.services.merge_service import MergeResult, MergeStatus

        job = make_job(id="job-123", repo="/test/repo", branch=None)
        mock_merge_service.request_pr_for_job = AsyncMock(
            return_value=MergeResult(status=MergeStatus.error, error="No branch to create a PR for")
        )
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_pr")(job_id="job-123")
            assert "error" in result


# ── Tracker tool (Story 6.1, CAP-13) ────────────────────────────────


def _make_task_link(**overrides: object):
    from backend.models.domain import TaskLink

    defaults: dict[str, object] = dict(
        id="tl-1",
        project_id="proj-1",
        repo_path="/repo",
        story_node_id=None,
        depends_on=[],
        job_id="job-123",
        tracker_link_id="link-1",
        tracker_ticket_ref="ABC-123",
        prompt_override=None,
        epic_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return TaskLink(**defaults)


class TestTrackerTool:
    @pytest.mark.asyncio
    async def test_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_tracker")(action="comment", job_id=None, value="hi")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_value(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_tracker")(action="comment", job_id="job-123", value=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_job_without_task_link(self, mcp_server) -> None:
        with patch("backend.mcp.server.resolve_tracker_for_job") as mock_resolve:
            from backend.services.tracker_resolution import TrackerResolutionError

            mock_resolve.side_effect = TrackerResolutionError("Job 'job-123' has no associated TaskLink")

            result = await _tool(mcp_server, "codeplane_tracker")(action="comment", job_id="job-123", value="hi")
            assert "error" in result

    @pytest.mark.asyncio
    async def test_comment_creates_approval_via_tracker_write_service(self, mcp_server, mock_approval) -> None:
        from backend.services.tracker_resolution import ResolvedTracker
        from backend.services.tracker_write_service import (
            TrackerWriteAction,
            TrackerWriteOutcome,
            TrackerWriteRequest,
            TrackerWriteService,
            TrackerWriteState,
        )

        resolved = ResolvedTracker(
            tracker_link_id="link-1",
            credential_id="cred-1",
            ticket_ref="ABC-123",
            task_link_id="task-1",
        )
        outcome = TrackerWriteOutcome(
            TrackerWriteState.applied,
            operation_id="operation-1",
            target_kind="comment",
        )

        with (
            patch("backend.mcp.server.resolve_tracker_for_job", AsyncMock(return_value=resolved)),
            patch.object(TrackerWriteService, "execute", AsyncMock(return_value=outcome)) as execute,
        ):
            result = await _tool(mcp_server, "codeplane_tracker")(
                action="comment",
                job_id="job-123",
                value="Ready for review",
                request_id="request-1",
            )

        assert result["applied"] is True
        assert result["state"] == "applied"
        assert result["ticketRef"] == "ABC-123"
        assert result["trackerLinkId"] == "link-1"
        assert result["action"] == "comment"
        assert result["operationId"] == "operation-1"
        execute.assert_awaited_once_with(
            "job-123",
            TrackerWriteRequest("link-1", "ABC-123", TrackerWriteAction.comment, "Ready for review"),
            source_key="mcp:job-123:request-1",
            task_link_id="task-1",
        )

    @pytest.mark.asyncio
    async def test_rejected_approval_never_dispatches(self, mcp_server, mock_approval) -> None:
        from backend.services.tracker_resolution import ResolvedTracker
        from backend.services.tracker_write_service import (
            TrackerWriteAction,
            TrackerWriteOutcome,
            TrackerWriteRequest,
            TrackerWriteService,
            TrackerWriteState,
        )

        resolved = ResolvedTracker(
            tracker_link_id="link-1",
            credential_id="cred-1",
            ticket_ref="ABC-123",
            task_link_id="task-1",
        )
        outcome = TrackerWriteOutcome(TrackerWriteState.rejected, operation_id="operation-2")

        with (
            patch("backend.mcp.server.resolve_tracker_for_job", AsyncMock(return_value=resolved)),
            patch.object(TrackerWriteService, "execute", AsyncMock(return_value=outcome)) as execute,
            patch(
                "backend.services.tracker_resolution.dispatch_tracker_write",
                AsyncMock(),
            ) as dispatch,
        ):
            result = await _tool(mcp_server, "codeplane_tracker")(action="transition", job_id="job-123", value="Done")

        assert result["applied"] is False
        assert result["state"] == "rejected"
        dispatch.assert_not_awaited()
        execute.assert_awaited_once_with(
            "job-123",
            TrackerWriteRequest("link-1", "ABC-123", TrackerWriteAction.transition, "Done"),
            source_key=None,
            task_link_id="task-1",
        )

    @pytest.mark.asyncio
    async def test_provider_failure_reports_failed(self, mcp_server, mock_approval) -> None:
        from backend.services.tracker_resolution import ResolvedTracker
        from backend.services.tracker_write_service import (
            TrackerWriteAction,
            TrackerWriteOutcome,
            TrackerWriteRequest,
            TrackerWriteService,
            TrackerWriteState,
        )

        resolved = ResolvedTracker(
            tracker_link_id="link-1",
            credential_id="cred-1",
            ticket_ref="ABC-123",
            task_link_id="task-1",
        )
        outcome = TrackerWriteOutcome(TrackerWriteState.failed, "provider unavailable", operation_id="operation-3")

        with (
            patch(
                "backend.mcp.server.resolve_tracker_for_job",
                AsyncMock(return_value=resolved),
            ),
            patch.object(TrackerWriteService, "execute", AsyncMock(return_value=outcome)) as execute,
        ):
            result = await _tool(mcp_server, "codeplane_tracker")(
                action="comment",
                job_id="job-123",
                value="Ready for review",
            )

        assert result["applied"] is False
        assert result["state"] == "failed"
        assert result["error"] == "provider unavailable"
        execute.assert_awaited_once_with(
            "job-123",
            TrackerWriteRequest("link-1", "ABC-123", TrackerWriteAction.comment, "Ready for review"),
            source_key=None,
            task_link_id="task-1",
        )


# ── Approval tool ────────────────────────────────────────────────────


class TestApprovalTool:
    @pytest.mark.asyncio
    async def test_list(self, mcp_server, mock_approval) -> None:
        mock_approval.list_for_job = AsyncMock(return_value=[_make_approval()])
        result = await _tool(mcp_server, "codeplane_approval")(action="list", job_id="job-123")
        assert isinstance(result, dict)
        assert "items" in result
        assert len(result["items"]) == 1
        assert result["items"][0]["id"] == "apr-1"

    @pytest.mark.asyncio
    async def test_list_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_approval")(action="list", job_id=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_resolve_approve(self, mcp_server, mock_approval) -> None:
        resolved = _make_approval(resolved_at=datetime.now(UTC), resolution="approved")
        mock_approval.resolve = AsyncMock(return_value=resolved)

        result = await _tool(mcp_server, "codeplane_approval")(
            action="resolve", approval_id="apr-1", resolution="approved"
        )
        assert result["resolution"] == "approved"

    @pytest.mark.asyncio
    async def test_resolve_reject(self, mcp_server, mock_approval) -> None:
        resolved = _make_approval(resolved_at=datetime.now(UTC), resolution="rejected")
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
        result = await _tool(mcp_server, "codeplane_approval")(action="resolve", approval_id=None, resolution=None)
        assert "error" in result


# ── Workspace tool ───────────────────────────────────────────────────


class TestWorkspaceTool:
    @pytest.mark.asyncio
    async def test_list(self, mcp_server, tmp_path) -> None:
        (tmp_path / "hello.txt").write_text("hi")
        job = make_job(id="job-123", repo="/test/repo", worktree_path=str(tmp_path))
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_workspace")(action="list", job_id="job-123")
            assert "items" in result
            names = [e["path"] for e in result["items"]]
            assert "hello.txt" in names

    @pytest.mark.asyncio
    async def test_read(self, mcp_server, tmp_path) -> None:
        (tmp_path / "readme.md").write_text("# Hello")
        job = make_job(id="job-123", repo="/test/repo", worktree_path=str(tmp_path))
        with (
            patch("backend.mcp.server.JobService") as mock_svc_cls,
            patch("backend.mcp.server.GitService"),
        ):
            svc = AsyncMock()
            svc.get_job = AsyncMock(return_value=job)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_workspace")(action="read", job_id="job-123", path="readme.md")
            assert result["content"] == "# Hello"

    @pytest.mark.asyncio
    async def test_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_workspace")(action="list", job_id=None)
        assert "error" in result


# ── Artifact tool ────────────────────────────────────────────────────


class TestArtifactTool:
    @pytest.mark.asyncio
    async def test_list(self, mcp_server) -> None:
        art = _make_artifact()
        with (
            patch("backend.mcp.server.ArtifactService") as mock_svc_cls,
            patch("backend.persistence.artifact_repo.ArtifactRepository"),
        ):
            svc = AsyncMock()
            svc.list_for_job = AsyncMock(return_value=[art])
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_artifact")(action="list", job_id="job-123")
            assert len(result["items"]) == 1
            assert result["items"][0]["name"] == "diff.patch"

    @pytest.mark.asyncio
    async def test_list_missing_job_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_artifact")(action="list", job_id=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get(self, mcp_server) -> None:
        art = _make_artifact()
        with (
            patch("backend.mcp.server.ArtifactService") as mock_svc_cls,
            patch("backend.persistence.artifact_repo.ArtifactRepository"),
        ):
            svc = AsyncMock()
            svc.get = AsyncMock(return_value=art)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_artifact")(action="get", artifact_id="art-1")
            assert result["id"] == "art-1"

    @pytest.mark.asyncio
    async def test_get_not_found(self, mcp_server) -> None:
        with (
            patch("backend.mcp.server.ArtifactService") as mock_svc_cls,
            patch("backend.persistence.artifact_repo.ArtifactRepository"),
        ):
            svc = AsyncMock()
            svc.get = AsyncMock(return_value=None)
            mock_svc_cls.return_value = svc

            result = await _tool(mcp_server, "codeplane_artifact")(action="get", artifact_id="missing")
            assert "error" in result

    @pytest.mark.asyncio
    async def test_get_missing_id(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_artifact")(action="get", artifact_id=None)
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
            result = await _tool(mcp_server, "codeplane_settings")(action="get")
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
            result = await _tool(mcp_server, "codeplane_settings")(action="update", max_concurrent_jobs=5)
            assert result["max_concurrent_jobs"] == 5


# ── Repo tool ────────────────────────────────────────────────────────


class TestRepoTool:
    @pytest.mark.asyncio
    async def test_list(self, mcp_server) -> None:
        with patch("backend.mcp.server.load_config") as mock_cfg:
            cfg = MagicMock()
            cfg.server.host = "127.0.0.1"
            cfg.server.port = 8080
            mock_cfg.return_value = cfg

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"items": ["/test/repo"]}
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_client_cls:
                client = AsyncMock()
                client.get = AsyncMock(return_value=mock_response)
                client.__aenter__ = AsyncMock(return_value=client)
                client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = client

                result = await _tool(mcp_server, "codeplane_repo")(action="list")
                assert result["items"] == ["/test/repo"]

    @pytest.mark.asyncio
    async def test_get(self, mcp_server) -> None:
        with patch("backend.mcp.server.load_config") as mock_cfg:
            cfg = MagicMock()
            cfg.server.host = "127.0.0.1"
            cfg.server.port = 8080
            mock_cfg.return_value = cfg

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"path": "/test/repo", "baseBranch": "main"}
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_client_cls:
                client = AsyncMock()
                client.get = AsyncMock(return_value=mock_response)
                client.__aenter__ = AsyncMock(return_value=client)
                client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = client

                result = await _tool(mcp_server, "codeplane_repo")(action="get", repo_path="/test/repo")
                assert result["path"] == "/test/repo"

    @pytest.mark.asyncio
    async def test_get_missing_path(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_repo")(action="get", repo_path=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_register_requires_project_lifecycle(self, mcp_server) -> None:
        with patch("backend.mcp.server.load_config") as mock_cfg:
            cfg = MagicMock()
            cfg.server.host = "127.0.0.1"
            cfg.server.port = 8080
            mock_cfg.return_value = cfg

            with patch("httpx.AsyncClient") as mock_client_cls:
                client = AsyncMock()
                client.__aenter__ = AsyncMock(return_value=client)
                client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = client

                result = await _tool(mcp_server, "codeplane_repo")(action="register", source="/test/repo")
                assert "codeplane_project" in result["error"]
                client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_remove_requires_project_lifecycle(self, mcp_server) -> None:
        with patch("backend.mcp.server.load_config") as mock_cfg:
            cfg = MagicMock()
            cfg.server.host = "127.0.0.1"
            cfg.server.port = 8080
            mock_cfg.return_value = cfg

            with patch("httpx.AsyncClient") as mock_client_cls:
                client = AsyncMock()
                client.__aenter__ = AsyncMock(return_value=client)
                client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = client

                result = await _tool(mcp_server, "codeplane_repo")(action="remove", repo_path="/test/repo")
                assert "codeplane_project" in result["error"]
                client.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_remove_missing_path(self, mcp_server) -> None:
        result = await _tool(mcp_server, "codeplane_repo")(action="remove", repo_path=None)
        assert "codeplane_project" in result["error"]


# ── Project tool ─────────────────────────────────────────────────────


class TestProjectTool:
    @pytest.mark.asyncio
    async def test_update_forwards_repo_removal_confirmation(self, mcp_server) -> None:
        with patch("backend.mcp.server.load_config") as mock_cfg:
            cfg = MagicMock()
            cfg.server.host = "127.0.0.1"
            cfg.server.port = 8080
            mock_cfg.return_value = cfg
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {"id": "proj-1"}

            with patch("httpx.AsyncClient") as mock_client_cls:
                client = AsyncMock()
                client.patch = AsyncMock(return_value=response)
                client.__aenter__ = AsyncMock(return_value=client)
                client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = client

                result = await _tool(mcp_server, "codeplane_project")(
                    action="update",
                    project_id="proj-1",
                    repo_paths=["/test/repo"],
                    confirm_repo_removal=True,
                )

        assert result == {"id": "proj-1"}
        client.patch.assert_awaited_once_with(
            "http://127.0.0.1:8080/api/settings/projects/proj-1",
            json={"repoPaths": ["/test/repo"], "confirmRepoRemoval": True},
        )


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

            result = await _tool(mcp_server, "codeplane_health")(action="check")
            assert result["status"] == "healthy"
            assert result["active_jobs"] == 2
            assert result["queued_jobs"] == 1

    @pytest.mark.asyncio
    async def test_cleanup(self, mcp_server) -> None:
        with (
            patch("backend.mcp.server.GitService") as mock_git_cls,
            patch("backend.mcp.server.load_config") as mock_cfg,
            patch(
                "backend.persistence.project_repo.ProjectRepository.list_all_repo_paths",
                new=AsyncMock(return_value={"/test/project-only": "proj-1"}),
            ),
        ):
            cfg = MagicMock()
            cfg.repos = ["/test/repo"]
            mock_cfg.return_value = cfg
            git = AsyncMock()
            git.cleanup_worktrees = AsyncMock(return_value=3)
            mock_git_cls.return_value = git

            result = await _tool(mcp_server, "codeplane_health")(action="cleanup")
            assert result["removed"] == 6
            cleaned = {call.args[0] for call in git.cleanup_worktrees.await_args_list}
            assert cleaned == {"/test/repo", "/test/project-only"}
