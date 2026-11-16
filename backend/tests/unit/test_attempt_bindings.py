import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import CPLConfig
from backend.models.domain import SessionConfig
from backend.services.action_policy.classifier import Preset, RepoPolicy
from backend.services.auth.permission_policy import PermissionRequest
from backend.services.claude_adapter import ClaudeAdapter
from backend.services.copilot_adapter import CopilotAdapter
from backend.services.events.event_bus import EventBus
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime import RuntimeService
from backend.services.runtime.attempt_bindings import AttemptBindingConflictError, AttemptBindings
from backend.tests.unit.test_approval_service import session_factory as session_factory


def test_binding_replacement_fences_old_token_and_scopes_by_job() -> None:
    bindings = AttemptBindings("epoch")
    adapter = AsyncMock()
    first = bindings.register("job-1", "attempt-1", adapter, "real-session", "copilot")
    second = bindings.register("job-2", "attempt-1", adapter, "other-session", "copilot")
    bindings.unregister("job-1", "attempt-1", first.token)
    replacement = bindings.register("job-1", "attempt-1", adapter, "real-session", "copilot")
    assert replacement.token != first.token
    with pytest.raises(AttemptBindingConflictError):
        bindings.unregister("job-1", "attempt-1", first.token)
    assert bindings.get("job-1", "attempt-1") == replacement
    assert bindings.get("job-2", "attempt-1") == second
    assert bindings.get("job-2", "missing") is None


def test_two_attempts_cannot_own_the_same_actual_session() -> None:
    bindings = AttemptBindings("epoch")
    adapter = AsyncMock()
    bindings.register("job-1", "attempt-1", adapter, "real-session", "copilot")
    with pytest.raises(AttemptBindingConflictError):
        bindings.register("job-2", "attempt-2", adapter, "real-session", "copilot")


def test_register_does_not_replace_existing_attempt_or_launch_anything() -> None:
    bindings = AttemptBindings("epoch")
    adapter = AsyncMock()
    bindings.register("job-1", "attempt-1", adapter, "real-session", "copilot")
    with pytest.raises(AttemptBindingConflictError):
        bindings.register("job-1", "attempt-1", adapter, "replacement-session", "copilot")
    adapter.create_session.assert_not_called()


def _runtime(session_factory):
    adapter = MagicMock()
    adapter.send_message = AsyncMock()
    adapter.abort_session = AsyncMock()
    registry = MagicMock()
    registry.get_adapter.return_value = adapter
    approvals = ApprovalService(session_factory)
    runtime = RuntimeService(
        session_factory=session_factory,
        event_bus=EventBus(),
        adapter_registry=registry,
        config=CPLConfig(),
        approval_service=approvals,
    )
    primary = SimpleNamespace(session_id="primary", send_message=AsyncMock())
    runtime._agent_sessions["job-1"] = primary
    return runtime, adapter, approvals, primary


async def test_runtime_targets_exact_attempt_not_primary_or_sibling(session_factory):
    runtime, adapter, _, primary = _runtime(session_factory)
    first = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")
    second = await runtime.register_attempt_execution("job-1", "second", adapter, "session-second")
    stale = await runtime.dispatch_current_execution("job-1", first.execution_token, "hi", attempt_id="second")
    assert stale.reason == "stale_execution"
    missing = await runtime.dispatch_current_execution("job-1", first.execution_token, "hi", attempt_id="missing")
    assert missing.reason == "no_live_execution"
    result = await runtime.dispatch_current_execution("job-1", second.execution_token, "hi", attempt_id="second")
    assert result.status == "accepted"
    adapter.send_message.assert_awaited_once_with("session-second", "hi")
    primary.send_message.assert_not_awaited()
    await runtime.cancel_attempt_execution("job-1", "first", first.execution_token)
    adapter.abort_session.assert_awaited_once_with("session-first")
    assert (await runtime.get_current_execution_context("job-1", attempt_id="first")).execution_token is None
    assert (await runtime.get_current_execution_context("job-1", attempt_id="second")).execution_token == (
        second.execution_token
    )


async def test_runtime_pending_approval_blocks_attempt_dispatch_and_registration(session_factory):
    runtime, adapter, approvals, _ = _runtime(session_factory)
    context = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")
    await approvals.create_request("job-1", "Await explicit operator decision", requires_explicit_approval=True)
    result = await runtime.dispatch_current_execution("job-1", context.execution_token, "hi", attempt_id="first")
    assert result.reason == "approval_pending"
    adapter.send_message.assert_not_awaited()
    with pytest.raises(AttemptBindingConflictError, match="pending approval"):
        await runtime.register_attempt_execution("job-1", "second", adapter, "session-second")


async def test_unconfirmed_stop_keeps_handle_but_cannot_be_steered(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    context = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")
    adapter.abort_session.side_effect = ConnectionError("Transport lost")
    with pytest.raises(ConnectionError):
        await runtime.cancel_attempt_execution("job-1", "first", context.execution_token)
    binding = runtime._attempt_bindings.get("job-1", "first")
    assert binding is not None and binding.cancel_requested
    result = await runtime.dispatch_current_execution("job-1", context.execution_token, "hi", attempt_id="first")
    assert result.reason == "no_live_execution"
    adapter.send_message.assert_not_awaited()


async def test_completed_owner_can_unregister_during_real_stop_without_deadlock(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    context = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")

    async def finish(session_id):
        await runtime.unregister_attempt_execution("job-1", "first", context.execution_token)

    adapter.abort_session.side_effect = finish
    await runtime.cancel_attempt_execution("job-1", "first", context.execution_token)
    assert runtime._attempt_bindings.get("job-1", "first") is None


async def test_owned_deferred_session_is_closed_when_registration_fails(session_factory):
    runtime, adapter, approvals, _ = _runtime(session_factory)

    async def create_session(config):
        await approvals.create_request("job-1", "New gate during SDK creation", requires_explicit_approval=True)
        return "inert-session"

    adapter.create_session = AsyncMock(side_effect=create_session)
    config = SessionConfig(workspace_path="/test", prompt="must not execute", job_id="job-1")
    with pytest.raises(AttemptBindingConflictError):
        async with runtime.open_attempt_execution("job-1", "attempt", config):
            pytest.fail("A pending approval must prevent an attempt becoming dispatchable")
    assert adapter.create_session.call_args.args[0].defer_start
    adapter.send_message.assert_not_awaited()
    adapter.abort_session.assert_awaited_once_with("inert-session")
    assert runtime._attempt_bindings.get("job-1", "attempt") is None


async def test_attempt_policy_reuses_job_router_across_providers_without_workspace_or_budget_reset(session_factory):
    runtime, _, _, _ = _runtime(session_factory)
    runtime._config.runtime.copilot_only = False
    copilot, claude = CopilotAdapter(), ClaudeAdapter()
    runtime._adapter_registry.get_adapter.side_effect = lambda sdk: copilot if str(sdk) == "copilot" else claude
    router = SimpleNamespace(route=AsyncMock(return_value=SimpleNamespace(proceed=True, classification=None)))
    policy = RepoPolicy(preset=Preset.supervised)
    runtime._policy_routers["job-1"] = router
    runtime._repo_policies["job-1"] = policy
    runtime._governance_decider = MagicMock()
    copilot.set_policy_router(router, policy, "job-1", "/primary")
    for provider, adapter, attempt, workspace in (
        ("copilot", copilot, "first", "/worktree-first"),
        ("claude", claude, "second", "/worktree-second"),
    ):
        sid = f"session-{attempt}"
        adapter.set_job_id(sid, "job-1")
        adapter._queues[sid] = asyncio.Queue()
        adapter.set_session_metadata(sid, SessionConfig(
            workspace_path=workspace, prompt="", job_id="job-1",
            attempt_id=attempt, execution_epoch=runtime.execution_epoch,
        ))
        context = await runtime.register_attempt_execution("job-1", attempt, adapter, sid, sdk=provider)
        await runtime.attach_attempt_policy("job-1", attempt, context.execution_token)
        assert adapter._policy_router["job-1"] is router
        assert adapter._repo_policies["job-1"] is policy
        adapter._verify_and_set_model(sid, "job-1", f"actual-{attempt}", "")
        observed = adapter._queues[sid].get_nowait()
        assert observed.metadata.run_id == sid
        assert observed.metadata.execution_token == context.execution_token
        await adapter._evaluate_with_policy_router(
            sid, "job-1", PermissionRequest(kind="write", workspace_path="/untrusted", file_name="file"),
            tool_name="write", tool_input={"path": "file"},
        )
        action = router.route.call_args.args[0]
        assert action.workspace_path == workspace
        assert router.route.call_args.kwargs["cwd"] == workspace
    assert copilot._worktree_paths["job-1"] == "/primary"
    assert "job-1" not in claude._worktree_paths
    runtime._governance_decider.register_job.assert_not_called()


async def test_attempt_policy_attachment_rejects_stale_tokens_and_missing_job_policy(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    context = await runtime.register_attempt_execution("job-1", "attempt", adapter, "session")
    with pytest.raises(AttemptBindingConflictError, match="current execution token"):
        await runtime.attach_attempt_policy("job-1", "attempt", "stale")
    with pytest.raises(AttemptBindingConflictError, match="not been configured"):
        await runtime.attach_attempt_policy("job-1", "attempt", context.execution_token)
    adapter.attach_attempt_policy.assert_not_called()


async def test_cancellation_after_registration_cleans_handle_and_refuses_late_dispatch(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    adapter.create_session = AsyncMock(return_value="inert-session")
    config = SessionConfig(workspace_path="/test", prompt="must not execute", job_id="job-1")
    registered = asyncio.Event()
    token = None

    async def open_and_wait():
        nonlocal token
        async with runtime.open_attempt_execution("job-1", "attempt", config) as execution:
            token = execution.execution_token
            registered.set()
            await asyncio.Event().wait()
            await runtime.dispatch_current_execution("job-1", token, config.prompt, attempt_id="attempt")

    task = asyncio.create_task(open_and_wait())
    await registered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    adapter.abort_session.assert_awaited_once_with("inert-session")
    adapter.send_message.assert_not_awaited()
    receipt = await runtime.dispatch_current_execution("job-1", token, config.prompt, attempt_id="attempt")
    assert receipt.reason == "no_live_execution"


async def test_hung_abort_cannot_delay_sibling_fences_or_primary_cancellation(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    first = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")
    second = await runtime.register_attempt_execution("job-1", "second", adapter, "session-second")
    primary_started, primary_cancelled = asyncio.Event(), asyncio.Event()
    abort_entered, release_abort = asyncio.Event(), asyncio.Event()

    async def primary():
        primary_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            primary_cancelled.set()

    async def abort(session_id):
        if session_id == "session-first":
            abort_entered.set()
            await release_abort.wait()

    task = asyncio.create_task(primary())
    runtime._tasks["job-1"] = task
    adapter.abort_session.side_effect = abort
    await primary_started.wait()
    cancel = asyncio.create_task(runtime.cancel("job-1"))
    try:
        await abort_entered.wait()
        await asyncio.wait_for(primary_cancelled.wait(), timeout=1)
        for attempt, token in (("first", first.execution_token), ("second", second.execution_token)):
            binding = runtime._attempt_bindings.get("job-1", attempt)
            assert binding is not None and binding.cancel_requested
            receipt = await runtime.dispatch_current_execution("job-1", token, "late", attempt_id=attempt)
            assert receipt.reason == "no_live_execution"
        assert any(call.args == ("session-second",) for call in adapter.pause_tools.call_args_list)
        assert not cancel.done()
    finally:
        release_abort.set()
        await cancel
        with pytest.raises(asyncio.CancelledError):
            await task
