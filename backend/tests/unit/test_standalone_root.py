import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import CPLConfig
from backend.models.domain import ApprovalResolution, JobState, SessionConfig
from backend.models.events import EventKind
from backend.persistence.event_repo import EventRepository
from backend.services.action_policy.classifier import Preset, RepoPolicy
from backend.services.action_policy.production import StandaloneGovernance
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.agent_adapter import CompletionResult
from backend.services.adapters.base_adapter import PermissionDecision
from backend.services.adapters.capability_guard import invocation_permission_request
from backend.services.auth.permission_policy import PermissionRequest
from backend.services.events.event_bus import EventBus
from backend.services.git.git_service import GitService
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime import RuntimeService
from backend.tests.unit.test_approval_service import session_factory as session_factory


@pytest.mark.parametrize("resolution", [ApprovalResolution.approved, ApprovalResolution.rejected, None, "shutdown"])
async def test_managed_policy_gate_uses_runtime_approval_state_and_exact_adapter_queue(
    session_factory, tmp_path, resolution
):
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
    requested = asyncio.Event()

    async def observe(event):
        if event.kind == "permission.requested":
            requested.set()

    events.subscribe(observe)
    pending = handler = None
    try:
        pin = await host.prepare_execution_contract("job-1", "admitted", recovery=False)
        assert pin is not None
        host._job_contract_pins["job-1"] = pin
        config = SessionConfig(workspace_path=str(tmp_path), prompt="", job_id="job-1")
        await host._setup_action_policy("job-1", config, str(tmp_path))
        adapter = registry.get_adapter("copilot")
        adapter.set_job_id("primary", "job-1")
        adapter.set_session_metadata("primary", config)
        adapter._queues["primary"] = asyncio.Queue()
        pending = asyncio.create_task(
            adapter._evaluate_permission(
                "primary",
                "job-1",
                PermissionRequest(
                    kind="shell", workspace_path=str(tmp_path), full_command_text="git rebase --show-current-patch"
                ),
                tool_name="bash",
                tool_input={"command": "git rebase --show-current-patch"},
                tool_call_id="gated-command",
                dialect="bash",
            )
        )
        event = await asyncio.wait_for(adapter._queues["primary"].get(), timeout=1)
        assert event is not None
        assert event.kind == "permission.requested"
        assert not requested.is_set(), "The Runtime must publish the request with its state transition"
        handler = asyncio.create_task(host._handle_approval_request("job-1", event, "Rejected fixture"))
        await asyncio.wait_for(requested.wait(), timeout=1)
        job = await host._get_job("job-1")
        assert job is not None and job.state == JobState.waiting_for_approval
        assert "job-1" in host._waiting_for_approval
        (approval,) = await approvals.list_pending("job-1")
        if resolution == "shutdown":
            host._shutting_down = True
            handler.cancel()
            pending.cancel()
            await asyncio.gather(handler, pending, return_exceptions=True)
            assert handler.cancelled() and pending.cancelled()
            retained = await approvals.get(approval.id)
            assert retained is not None and retained.resolution is None
            assert len(await approvals.list_pending("job-1")) == 1
        elif resolution is None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            assert pending.cancelled()
            assert await asyncio.wait_for(handler, 1) == ApprovalResolution.rejected
            retired = await approvals.get(approval.id)
            assert retired is not None and retired.resolution == ApprovalResolution.rejected
        else:
            await approvals.resolve(approval.id, resolution)
            assert await asyncio.wait_for(handler, 1) == resolution
            expected = (
                PermissionDecision.allow if resolution == ApprovalResolution.approved else PermissionDecision.deny
            )
            assert await asyncio.wait_for(pending, 1) == expected
        if resolution != "shutdown":
            assert not await approvals.list_pending("job-1")
            assert "job-1" not in host._waiting_for_approval
    finally:
        for task in (pending, handler):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await root.close()
        await host.shutdown()


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [("view", {"path": "source.py"}), ("grep", {"paths": "source.py", "pattern": "fixture"})],
)
async def test_real_root_catalogue_pin_policy_and_audit_reach_the_adapter_boundary(
    session_factory, tmp_path, tool_name, arguments
):
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
            invocation_permission_request(str(tmp_path), tool_name, arguments),
            tool_name=tool_name,
            tool_input=arguments,
            tool_call_id="read-1",
        )
        assert decision == PermissionDecision.allow
        adapter.complete.assert_not_awaited()
        host._git_service.recovery_witness.assert_not_called()
        assert not hasattr(host, "_governance_decider")
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


@pytest.fixture
async def recovery_runtime(session_factory, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git = GitService(CPLConfig())
    await git.run_git("init", "--initial-branch=main", cwd=workspace)
    await git.run_git("config", "user.name", "Recovery fixture", cwd=workspace)
    await git.run_git("config", "user.email", "recovery@example.invalid", cwd=workspace)
    (workspace / "source.txt").write_bytes(b"retained bytes\n")
    await git.add_all(cwd=workspace)
    await git.commit(
        "test: retain bytes\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>", cwd=workspace
    )
    events = EventBus()
    approvals = ApprovalService(session_factory)
    registry = AdapterRegistry(approval_service=approvals, event_bus=events, session_factory=session_factory)
    host = RuntimeService(
        session_factory=session_factory,
        event_bus=events,
        adapter_registry=registry,
        config=CPLConfig(),
        approval_service=approvals,
        git_service=git,
    )
    root = StandaloneGovernance(host, tmp_path)
    await root.start()
    host._standalone_governance = root
    try:
        pin = await host.prepare_execution_contract("job-1", "admitted", recovery=False)
        host._job_contract_pins["job-1"] = pin
        yield host, root, registry.get_adapter("copilot"), approvals, git, workspace
    finally:
        await root.close()
        await host.shutdown()


async def test_persisted_spend_controls_reload_into_actual_root_and_bound_monitor(recovery_runtime, session_factory):
    from backend.models.events import new_event
    from backend.persistence.policy_repo import PolicyRepository
    from backend.services.adapters.completion_context import current_completion_context
    from backend.tests.unit.test_policy_authority import _verified_spend

    host, root, adapter, _, _, workspace = recovery_runtime
    async with session_factory() as session:
        await PolicyRepository(session).set_usd_ceilings({"supervised": {"warn_usd": 0, "ceiling_usd": None}})
        await session.commit()
    await _verified_spend(root._ledger)
    config = SessionConfig(workspace_path=str(workspace), prompt="", job_id="job-1")
    await host._setup_action_policy("job-1", config, str(workspace))
    adapter.set_job_id("controlled-session", "job-1")
    adapter.set_session_metadata("controlled-session", config)
    classified = []
    contexts = []

    async def record(event):
        if event.kind == EventKind.action_classified:
            classified.append(event)

    async def monitor_complete(*args, **kwargs):
        contexts.append(current_completion_context())
        return CompletionResult(text="approve", model=host._config.runtime.utility_model)

    host._event_bus.subscribe(record)
    adapter.complete = AsyncMock(side_effect=monitor_complete)

    async def read():
        return await adapter._evaluate_permission(
            "controlled-session",
            "job-1",
            invocation_permission_request(str(workspace), "view", {"path": "source.txt"}),
            tool_name="view",
            tool_input={"path": "source.txt"},
            tool_call_id="read",
        )

    assert await read() == PermissionDecision.allow
    assert classified[-1].payload["reasonCode"] == "cp_usd_ceiling_warn"
    assert classified[-1].payload["checkpoint_ref"]
    adapter.complete.assert_not_awaited()
    async with session_factory() as session:
        await PolicyRepository(session).set_usd_ceilings({"supervised": {"warn_usd": None, "ceiling_usd": 0}})
        await session.commit()
    await host._on_policy_settings_changed(new_event("", EventKind.policy_settings_changed, {}))
    assert await read() == PermissionDecision.allow
    assert classified[-1].payload["reasonCode"] == "cp_usd_ceiling"
    adapter.complete.assert_awaited_once()
    assert contexts[0].role == "monitor"
    assert contexts[0].job_id == "job-1"
    assert contexts[0].execution_epoch == host.execution_epoch
    assert contexts[0].execution_contract == host._job_contract_pins["job-1"]


@pytest.mark.parametrize("cancel_issuance", [False, True])
async def test_routine_batch_preserves_runtime_waiting_state_and_durable_resolution(
    recovery_runtime, session_factory, monkeypatch, cancel_issuance
):
    from backend.persistence.policy_repo import PolicyRepository
    from backend.tests.unit.test_policy_authority import _verified_spend

    host, root, adapter, approvals, _, workspace = recovery_runtime
    async with session_factory() as session:
        await PolicyRepository(session).set_usd_ceilings({"locked": {"warn_usd": None, "ceiling_usd": 0}})
        await PolicyRepository(session).update_config(batch_window_seconds=0.5)
        await session.commit()
    await _verified_spend(root._ledger)
    config = SessionConfig(workspace_path=str(workspace), prompt="", job_id="job-1")
    await host._setup_action_policy("job-1", config, str(workspace), job_preset="locked")
    adapter.set_job_id("batch-session", "job-1")
    adapter.set_session_metadata("batch-session", config)
    adapter._queues["batch-session"] = asyncio.Queue()
    pending = asyncio.create_task(
        adapter._evaluate_permission(
            "batch-session",
            "job-1",
            invocation_permission_request(str(workspace), "view", {"path": "source.txt"}),
            tool_name="view",
            tool_input={"path": "source.txt"},
            tool_call_id="read",
        )
    )
    waiting = asyncio.Event()
    displayed = asyncio.Event()

    async def observe(event):
        if event.kind == EventKind.approval_requested:
            waiting.set()
        if event.kind == EventKind.batch_approval_requested:
            displayed.set()

    host._event_bus.subscribe(observe)
    handler = None
    release = None
    try:
        event = await asyncio.wait_for(adapter._queues["batch-session"].get(), 2)
        assert event.kind == EventKind.approval_requested
        handler = asyncio.create_task(host._handle_approval_request("job-1", event, "Rejected fixture"))
        await asyncio.wait_for(waiting.wait(), 2)
        assert (await host._get_job("job-1")).state == JobState.waiting_for_approval
        await asyncio.wait_for(displayed.wait(), 2)
        (batch,) = host._policy_batchers["job-1"].get_pending_batches("job-1")
        if cancel_issuance:
            from backend.tests.unit.test_governance_ledger import pause_grant_write

            entered, release = pause_grant_write(monkeypatch, root._ledger)
        assert await host.resolve_policy_batch("job-1", batch.id, "approved")
        assert await asyncio.wait_for(handler, 2) == ApprovalResolution.approved
        if cancel_issuance:
            await asyncio.wait_for(entered.wait(), 2)
            pending.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            router = host._policy_routers["job-1"]
            assert not await root._ledger.active_reason_grants(
                "job-1", router._authority.policy_scope(router.pin, router._policy)
            )
        else:
            assert await asyncio.wait_for(pending, 2) == PermissionDecision.allow
        assert not await approvals.list_pending("job-1")
        assert (await host._get_job("job-1")).state == JobState.running
    finally:
        if release is not None:
            release.set()
        pending.cancel()
        if handler is not None:
            handler.cancel()
        await asyncio.gather(pending, *([handler] if handler is not None else []), return_exceptions=True)


@pytest.mark.parametrize("scenario", ["late_manifest", "other_workspace", "prior_execution", "prior_decision"])
async def test_monitor_uses_only_pre_execution_workspace_baseline(recovery_runtime, session_factory, scenario):
    from backend.models.events import new_event
    from backend.persistence.database import serialized_write
    from backend.services.action_policy.classifier import Action, ActionKind, Classification, RecommendedAction
    from backend.services.action_policy.monitor import MonitorVerdict

    host, root, adapter, _, _, workspace = recovery_runtime
    package = workspace / "package.json"
    if scenario != "late_manifest":
        package.write_text(json.dumps({"dependencies": {"fixturelib": "1"}}))
    if scenario == "prior_execution":
        async with serialized_write(session_factory) as session:
            await EventRepository(session).append(
                new_event("job-1", EventKind.tool_call_started, {"tool_name": "write"})
            )
    if scenario == "prior_decision":
        await root._ledger.enqueue_decision(
            decision_id="prior-permission",
            job_id="job-1",
            epoch=host.execution_epoch,
            action_digest="prior-bound-action",
            decision="allow",
            metadata={"reason_code": "known_read"},
        )
    config = SessionConfig(workspace_path=str(workspace), prompt="", job_id="job-1")
    await host._setup_action_policy("job-1", config, str(workspace))
    adapter.complete = AsyncMock(
        return_value=CompletionResult(text="escalate", model=host._config.runtime.utility_model)
    )
    router = host._policy_routers["job-1"]
    classification = Classification(
        RecommendedAction.ESCALATE, "external_mutation", None, "unknown", "mutating", "shell", "Review dependency"
    )
    action = Action(ActionKind.shell, command="uv add fixturelib", job_id="job-1", workspace_path=str(workspace))
    assert router._monitor is not None
    if scenario == "late_manifest":
        package.write_text(json.dumps({"dependencies": {"fixturelib": "1"}}))
    elif scenario == "other_workspace":
        assert (await router._monitor(action, classification))[0] == MonitorVerdict.approve
        adapter.complete.assert_not_awaited()
        other = workspace.parent / "other-workspace"
        other.mkdir()
        from dataclasses import replace

        action = replace(action, workspace_path=str(other))
    verdict, _ = await router._monitor(action, classification)
    assert verdict == MonitorVerdict.escalate
    adapter.complete.assert_awaited_once()


@pytest.mark.parametrize("change", ["closed", "reloaded", "canceled"])
async def test_operator_grant_issuance_cannot_outlive_router_fence(recovery_runtime, monkeypatch, change):
    from backend.tests.unit.test_governance_ledger import pause_grant_write

    host, root, _, _, _, workspace = recovery_runtime
    config = SessionConfig(workspace_path=str(workspace), prompt="", job_id="job-1")
    await host._setup_action_policy("job-1", config, str(workspace))
    router = host._policy_routers["job-1"]
    scope = router._authority.policy_scope(router.pin, router._policy)
    entered, release = pause_grant_write(monkeypatch, root._ledger)
    task = asyncio.create_task(router.trust_job())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if change == "closed":
            router.cleanup_job("job-1")
        elif change == "reloaded":
            router.update_policy(RepoPolicy(preset=Preset.locked))
        else:
            task.cancel()
        release.set()
        if change == "canceled":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert not await task
        assert not await root._ledger.active_reason_grants("job-1", scope)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_used_grant_expiring_after_classification_publication_is_rejected(
    recovery_runtime, session_factory, monkeypatch
):
    from datetime import timedelta

    from backend.persistence.policy_repo import PolicyRepository
    from backend.tests.unit.test_policy_authority import _verified_spend, grant_clock

    host, root, adapter, _, _, workspace = recovery_runtime
    clock = grant_clock(monkeypatch)
    async with session_factory() as session:
        await PolicyRepository(session).set_usd_ceilings({"supervised": {"warn_usd": None, "ceiling_usd": 0}})
        await session.commit()
    await _verified_spend(root._ledger)
    config = SessionConfig(workspace_path=str(workspace), prompt="", job_id="job-1")
    await host._setup_action_policy("job-1", config, str(workspace))
    router = host._policy_routers["job-1"]
    await root._ledger.grant_reasons(
        "job-1",
        router._authority.policy_scope(router.pin, router._policy),
        frozenset({"cp_usd_ceiling"}),
        epoch=host.execution_epoch,
        source_id="ceiling",
    )
    adapter.set_job_id("expiry-session", "job-1")
    adapter.set_session_metadata("expiry-session", config)

    async def expire(event):
        if event.kind == EventKind.action_classified:
            clock[0] += timedelta(days=2)
            await asyncio.sleep(0)

    host._event_bus.subscribe(expire)
    result = await adapter._evaluate_permission(
        "expiry-session",
        "job-1",
        invocation_permission_request(str(workspace), "view", {"path": "source.txt"}),
        tool_name="view",
        tool_input={"path": "source.txt"},
        tool_call_id="read",
    )
    assert result == PermissionDecision.deny


@pytest.mark.parametrize(
    "scenario",
    [
        "clean",
        "new",
        "dirty",
        "changed-bytes",
        "changed-ref",
        "contradictory-read",
        "contradictory-recovery",
        "path-mismatch",
        "path-hint-only",
        "opaque-write",
        "final-policy-change",
        "final-policy-unchanged",
        "final-owner-change",
        "final-current-policy-change",
    ],
)
async def test_real_native_write_requires_bound_current_bytes_not_a_model_git_claim(recovery_runtime, scenario):
    host, root, adapter, approvals, git, workspace = recovery_runtime
    config = SessionConfig(workspace_path=str(workspace), prompt="", job_id="job-1")
    await host._setup_action_policy("job-1", config, str(workspace), job_preset="autonomous")
    adapter.set_job_id("native-write", "job-1")
    adapter.set_session_metadata("native-write", config)
    adapter._queues["native-write"] = asyncio.Queue()
    path = "new.txt" if scenario == "new" else "source.txt"
    if scenario == "dirty":
        (workspace / path).write_text("valuable uncommitted bytes\n")

    async def complete(prompt, **kwargs):
        fields = json.loads(prompt)
        if scenario not in {"dirty", "path-mismatch", "path-hint-only", "opaque-write"}:
            assert "Application-verified complete file target set" in fields["context"]
        if scenario == "changed-bytes":
            (workspace / path).write_text("different bytes during assessment\n")
        elif scenario == "changed-ref":
            await git.commit(
                "test: advance ref\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
                cwd=workspace,
                allow_empty=True,
            )
        values = {
            "effect": "read_only" if scenario in {"contradictory-read", "opaque-write"} else "mutating",
            "scope": "workspace",
            "network": "none",
            "reversibility": "not_applicable"
            if scenario in {"contradictory-read", "contradictory-recovery", "opaque-write"}
            else "git",
        }
        return CompletionResult(
            text=json.dumps(
                {
                    "schema_version": "cp-action-v1",
                    "mechanism": "file",
                    **values,
                    "capabilities": [],
                    "structure": [],
                    "unknowns": [],
                    "evidence": [
                        {"field": key, "value": value, "source": "context", "start": 0, "end": len(fields["context"])}
                        for key, value in values.items()
                    ],
                }
            ),
            model=kwargs["model"],
        )

    adapter.complete = AsyncMock(side_effect=complete)
    arguments = {"path": path, "old_str": "retained bytes", "new_str": "replacement"}
    request = invocation_permission_request(str(workspace), "edit", arguments)
    if scenario == "path-mismatch":
        arguments = {**arguments, "path": "other.txt"}
    elif scenario == "path-hint-only":
        arguments = {}
    final_read_entered, release_final_read = asyncio.Event(), asyncio.Event()
    classified = False
    final_head_reads = 0
    original_git = git._run_git

    async def observe_classification(event):
        nonlocal classified
        if event.kind == EventKind.action_classified:
            classified = True

    async def pause_final_read(*args, **kwargs):
        nonlocal final_head_reads
        result = await original_git(*args, **kwargs)
        if classified and args == ("rev-parse", "--verify", "HEAD^{commit}"):
            final_head_reads += 1
            if final_head_reads == 2:
                final_read_entered.set()
                await release_final_read.wait()
        return result

    if scenario.startswith("final-"):
        host._event_bus.subscribe(observe_classification)
        git._run_git = pause_final_read
    pending = asyncio.create_task(
        adapter._evaluate_permission(
            "native-write",
            "job-1",
            request,
            tool_name="apply_patch" if scenario == "opaque-write" else "edit",
            tool_input=arguments,
            tool_call_id="write-1",
        )
    )
    try:
        if scenario.startswith("final-"):
            await asyncio.wait_for(final_read_entered.wait(), 3)
            router = host._policy_routers["job-1"]
            if scenario == "final-policy-change":
                router.update_policy(RepoPolicy(preset=Preset.locked))
            elif scenario == "final-owner-change":
                adapter.pause_tools("native-write")
                adapter.resume_tools("native-write")
            elif scenario == "final-current-policy-change":
                original_current = router._job_is_current

                async def replace_after_liveness(*args):
                    result = await original_current(*args)
                    router.update_policy(RepoPolicy(preset=Preset.locked))
                    return result

                router._job_is_current = replace_after_liveness
            release_final_read.set()
        if scenario in {
            "dirty",
            "contradictory-read",
            "contradictory-recovery",
            "path-mismatch",
            "path-hint-only",
            "opaque-write",
        }:
            event = await asyncio.wait_for(adapter._queues["native-write"].get(), 3)
            assert event.kind == "permission.requested"
            (approval,) = await approvals.list_pending("job-1")
            assert approval.requires_explicit_approval
            assert json.loads(approval.proposed_action)["reason_code"] == "incomplete_evidence"
            await approvals.resolve(approval.id, ApprovalResolution.rejected)
        decision = await asyncio.wait_for(pending, 3)
        assert decision == (
            PermissionDecision.allow
            if scenario in {"clean", "new", "final-policy-unchanged"}
            else PermissionDecision.deny
        )
        adapter.complete.assert_awaited_once()
        assert adapter._sessions == {}, "The regression must not create real provider sessions"
        token = adapter._event_metadata("native-write").execution_token
        recorded = await root._ledger.authorized_tool("job-1", host.execution_epoch, token, "write-1")
        assert recorded is not None
        proof = recorded.metadata["git_recovery"]
        if scenario in {"dirty", "path-mismatch", "path-hint-only", "opaque-write"}:
            assert proof is None
        else:
            assert proof["targets"][0]["path"] == path
            assert (proof["targets"][0]["blob"] is None) is (scenario == "new")
        if scenario.startswith("changed-"):
            assert recorded.decision == "reject"
            assert recorded.metadata["reason_code"] == "execution_ineligible"
    finally:
        release_final_read.set()
        if scenario.startswith("final-"):
            host._event_bus.unsubscribe(observe_classification)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("change", ["bytes", "ref"])
async def test_human_approval_cannot_consume_a_changed_recovery_witness(recovery_runtime, change):
    host, root, adapter, approvals, git, workspace = recovery_runtime
    config = SessionConfig(workspace_path=str(workspace), prompt="", job_id="job-1")
    await host._setup_action_policy("job-1", config, str(workspace), job_preset="locked")
    adapter.set_job_id("waiting-write", "job-1")
    adapter.set_session_metadata("waiting-write", config)
    adapter._queues["waiting-write"] = asyncio.Queue()
    # An unavailable assessment still binds the independently captured witness.
    adapter.complete = AsyncMock(side_effect=ValueError("controlled unavailable provider"))
    arguments = {"path": "source.txt", "old_str": "retained", "new_str": "replacement"}
    pending = asyncio.create_task(
        adapter._evaluate_permission(
            "waiting-write",
            "job-1",
            invocation_permission_request(str(workspace), "edit", arguments),
            tool_name="edit",
            tool_input=arguments,
            tool_call_id="waiting-1",
        )
    )
    try:
        event = await asyncio.wait_for(adapter._queues["waiting-write"].get(), 3)
        assert event.kind == "permission.requested"
        (approval,) = await approvals.list_pending("job-1")
        if change == "bytes":
            (workspace / "source.txt").write_text("changed while awaiting approval\n")
        else:
            await git.commit(
                "test: advance ref\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
                cwd=workspace,
                allow_empty=True,
            )
        await approvals.resolve(approval.id, ApprovalResolution.approved)
        assert await asyncio.wait_for(pending, 3) == PermissionDecision.deny
        recorded = await root._ledger.authorized_tool(
            "job-1",
            host.execution_epoch,
            adapter._event_metadata("waiting-write").execution_token,
            "waiting-1",
        )
        assert recorded is not None and recorded.decision == "reject"
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


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
        adapter._queues["session"] = asyncio.Queue()
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
        event = await asyncio.wait_for(adapter._queues["session"].get(), timeout=3)
        assert event is not None
        await events.publish(event)
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
