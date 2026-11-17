import asyncio
from unittest.mock import AsyncMock, MagicMock

from backend.config import CPLConfig
from backend.models.domain import SessionConfig
from backend.persistence.event_repo import EventRepository
from backend.services.action_policy.production import StandaloneGovernance
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.base_adapter import PermissionDecision
from backend.services.auth.permission_policy import PermissionRequest
from backend.services.events.event_bus import EventBus
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime import RuntimeService
from backend.tests.unit.test_approval_service import session_factory as session_factory


async def test_real_root_catalogue_pin_policy_and_audit_reach_the_adapter_boundary(session_factory, tmp_path):
    events = EventBus()
    approvals = ApprovalService(session_factory)
    registry = AdapterRegistry(approval_service=approvals, event_bus=events, session_factory=session_factory)
    host = RuntimeService(
        session_factory=session_factory,
        event_bus=events,
        adapter_registry=registry,
        config=CPLConfig(),
        approval_service=approvals,
        git_service=MagicMock(),
    )
    root = StandaloneGovernance(host, tmp_path)
    await root.start()
    host._standalone_governance = root
    try:
        pin = await host.prepare_execution_contract("job-1", "admitted", recovery=False)
        assert pin is not None
        assert len(pin.contract.mappings) == 24 and len(pin.contract.grammars) == 4
        host._job_contract_pins["job-1"] = pin
        config = SessionConfig(workspace_path=str(tmp_path), prompt="", job_id="job-1")
        await host._setup_action_policy("job-1", config, str(tmp_path))
        adapter = registry.get_adapter("copilot")
        adapter.complete = AsyncMock()
        adapter.set_job_id("native-session", "job-1")
        adapter.set_session_metadata("native-session", config)
        decision = await adapter._evaluate_permission(
            "native-session",
            "job-1",
            PermissionRequest(kind="read", workspace_path=str(tmp_path), path="source.py"),
            tool_name="view",
            tool_input={"path": "source.py"},
            tool_call_id="read-1",
        )
        assert decision == PermissionDecision.allow
        adapter.complete.assert_not_awaited()
        assert host._governance_decider is None
        assert adapter._sessions == {}, "Policy setup must not create an SDK client or session"
        async with session_factory() as session:
            retained = await EventRepository(session).list_after(0, job_id="job-1")
        handles = [item.event for item in retained if item.event.kind == "execution.contract.handle_bound"]
        assert len(handles) == 1
        token = handles[0].payload["execution_token"]
        recorded = await root._ledger.authorized_tool("job-1", host.execution_epoch, token, "read-1")
        assert recorded is not None and recorded.decision == "allow"
        assert recorded.metadata["admission_id"] == pin.admission_id
        await root._audit.deliver_one()
        assert root._audit._entries(0)[-1].data["action_digest"] == recorded.action_digest
    finally:
        await root.close()
        await host.shutdown()


async def test_root_cancellation_during_human_wait_cannot_release_an_old_permission(session_factory, tmp_path):
    events = EventBus()
    approvals = ApprovalService(session_factory)
    registry = AdapterRegistry(approval_service=approvals, event_bus=events, session_factory=session_factory)
    host = RuntimeService(
        session_factory=session_factory,
        event_bus=events,
        adapter_registry=registry,
        config=CPLConfig(),
        approval_service=approvals,
        git_service=MagicMock(),
    )
    root = StandaloneGovernance(host, tmp_path)
    await root.start()
    host._standalone_governance = root
    entered = asyncio.Event()

    async def requested(event):
        if event.kind == "permission.requested":
            entered.set()

    events.subscribe(requested)
    pending = None
    try:
        pin = await host.prepare_execution_contract("job-1", "admitted", recovery=False)
        host._job_contract_pins["job-1"] = pin
        config = SessionConfig(workspace_path=str(tmp_path), prompt="", job_id="job-1")
        await host._setup_action_policy("job-1", config, str(tmp_path))
        adapter = registry.get_adapter("copilot")
        adapter.set_job_id("session", "job-1")
        adapter.set_session_metadata("session", config)
        pending = asyncio.create_task(
            adapter._evaluate_permission(
                "session",
                "job-1",
                PermissionRequest(
                    kind="shell",
                    workspace_path=str(tmp_path),
                    full_command_text="git reset --hard HEAD",
                ),
                tool_name="bash",
                tool_input={"command": "git reset --hard HEAD"},
                tool_call_id="hard-1",
                dialect="bash",
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=3)
        (approval,) = await approvals.list_pending("job-1")
        assert approval.requires_explicit_approval
        adapter.pause_tools("session")
        adapter.resume_tools("session")
        from backend.models.domain import ApprovalResolution

        await approvals.resolve(approval.id, ApprovalResolution.approved)
        assert await asyncio.wait_for(pending, timeout=3) == PermissionDecision.deny
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await root.close()
        await host.shutdown()
