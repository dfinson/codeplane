import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import CPLConfig
from backend.models.domain import SessionConfig
from backend.models.events import EventKind, new_event
from backend.models.schemas.recipes import (
    RecipeAttemptExecution,
    RecipeCommandRunner,
    RecipeDefinition,
    RecipeGateProposal,
    RecipeHumanRunner,
    RecipeRoute,
    RecipeStep,
    RecipeWorkspacePolicy,
)
from backend.models.session_event import EventMetadata
from backend.persistence.event_repo import EventRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.action_policy.classifier import RepoPolicy
from backend.services.copilot_adapter import CopilotAdapter
from backend.services.events.event_bus import EventBus
from backend.services.job.approval_service import ApprovalService
from backend.services.recipe.validation import content_checksum
from backend.services.runtime import RuntimeService
from backend.services.runtime.attempt_bindings import AttemptBindingConflictError
from backend.tests.unit.test_execution_contracts import _contract
from backend.tests.unit.test_recipe_definitions import agent
from backend.tests.unit.test_recipe_runs import create_run, ready_attempt
from backend.tests.unit.test_recipe_runs import sessions as sessions


async def _context(sessions, kind):
    left = agent("gate")
    if kind == "human":
        left = RecipeStep(
            id="gate", title="Human gate", runner=RecipeHumanRunner(proposed_action="Review the exact inputs"),
            workspace=RecipeWorkspacePolicy(mode="none"),
            routes={"approved": RecipeRoute(terminal="succeeded"), "rejected": RecipeRoute(terminal="failed")},
        )
    elif kind == "command":
        left = RecipeStep(
            id="gate", title="Command gate", runner=RecipeCommandRunner(argv=["echo", "fixture"]),
            capabilities=["read", "shell"],
            routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
        )
    run = await create_run(
        sessions, RecipeDefinition(
            name="Independent attempts", entry_steps=["gate", "work"], steps=[left, agent("work")],
        ),
    )
    async with sessions() as session:
        owner = await ready_attempt(session, run, "gate")
        target = await ready_attempt(session, run, "work")
    approvals = ApprovalService(sessions)
    adapter = CopilotAdapter(approval_service=approvals)

    async def create(config):
        adapter.set_job_id("target-session", config.job_id)
        adapter.set_session_metadata("target-session", config)
        return "target-session"

    adapter.create_session = AsyncMock(side_effect=create)
    adapter.abort_session = AsyncMock()
    adapter.send_message = AsyncMock()
    registry = MagicMock()
    registry.get_adapter.return_value = adapter
    runtime = RuntimeService(
        session_factory=sessions, event_bus=EventBus(), adapter_registry=registry,
        config=CPLConfig(), approval_service=approvals,
    )
    runtime._policy_routers[run.job_id] = MagicMock()
    runtime._repo_policies[run.job_id] = RepoPolicy()
    contract = _contract("test")
    runtime.configure_execution_contracts(available={contract.id: contract}, selected_for_new=contract.id)
    pin = await runtime.prepare_execution_contract(run.job_id, run.id, recovery=False)
    return runtime, adapter, approvals, run, owner, target, pin


async def _gate(sessions, approvals, run, owner, kind, epoch, *, alteration=None):
    if kind == "human":
        async with sessions() as session:
            instance = await RecipeRunRepository(session).get_step(owner.step_instance_id)
        proposal = RecipeGateProposal(
            recipe_run_id=run.id, step_instance_id=owner.step_instance_id, attempt_id=owner.id,
            input_checksum=content_checksum({}), proposed_action=owner.runner.proposed_action,
            next_steps={key: value.next_steps for key, value in instance.definition.routes.items()},
        )
        approval = await approvals.create_request(
            run.job_id, "Human gate", proposal.model_dump_json(), requires_explicit_approval=True,
        )
        async with sessions() as session:
            await RecipeRunRepository(session).bind_gate(proposal, approval.id)
            await session.commit()
        return approval
    approval = await approvals.create_request(run.job_id, "Scoped gate", "exact action")
    metadata = EventMetadata(
        source_framework="codeplane.recipe" if kind == "command" else "copilot",
        recipe_run_id=run.id, step_instance_id=owner.step_instance_id,
        attempt_id=owner.id, execution_epoch=epoch,
        execution_token=None if kind == "command" else "owner-token",
        run_id=None if kind == "command" else "owner-session",
    )
    async with sessions() as session:
        if kind == "sdk":
            await RecipeRunRepository(session).record_execution(
                owner.id, RecipeAttemptExecution(
                    execution_token="owner-token", session_id="owner-session", provider="copilot",
                    model=None, started_at=datetime.now(UTC),
                ),
            )
        if alteration == "observation":
            metadata = metadata.model_copy(update={"integration_binding_id": "untrusted-source"})
        elif alteration == "namespace":
            metadata = metadata.model_copy(update={"namespace": ("observation-source", "record")})
        elif alteration == "stale_token":
            metadata = metadata.model_copy(update={"execution_token": "unrecorded-token"})
        elif alteration == "wrong_step":
            metadata = metadata.model_copy(update={"step_instance_id": "unrecorded-step"})
        if alteration != "missing":
            await EventRepository(session).append(new_event(
                run.job_id, EventKind.approval_requested,
                {"approval_id": approval.id, "proposed_action": approval.proposed_action}, metadata=metadata,
            ))
            if alteration == "ambiguous":
                await EventRepository(session).append(new_event(
                    run.job_id, EventKind.approval_requested,
                    {"approval_id": approval.id, "proposed_action": approval.proposed_action}, metadata=metadata,
                ))
        await session.commit()
    return approval


@pytest.mark.parametrize("kind", ["human", "sdk", "command"])
async def test_independent_bound_gate_does_not_block_open_bind_attach_or_dispatch(sessions, kind):
    runtime, adapter, approvals, run, owner, target, pin = await _context(sessions, kind)
    approval = await _gate(sessions, approvals, run, owner, kind, runtime.execution_epoch)
    assert await approvals.has_blocking_approvals(run.job_id)
    config = SessionConfig(
        workspace_path=target.workspace.path, prompt="target", job_id=run.job_id,
        recipe_run_id=run.id, step_instance_id=target.step_instance_id, attempt_id=target.id,
        execution_epoch=runtime.execution_epoch,
    )
    with pytest.raises(AttemptBindingConflictError, match="pending approval"):
        await runtime._require_attempt_job(run.job_id, owner.id)
    async with runtime.open_attempt_execution(run.job_id, target.id, config) as execution:
        await runtime.bind_attempt_contract(pin, target.id, execution.execution_token)
        await runtime.attach_attempt_policy(run.job_id, target.id, execution.execution_token)
        result = await runtime.dispatch_current_execution(
            run.job_id, execution.execution_token, "Only the target", attempt_id=target.id,
        )
        assert result.status == "accepted"
        adapter.send_message.assert_awaited_once_with("target-session", "Only the target")
    assert (await approvals.get(approval.id)).resolution is None


@pytest.mark.parametrize(
    "alteration", ["missing", "observation", "namespace", "stale_token", "wrong_step", "ambiguous"],
)
async def test_missing_or_untrusted_sdk_scope_remains_job_blocking(sessions, alteration):
    runtime, adapter, approvals, run, owner, target, _ = await _context(sessions, "sdk")
    await _gate(sessions, approvals, run, owner, "sdk", runtime.execution_epoch, alteration=alteration)
    with pytest.raises(AttemptBindingConflictError, match="pending approval"):
        await runtime.register_attempt_execution(run.job_id, target.id, adapter, "target-session")
    adapter.create_session.assert_not_awaited()


async def test_tool_json_cannot_declare_its_own_approval_scope(sessions):
    runtime, adapter, approvals, run, owner, target, _ = await _context(sessions, "sdk")
    await approvals.create_request(
        run.job_id, "Unscoped approval",
        json.dumps({"kind": "recipe_gate", "attempt_id": owner.id, "recipe_run_id": run.id}),
    )
    with pytest.raises(AttemptBindingConflictError, match="pending approval"):
        await runtime.register_attempt_execution(run.job_id, target.id, adapter, "target-session")


async def test_unknown_target_cannot_skip_an_independently_bound_gate(sessions):
    runtime, adapter, approvals, run, owner, _, _ = await _context(sessions, "human")
    await _gate(sessions, approvals, run, owner, "human", runtime.execution_epoch)
    with pytest.raises(AttemptBindingConflictError, match="pending approval"):
        await runtime.register_attempt_execution(run.job_id, "unregistered-attempt", adapter, "target-session")
