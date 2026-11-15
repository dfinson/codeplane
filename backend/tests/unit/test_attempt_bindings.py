from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import CPLConfig
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
