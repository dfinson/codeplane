import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.models.domain import ApprovalResolution
from backend.models.events import EventKind
from backend.models.execution_contracts import ExecutionContract, ImplementationPin
from backend.persistence.execution_contract_repo import ExecutionContractRepository
from backend.persistence.governance_repo import GovernanceLedger
from backend.services.action_policy.agt_policy import POLICY_VERSION, AgtPolicy, PolicyInput
from backend.services.action_policy.assessment import AssessmentService
from backend.services.action_policy.audit import GovernanceAudit
from backend.services.action_policy.authority import PolicyAuthority
from backend.services.action_policy.classifier import Preset
from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.events.event_bus import EventBus
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime.execution_contracts import ExecutionContracts
from backend.tests.unit.test_approval_service import session_factory as session_factory


async def _services(tmp_path, session_factory):
    component = ImplementationPin(id="test-component", sha256="a" * 64)
    contract = ExecutionContract(
        id="test-contract",
        policy=ImplementationPin(id=POLICY_VERSION, sha256="b" * 64),
        parser=component,
        grouping=component,
        title=component,
    )
    contracts = ExecutionContracts(
        ExecutionContractRepository(session_factory),
        available={contract.id: contract},
        selected_for_new=contract.id,
    )
    pin = await contracts.fresh("job-1", "admission")
    ledger = GovernanceLedger(tmp_path / "governance.db", application_database=tmp_path / "data.db")
    await ledger.initialize()
    audit = GovernanceAudit(tmp_path / "audit", ledger)
    await audit.initialize()
    approvals, events = ApprovalService(session_factory), EventBus()
    provider = SimpleNamespace(complete=AsyncMock())
    assessments = AssessmentService()
    authority = PolicyAuthority(
        contracts=contracts,
        ledger=ledger,
        approvals=approvals,
        events=events,
        assessments=assessments,
        policy=AgtPolicy(),
        provider=provider,
        model="claude-test",
    )
    return authority, pin, ledger, audit, approvals, events, provider


async def test_known_read_records_real_agt_decision_before_permission_release(tmp_path, session_factory):
    authority, pin, ledger, audit, approvals, _, provider = await _services(tmp_path, session_factory)
    result = await authority.authorize(
        pin=pin,
        execution=ExecutionIdentity("job-1", "managed-session", "epoch", "token"),
        local=PolicyInput(job_id="job-1", preset=Preset.supervised, trusted_file_read=True),
        fields={"command": "", "arguments": '{"path":"file"}', "tool_definition": "file read", "context": ""},
        is_current=AsyncMock(return_value=True),
    )
    assert result.proceed and result.decision.reason_code == "known_read"
    provider.complete.assert_not_awaited()
    assert await approvals.list_pending("job-1") == []
    assert await audit.deliver_one()
    recorded = audit._entries(0)[-1].data
    assert recorded["decision_id"] == result.decision_id
    assert recorded["action_digest"] == result.binding.sha256
    assert recorded["metadata"]["admission_id"] == pin.admission_id
    assert recorded["metadata"]["budget_snapshot"]["verified_usd"] is None
    assert (await ledger.job_totals("job-1")).executed_calls == 0


@pytest.mark.parametrize(
    "resolution,current,allowed",
    [
        (ApprovalResolution.approved, True, True),
        (ApprovalResolution.rejected, True, False),
        (ApprovalResolution.approved, False, False),
    ],
)
async def test_durable_hard_gate_and_final_live_fence_control_permission(
    tmp_path,
    session_factory,
    resolution,
    current,
    allowed,
):
    authority, pin, _, audit, approvals, events, provider = await _services(tmp_path, session_factory)
    requested = asyncio.Event()
    alive = True

    async def on_event(event):
        if event.kind == EventKind.approval_requested:
            requested.set()

    async def is_current():
        return alive

    events.subscribe(on_event)
    pending = asyncio.create_task(
        authority.authorize(
            pin=pin,
            execution=ExecutionIdentity("job-1", "managed-session", "epoch", "token"),
            local=PolicyInput(job_id="job-1", preset=Preset.autonomous, hard_gate=True),
            fields={"command": "git reset --hard HEAD", "arguments": "", "tool_definition": "", "context": ""},
            dialect="bash",
            is_current=is_current,
        )
    )
    try:
        await asyncio.wait_for(requested.wait(), timeout=2)
        (approval,) = await approvals.list_pending("job-1")
        assert approval.requires_explicit_approval
        assert not pending.done()
        alive = current
        await approvals.resolve(approval.id, resolution)
        result = await asyncio.wait_for(pending, timeout=2)
        assert result.proceed is allowed
        assert result.approval_id == approval.id
        provider.complete.assert_not_awaited()
        assert await audit.deliver_one()
        assert audit._entries(0)[-1].data["decision"] == ("allow" if allowed else "reject")
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_audit_enqueue_failure_cannot_release_permission(tmp_path, session_factory, monkeypatch):
    authority, pin, ledger, _, _, _, _ = await _services(tmp_path, session_factory)
    monkeypatch.setattr(ledger, "enqueue_decision", AsyncMock(side_effect=OSError("Database unavailable")))
    result = await authority.authorize(
        pin=pin,
        execution=ExecutionIdentity("job-1", "managed-session", "epoch", "token"),
        local=PolicyInput(job_id="job-1", preset=Preset.supervised, trusted_file_read=True),
        fields={"command": "", "arguments": "", "tool_definition": "", "context": ""},
        is_current=AsyncMock(return_value=True),
    )
    assert not result.proceed
