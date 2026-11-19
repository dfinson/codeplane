import asyncio
from decimal import Decimal
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
from backend.services.action_policy.classifier import Action, ActionKind, Preset, RepoPolicy
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
@pytest.mark.parametrize("command", ["git reset --hard HEAD", "env LANG=C git -C . merge topic"])
async def test_durable_hard_gate_and_final_live_fence_control_permission(
    tmp_path,
    session_factory,
    resolution,
    current,
    allowed,
    command,
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
            local=PolicyInput(job_id="job-1", preset=Preset.autonomous),
            fields={"command": command, "arguments": "", "tool_definition": "", "context": ""},
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


async def _verified_spend(ledger, amount="0"):
    from backend.models.usage import UsageAmount

    await ledger.record_usage(
        "verified-usage",
        UsageAmount(
            amount=Decimal(amount),
            unit="USD",
            currency="USD",
            verified=True,
            producer="fixture",
            role="primary",
            source="provider.cost",
            job_id="job-1",
            execution_epoch="epoch",
            model=None,
        ),
    )


def _read_arguments(pin, settings):
    return dict(
        pin=pin,
        execution=ExecutionIdentity("job-1", "managed-session", "epoch", "token"),
        local=PolicyInput(job_id="job-1", preset=settings.preset, trusted_file_read=True),
        fields={"command": "", "arguments": '{"path":"source.py"}', "tool_definition": "read file", "context": ""},
        is_current=AsyncMock(return_value=True),
        settings=settings,
        action=Action(
            ActionKind.file,
            path="source.py",
            job_id="job-1",
            workspace_path="/workspace",
            arguments_json='{"path":"source.py"}',
            tool_definition="read file",
        ),
        savepoint=AsyncMock(return_value="checkpoint"),
    )


@pytest.mark.parametrize(
    "spend,overrides,expected",
    [("1", (None, None), "allow"), ("0", (0, None), "warn"), ("0.1", (0.1, None), "warn")],
)
async def test_verified_usd_zero_null_and_decimal_warning_are_soft(
    tmp_path, session_factory, spend, overrides, expected
):
    authority, pin, ledger, _, _, _, _ = await _services(tmp_path, session_factory)
    await _verified_spend(ledger, spend)
    args = _read_arguments(pin, RepoPolicy(usd_ceilings={"supervised": overrides}))
    try:
        result = await authority.authorize(**args)
        assert result.proceed and result.decision.action == expected
        assert not result.decision.individual_human
        assert result.checkpoint_ref == ("checkpoint" if expected == "warn" else None)
    finally:
        await ledger.close()


@pytest.mark.parametrize("verdict", ["approve", "reject", "escalate"])
async def test_soft_ceiling_uses_monitor_but_does_not_create_monitor_grants(tmp_path, session_factory, verdict):
    from backend.services.action_policy.monitor import MonitorVerdict

    authority, pin, ledger, _, approvals, events, _ = await _services(tmp_path, session_factory)
    await _verified_spend(ledger)
    settings = RepoPolicy(usd_ceilings={"supervised": (None, 0)})
    args = _read_arguments(pin, settings)
    monitor = AsyncMock(return_value=(MonitorVerdict(verdict), "bounded fixture verdict"))
    requested = asyncio.Event()

    async def observe(event):
        if event.kind == EventKind.approval_requested:
            requested.set()

    events.subscribe(observe)
    pending = asyncio.create_task(authority.authorize(**args, monitor=monitor))
    try:
        if verdict == "escalate":
            await asyncio.wait_for(requested.wait(), 2)
            (approval,) = await approvals.list_pending("job-1")
            await approvals.resolve(approval.id, ApprovalResolution.rejected)
        result = await asyncio.wait_for(pending, 2)
        assert result.proceed is (verdict == "approve")
        assert result.decision.reason_code == "cp_usd_ceiling"
        monitor.assert_awaited_once()
        assert await ledger.active_reason_grants("job-1", authority.policy_scope(pin, settings)) == frozenset()
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await ledger.close()


@pytest.mark.parametrize("resolution", ["approved", "partial", "rejected", "stale"])
async def test_batch_selects_exact_actions_and_only_full_approval_grants_a_reason(
    tmp_path, session_factory, resolution
):
    from backend.services.action_policy.batcher import ApprovalBatcher, BatchResolution

    authority, pin, ledger, _, approvals, events, _ = await _services(tmp_path, session_factory)
    await _verified_spend(ledger)
    settings = RepoPolicy(usd_ceilings={"supervised": (None, 0)})
    batcher = ApprovalBatcher(events, batch_window_seconds=0.01)
    ready = asyncio.Event()

    async def observe(event):
        if event.kind == EventKind.batch_approval_requested and event.payload["batch_size"] == 2:
            ready.set()

    events.subscribe(observe)
    current = AsyncMock(return_value=True)
    pending = [
        asyncio.create_task(
            authority.authorize(**dict(_read_arguments(pin, settings), is_current=current), batcher=batcher)
        )
        for _ in range(2)
    ]
    try:
        await asyncio.wait_for(ready.wait(), 2)
        (batch,) = batcher.get_pending_batches("job-1")
        first_id = batch.actions[0].id
        assert len(await approvals.list_pending("job-1")) == 2
        if resolution == "stale":
            current.return_value = False
        assert batcher.resolve_batch(
            batch.id, BatchResolution.approved if resolution == "stale" else BatchResolution(resolution), {first_id}
        )
        results = await asyncio.wait_for(asyncio.gather(*pending), 2)
        for result in results:
            assert result.proceed is (
                resolution == "approved" or resolution == "partial" and result.decision_id == first_id
            )
        grants = await ledger.active_reason_grants("job-1", authority.policy_scope(pin, settings))
        assert grants == (frozenset({"cp_usd_ceiling"}) if resolution == "approved" else frozenset())
        assert not await approvals.list_pending("job-1")
        assert not await ledger.active_reason_grants("job-2", authority.policy_scope(pin, settings))
        assert not await ledger.active_reason_grants("job-1", authority.policy_scope(pin, RepoPolicy()))
        if resolution == "approved":
            repeated = await authority.authorize(**_read_arguments(pin, settings), batcher=batcher)
            assert repeated.proceed and repeated.approval_id is None
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        batcher.cleanup_job("job-1")
        await ledger.close()


@pytest.mark.parametrize("reason", ["hard", "incomplete", "locked"])
async def test_grants_and_monitor_cannot_bypass_individual_or_locked_routes(tmp_path, session_factory, reason):
    from dataclasses import replace

    authority, pin, ledger, _, approvals, events, _ = await _services(tmp_path, session_factory)
    settings = RepoPolicy(preset=Preset.locked if reason == "locked" else Preset.supervised)
    await authority.trust_job(pin, settings, "epoch")
    args = _read_arguments(pin, settings)
    if reason == "locked":
        # A new spend override is not covered by the old policy-scope grant.
        settings.usd_ceilings["locked"] = (None, 0)
        await _verified_spend(ledger)
    else:
        args["local"] = replace(args["local"], hard_gate=reason == "hard", accounting_unresolved=reason == "incomplete")
    monitor = AsyncMock(return_value=("approve", "must not run"))
    requested = asyncio.Event()

    async def observe(event):
        if event.kind == EventKind.approval_requested:
            requested.set()

    events.subscribe(observe)
    pending = asyncio.create_task(authority.authorize(**args, monitor=monitor))
    try:
        await asyncio.wait_for(requested.wait(), 2)
        (approval,) = await approvals.list_pending("job-1")
        assert approval.requires_explicit_approval
        await approvals.trust_job("job-1")
        assert not pending.done()
        await approvals.resolve(approval.id, ApprovalResolution.rejected)
        assert not (await asyncio.wait_for(pending, 2)).proceed
        monitor.assert_not_awaited()
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await ledger.close()
