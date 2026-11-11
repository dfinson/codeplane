"""Coordinated dispatch is fenced to an exact live execution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from backend.config import CPLConfig
from backend.services.completers.copilot_steer import NativeDeliveryError
from backend.services.events.event_bus import EventBus
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime import RuntimeService
from backend.tests.unit.test_approval_service import session_factory as _source_factory

session_factory = _source_factory


def _live_runtime(session_factory):
    approvals = ApprovalService(session_factory)
    runtime = RuntimeService(
        session_factory=session_factory,
        event_bus=EventBus(),
        adapter_registry=MagicMock(),
        config=CPLConfig(),
        approval_service=approvals,
    )
    handle = SimpleNamespace(session_id="sdk-fixture", send_message=AsyncMock(), resume_tools=MagicMock())
    runtime._agent_sessions["job-1"] = handle
    runtime._resume_orphaned = AsyncMock()
    return runtime, approvals, handle


class TestRuntimeContract:
    async def test_missing_execution_never_resumes(self, session_factory):
        runtime = RuntimeService(
            session_factory=session_factory,
            event_bus=EventBus(),
            adapter_registry=MagicMock(),
            config=CPLConfig(),
        )
        runtime._resume_orphaned = AsyncMock()
        dispatch = getattr(runtime, "dispatch_current_execution", None)
        assert dispatch is not None, "A distinct strict dispatch contract is required"
        result = await dispatch("job-1", "stale-token", "fixture")
        assert result.status == "conflict"
        assert result.reason == "no_live_execution"
        runtime._resume_orphaned.assert_not_awaited()


async def test_dispatch_refuses_replaced_generation(session_factory):
    runtime, _, first = _live_runtime(session_factory)
    preview = await runtime.get_current_execution_context("job-1")
    replacement = SimpleNamespace(session_id=first.session_id, send_message=AsyncMock(), resume_tools=MagicMock())
    runtime._agent_sessions["job-1"] = replacement
    receipt = await runtime.dispatch_current_execution("job-1", preview.execution_token, "fixture")
    assert receipt.status == "conflict" and receipt.reason == "stale_execution"
    replacement.send_message.assert_not_awaited()
    replacement.resume_tools.assert_not_called()
    runtime._resume_orphaned.assert_not_awaited()


async def test_pending_permission_prevents_dispatch_without_unpausing(session_factory):
    runtime, approvals, handle = _live_runtime(session_factory)
    preview = await runtime.get_current_execution_context("job-1")
    request = await approvals.create_request("job-1", "fixture hard gate", requires_explicit_approval=True)
    receipt = await runtime.dispatch_current_execution("job-1", preview.execution_token, "fixture")
    assert receipt.status == "conflict" and receipt.reason == "approval_pending"
    handle.send_message.assert_not_awaited()
    handle.resume_tools.assert_not_called()
    assert (await approvals.get(request.id)).resolution is None


async def test_dispatch_reports_uncertainty_without_retry(session_factory):
    runtime, _, handle = _live_runtime(session_factory)
    preview = await runtime.get_current_execution_context("job-1")
    handle.send_message.side_effect = NativeDeliveryError("fixture lost receipt", uncertain=True)
    receipt = await runtime.dispatch_current_execution("job-1", preview.execution_token, "fixture")
    assert receipt.status == "uncertain" and receipt.reason == "delivery_unknown"
    handle.send_message.assert_awaited_once()
    handle.resume_tools.assert_not_called()


async def test_dispatch_accepts_only_live_ungated_generation(session_factory):
    runtime, _, handle = _live_runtime(session_factory)
    preview = await runtime.get_current_execution_context("job-1")
    receipt = await runtime.dispatch_current_execution("job-1", preview.execution_token, "fixture")
    assert receipt.status == "accepted"
    assert receipt.execution_token == preview.execution_token
    assert receipt.message_event_id
    handle.send_message.assert_awaited_once_with("fixture")
    handle.resume_tools.assert_not_called()
