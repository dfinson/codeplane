import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from backend.models.domain import ApprovalResolution, StateConflictError
from backend.models.events import EventKind
from backend.models.schemas.recipes import (
    RecipeCommandRunner,
    RecipeDefinition,
    RecipeOutput,
    RecipeRoute,
    RecipeStep,
    RecipeWorkspacePolicy,
)
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.action_policy.production import StandaloneGovernance
from backend.services.adapters.agent_adapter import CompletionResult
from backend.services.job.approval_service import ApprovalService
from backend.services.recipe.command_runner import CommandStepRunner
from backend.services.recipe.executor import OwnedRecipeStep, RecipeExecutor
from backend.tests.unit.test_recipe_command_runner import command_context
from backend.tests.unit.test_recipe_runs import sessions as sessions


async def _production(sessions, tmp_path, monkeypatch, argv):
    graph = RecipeDefinition(
        name="Exact command policy",
        entry_steps=["check"],
        steps=[
            RecipeStep(
                id="check",
                title="Check",
                runner=RecipeCommandRunner(argv=argv),
                capabilities=["read", "write", "shell", "external_access"],
                workspace=RecipeWorkspacePolicy(mode="mutable"),
                outputs={"receipt": RecipeOutput(type="object")},
                routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
            )
        ],
    )
    host, _, run, instance, attempt = await command_context(
        sessions,
        tmp_path,
        monkeypatch,
        "unused",
        graph=graph,
        admit=False,
    )
    from backend.models.schemas.recipes import RecipeResolvedWorkspace

    workspace = str(tmp_path / "repo")
    code = await host._git_service.rev_parse("HEAD", cwd=workspace)
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        instance = await repository.project_step(instance, "ready", "Exact command test")
        attempt = await repository.claim_attempt(
            instance,
            inputs={},
            runner=instance.definition.runner,
            capabilities=instance.definition.capabilities,
            workspace=RecipeResolvedWorkspace(path=workspace, code_ref=code, policy=instance.definition.workspace),
        )
        await session.commit()
    approvals = ApprovalService(sessions)
    host._approval_service = approvals
    root = StandaloneGovernance(host, tmp_path / "governance")
    await root.start()
    host._standalone_governance = root
    pin = await host.prepare_execution_contract(run.job_id, run.id, recovery=False)
    host._recipe_contract_pins[run.job_id] = pin
    from backend.models.domain import SessionConfig
    from backend.persistence.job_repo import JobRepository

    async with sessions() as session:
        job = await JobRepository(session).get(run.job_id)
    config = SessionConfig(workspace_path=attempt.workspace.path, prompt="", job_id=run.job_id)
    await host._setup_action_policy(run.job_id, config, attempt.workspace.path)
    controller = RecipeExecutor(host, job, config, contract_pin=pin)
    host._recipe_controllers[run.job_id] = controller
    return host, root, approvals, controller, run, instance, attempt


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "merge", "topic"],
        ["git", "-C", ".", "rebase", "topic"],
        ["git", "reset", "--hard", "HEAD"],
        ["git", "push", "--force"],
        ["sh", "-c", "git -C . merge topic"],
        ["env", "git", "merge", "topic"],
        ["pwsh.exe", "-Command", "git merge topic"],
        ["bash", "-c", "git -C . merge topic"],
        ["bash", "-lc", "'/usr/bin/git' -C . merge topic"],
        ["env", "LANG=C", "git", "-C", ".", "merge", "topic"],
        ["env", "--", "bash", "-c", "git -C . rebase topic"],
        ["pwsh", "-Command", "git -C . merge topic"],
    ],
)
async def test_direct_git_argv_hard_gate_precedes_an_sdk_labeled_model_answer(
    sessions,
    tmp_path,
    monkeypatch,
    argv,
):
    host, root, approvals, controller, run, _, attempt = await _production(sessions, tmp_path, monkeypatch, argv)
    provider = AsyncMock()
    # This answer would ordinarily select supervised WARN if local hard facts were lost.
    provider.complete.return_value = CompletionResult(
        text=json.dumps(
            {
                "schema_version": "cp-action-v1",
                "mechanism": "sdk",
                "effect": "mutating",
                "scope": "workspace",
                "network": "none",
                "reversibility": "git",
                "capabilities": [],
                "structure": [],
                "unknowns": [],
                "evidence": [
                    {"field": field, "value": value, "source": "arguments", "start": 0, "end": 1}
                    for field, value in [
                        ("effect", "mutating"),
                        ("scope", "workspace"),
                        ("network", "none"),
                        ("reversibility", "git"),
                    ]
                ],
            }
        ),
        model="claude-test",
    )
    authority = host._policy_routers[run.job_id]._authority
    authority._provider = provider
    authority._model = "claude-test"
    requested = asyncio.Event()

    async def on_event(event):
        if event.kind == EventKind.approval_requested:
            requested.set()

    host._event_bus.subscribe(on_event)

    async def authorize():
        task = asyncio.current_task()
        assert task is not None
        controller._owned_attempts[attempt.id] = OwnedRecipeStep(
            "actual-task-token",
            run.id,
            attempt.step_instance_id,
            task,
        )
        await host.authorize_attempt_command(
            run.job_id,
            attempt.id,
            "actual-task-token",
            argv,
            attempt.workspace.path,
        )

    pending = asyncio.create_task(authorize())
    try:
        await asyncio.wait_for(requested.wait(), timeout=3)
        (approval,) = await approvals.list_pending(run.job_id)
        assert approval.requires_explicit_approval
        assert json.loads(approval.proposed_action)["reason_code"] == "hard_gate"
        provider.complete.assert_not_awaited()
        await approvals.resolve(approval.id, ApprovalResolution.rejected)
        with pytest.raises(StateConflictError, match="not authorized"):
            await pending
        assert (await root._ledger.job_totals(run.job_id)).executed_calls == 0
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        controller._owned_attempts.clear()
        await root.close()


@pytest.mark.parametrize("exit_code", [0, 3])
async def test_executed_command_result_accrues_once_from_durable_replay(sessions, tmp_path, monkeypatch, exit_code):
    argv = ["sh", "-c", f"exit {exit_code}"]
    host, root, approvals, controller, run, instance, attempt = await _production(
        sessions,
        tmp_path,
        monkeypatch,
        argv,
    )
    host._policy_routers[run.job_id]._authority._provider = None

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    try:
        task = asyncio.current_task()
        assert task is not None
        token = f"{host.execution_epoch}:command"
        controller._owned_attempts[attempt.id] = OwnedRecipeStep(token, run.id, instance.id, task)
        result, outputs = await CommandStepRunner(host).run(run, instance, attempt)
        assert result.evidence.started is True and result.evidence.exit_status == exit_code
        async with sessions() as session:
            await RecipeRunRepository(session).finish_attempt(attempt.id, result, outputs)
            await session.commit()
        await root.close()
        from backend.persistence.event_repo import EventRepository

        async with sessions() as session:
            records = await EventRepository(session).list_after(0, job_id=run.job_id)
        (completion,) = [row.event for row in records if row.event.kind == "execution.command.result"]
        await root.observe(completion)
        await root.observe(completion)
        totals = await root._ledger.job_totals(run.job_id)
        assert totals.executed_calls == 1
        assert totals.unaccounted == 1, "Unknown side effects stay unresolved, not measured safe"
    finally:
        controller._owned_attempts.clear()
        await root.close()


@pytest.mark.parametrize("unknown", [False, True])
async def test_nonstarted_or_unknown_command_is_not_counted_as_executed(sessions, tmp_path, monkeypatch, unknown):
    argv = ["sh", "-c", "exit 0"]
    host, root, approvals, controller, run, instance, attempt = await _production(
        sessions,
        tmp_path,
        monkeypatch,
        argv,
    )
    host._policy_routers[run.job_id]._authority._provider = None

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    task = asyncio.current_task()
    assert task is not None
    controller._owned_attempts[attempt.id] = OwnedRecipeStep("not-started-token", run.id, instance.id, task)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("Spawn failed")))
    try:
        result, outputs = await CommandStepRunner(host).run(run, instance, attempt)
        assert result.evidence.started is False
        if unknown:
            result = result.model_copy(update={"evidence": result.evidence.model_copy(update={"started": None})})
        async with sessions() as session:
            await RecipeRunRepository(session).finish_attempt(attempt.id, result, outputs)
            await session.commit()
        await root.close()
        from backend.persistence.event_repo import EventRepository

        async with sessions() as session:
            records = await EventRepository(session).list_after(0, job_id=run.job_id)
        (completion,) = [row.event for row in records if row.event.kind == "execution.command.result"]
        await root.observe(completion)
        totals = await root._ledger.job_totals(run.job_id)
        assert totals.executed_calls == 0
        assert totals.unaccounted == int(unknown)
    finally:
        controller._owned_attempts.clear()
        await root.close()
