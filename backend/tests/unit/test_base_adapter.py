"""Tests for backend.services.adapters.base_adapter — shared adapter infrastructure.

Covers the pure-logic helpers and state management that don't require
a running SDK subprocess:
  - _build_permission_description formatting
  - Transcript ring buffer
  - Queue / session state management
  - Permission description building
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.adapters.base_adapter import BaseAgentAdapter, PermissionDecision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ConcreteAdapter(BaseAgentAdapter):
    """Minimal concrete subclass for testing shared BaseAgentAdapter logic."""

    async def create_session(self, *a, **kw):
        raise NotImplementedError

    async def send_message(self, *a, **kw):
        raise NotImplementedError

    async def abort_session(self, *a, **kw):
        raise NotImplementedError

    async def complete(self, *a, **kw):
        raise NotImplementedError

    async def stream_events(self, *a, **kw):
        raise NotImplementedError


def _make_adapter(**kwargs) -> BaseAgentAdapter:
    """Create a concrete BaseAgentAdapter with mocked collaborators."""
    defaults = {
        "approval_service": None,
        "event_bus": None,
        "session_factory": None,
    }
    defaults.update(kwargs)
    return _ConcreteAdapter(**defaults)


# ===================================================================
# _build_permission_description — pure static
# ===================================================================


class TestBuildPermissionDescription:
    def test_shell_with_command(self) -> None:
        desc = BaseAgentAdapter._build_permission_description("shell", "Bash", {"command": "ls -la"}, "ls -la")
        assert desc.startswith("Run shell:")
        assert "ls -la" in desc

    def test_shell_no_input(self) -> None:
        desc = BaseAgentAdapter._build_permission_description("shell", "Bash", None, "echo hi")
        assert "echo hi" in desc

    def test_write_file(self) -> None:
        desc = BaseAgentAdapter._build_permission_description("write", "Edit", {"file_path": "/tmp/foo.py"}, None)
        assert desc.startswith("Write file:")
        assert "/tmp/foo.py" in desc

    def test_web_search(self) -> None:
        desc = BaseAgentAdapter._build_permission_description("search", "WebSearch", {"query": "python async"}, None)
        assert "Web search:" in desc
        assert "python async" in desc

    def test_web_fetch(self) -> None:
        desc = BaseAgentAdapter._build_permission_description("url", "WebFetch", {"url": "https://example.com"}, None)
        assert "Fetch URL:" in desc

    def test_read_file(self) -> None:
        desc = BaseAgentAdapter._build_permission_description("read", "Read", {"file_path": "/etc/hosts"}, None)
        assert desc.startswith("Read file:")

    def test_generic_tool(self) -> None:
        desc = BaseAgentAdapter._build_permission_description("custom", "MyTool", {"arg": "val"}, None)
        assert desc.startswith("MyTool:")

    def test_fallback_to_command_text(self) -> None:
        desc = BaseAgentAdapter._build_permission_description("unknown", "", None, "some raw text")
        assert desc == "some raw text"

    def test_fallback_to_kind(self) -> None:
        desc = BaseAgentAdapter._build_permission_description("unknown", "", None, None)
        assert desc == "unknown"


# ===================================================================
# Queue and session state management
# ===================================================================


class TestSessionState:
    def test_attempt_models_are_verified_independently_without_job_default_writes(self) -> None:
        from backend.models.domain import SessionConfig
        from backend.models.events import EventKind

        adapter = _make_adapter()
        adapter._schedule_db_write = MagicMock()
        adapter._job_main_models["job"] = "primary-model"
        for sid, attempt in (("first", "attempt-one"), ("second", "attempt-two")):
            adapter._queues[sid] = asyncio.Queue()
            adapter.set_job_id(sid, "job")
            adapter.set_session_metadata(
                sid,
                SessionConfig(
                    workspace_path="/repo",
                    prompt="",
                    job_id="job",
                    attempt_id=attempt,
                    execution_epoch="epoch",
                    recipe_run_id="run",
                ),
            )
        adapter._verify_and_set_model("first", "job", "served-one", "requested-one")
        adapter._verify_and_set_model("second", "job", "served-two", "requested-two")
        assert adapter.get_reported_model("first") == "served-one"
        assert adapter.get_reported_model("second") == "served-two"
        assert adapter._job_main_models["job"] == "primary-model"
        adapter._schedule_db_write.assert_not_called()
        for sid, attempt, actual in (
            ("first", "attempt-one", "served-one"),
            ("second", "attempt-two", "served-two"),
        ):
            observed = adapter._queues[sid].get_nowait()
            mismatch = adapter._queues[sid].get_nowait()
            assert observed.kind == EventKind.model_observed
            assert mismatch.kind == EventKind.model_downgraded
            assert observed.payload["actual_model"] == actual
            assert observed.payload["model_match"] is False
            assert mismatch.metadata.attempt_id == observed.metadata.attempt_id == attempt
            assert observed.metadata.run_id == sid and observed.metadata.execution_epoch == "epoch"

    def test_set_job_id(self) -> None:
        adapter = _make_adapter()
        adapter.set_job_id("s1", "j1")
        assert adapter._session_to_job["s1"] == "j1"
        assert "j1" in adapter._job_start_times

    def test_pause_and_resume_tools(self) -> None:
        adapter = _make_adapter()
        adapter.pause_tools("s1")
        assert "s1" in adapter._paused_sessions
        adapter.resume_tools("s1")
        assert "s1" not in adapter._paused_sessions

    def test_resume_nonexistent_is_noop(self) -> None:
        adapter = _make_adapter()
        adapter.resume_tools("nonexistent")  # should not raise

    def test_cleanup_session_state(self) -> None:
        adapter = _make_adapter()
        adapter.set_job_id("s1", "j1")
        adapter._queues["s1"] = asyncio.Queue()
        adapter._clients["s1"] = object()
        adapter._paused_sessions.add("s1")
        adapter._current_phases["j1"] = "agent_reasoning"

        adapter._cleanup_session_state("s1")

        assert "s1" not in adapter._session_to_job
        assert "s1" not in adapter._queues
        assert "s1" not in adapter._clients
        assert "s1" not in adapter._paused_sessions
        assert "j1" not in adapter._job_start_times
        assert "j1" not in adapter._current_phases

    def test_cleanup_unknown_session(self) -> None:
        adapter = _make_adapter()
        adapter._cleanup_session_state("nonexistent")  # should not raise

    def test_set_execution_phase(self) -> None:
        from backend.models.api_schemas import ExecutionPhase

        adapter = _make_adapter()
        adapter.set_execution_phase("j1", ExecutionPhase.agent_reasoning)
        assert adapter._current_phases["j1"] == ExecutionPhase.agent_reasoning


# ===================================================================
# Enqueue helpers
# ===================================================================


class TestEnqueue:
    def test_enqueue_to_existing_queue(self) -> None:
        from backend.models.events import EventKind, SessionEvent, new_event

        adapter = _make_adapter()
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues["s1"] = q
        evt = new_event(session_id="j1", kind=EventKind.log_line_emitted, payload={"msg": "hi"})
        adapter._enqueue("s1", evt)
        assert q.qsize() == 1

    def test_enqueue_to_missing_queue(self) -> None:
        from backend.models.events import EventKind, new_event

        adapter = _make_adapter()
        evt = new_event(session_id="j1", kind=EventKind.log_line_emitted, payload={"msg": "hi"})
        adapter._enqueue("no-queue", evt)  # should not raise


# ===================================================================
# DB write scheduling (backpressure)
# ===================================================================


class TestScheduleDbWrite:
    @pytest.mark.asyncio
    async def test_schedule_write_creates_task(self) -> None:
        adapter = _make_adapter()
        called = asyncio.Event()

        async def fake_coro() -> None:
            called.set()

        adapter._schedule_db_write(fake_coro())
        await asyncio.sleep(0.01)
        assert called.is_set()

    @pytest.mark.asyncio
    async def test_backpressure_drops_writes(self) -> None:
        adapter = _make_adapter()
        # Fill with fake pending tasks that never complete
        for _ in range(adapter._MAX_PENDING_WRITES):

            async def _block() -> None:
                await asyncio.sleep(999)

            task = asyncio.create_task(_block())
            adapter._write_tasks.append(task)

        dropped = asyncio.Event()

        async def should_not_run() -> None:
            dropped.set()

        adapter._schedule_db_write(should_not_run())
        await asyncio.sleep(0.01)
        assert not dropped.is_set()

        # Clean up
        for t in adapter._write_tasks:
            t.cancel()
        await asyncio.sleep(0.01)


# ===================================================================
# Permission evaluation — paused / trust bypass / hard block
# ===================================================================


class TestEvaluatePermission:
    async def test_attempt_checkpoint_and_policy_use_trusted_session_workspace(self) -> None:
        from backend.models.domain import SessionConfig
        from backend.services.auth.permission_policy import PermissionRequest

        adapter = _make_adapter()
        router = MagicMock()
        router.route = AsyncMock(return_value=MagicMock(proceed=True, classification=None))
        adapter._policy_router["job"] = router
        adapter._worktree_paths["job"] = "/primary"
        adapter.set_session_metadata(
            "attempt-session",
            SessionConfig(
                workspace_path="/attempt",
                prompt="",
                job_id="job",
                attempt_id="attempt",
                execution_epoch="epoch",
            ),
        )
        decision = await adapter._evaluate_permission(
            "attempt-session",
            "job",
            PermissionRequest(kind="read", workspace_path="/untrusted"),
            tool_name="Read",
        )
        assert decision == PermissionDecision.allow
        assert router.route.call_args.kwargs["cwd"] == "/attempt"
        assert router.route.call_args.args[0].workspace_path == "/attempt"

    @pytest.mark.asyncio
    async def test_paused_session_denied(self) -> None:
        adapter = _make_adapter()
        adapter._paused_sessions.add("s1")
        from backend.services.auth.permission_policy import PermissionRequest

        result = await adapter._evaluate_permission(
            "s1",
            "j1",
            PermissionRequest(kind="shell", workspace_path=""),
            tool_name="Bash",
        )
        assert result == PermissionDecision.deny

    @pytest.mark.asyncio
    async def test_trusted_job_allowed(self) -> None:
        """Trusted jobs are allowed when a policy router is set up (trust goes via router)."""
        adapter = _make_adapter()

        # Set up a policy router that always allows (simulating trust coverage)
        from unittest.mock import AsyncMock

        mock_decision = MagicMock(proceed=True, checkpoint_ref=None, classification=None)
        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value=mock_decision)
        adapter._policy_router["j1"] = mock_router
        adapter._repo_policies["j1"] = MagicMock()
        adapter._worktree_paths["j1"] = "/tmp"

        from backend.services.auth.permission_policy import PermissionRequest

        result = await adapter._evaluate_permission(
            "s1",
            "j1",
            PermissionRequest(kind="read", workspace_path=""),
            tool_name="Read",
        )
        assert result == PermissionDecision.allow

    @pytest.mark.asyncio
    async def test_git_reset_hard_blocked(self) -> None:
        mock_approval = MagicMock()
        mock_approval.create_request = AsyncMock(return_value=MagicMock(id="a1"))
        mock_approval.wait_for_resolution = AsyncMock(return_value="denied")
        adapter = _make_adapter(approval_service=mock_approval)
        adapter._queues["s1"] = asyncio.Queue()

        from backend.services.auth.permission_policy import PermissionRequest

        result = await adapter._evaluate_permission(
            "s1",
            "j1",
            PermissionRequest(kind="shell", workspace_path="", full_command_text="git reset --hard HEAD~1"),
            tool_name="Bash",
        )
        assert result == PermissionDecision.deny

    @pytest.mark.asyncio
    async def test_git_reset_hard_approved(self) -> None:
        mock_approval = MagicMock()
        mock_approval.create_request = AsyncMock(return_value=MagicMock(id="a1"))
        mock_approval.wait_for_resolution = AsyncMock(return_value="approved")
        adapter = _make_adapter(approval_service=mock_approval)
        adapter._queues["s1"] = asyncio.Queue()

        from backend.services.auth.permission_policy import PermissionRequest

        result = await adapter._evaluate_permission(
            "s1",
            "j1",
            PermissionRequest(kind="shell", workspace_path="", full_command_text="git reset --hard HEAD~1"),
            tool_name="Bash",
        )
        assert result == PermissionDecision.allow
