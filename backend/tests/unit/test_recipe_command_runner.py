"""Real subprocess evidence with isolated Git, storage and policy test doubles."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from backend.config import CPLConfig
from backend.models.db import RecipeWorkspaceLeaseRow
from backend.models.domain import ApprovalResolution, JobState, SessionConfig, StateConflictError
from backend.models.execution_contracts import ExecutionContract, ImplementationPin
from backend.models.schemas.recipes import (
    RecipeBlockRunner,
    RecipeCodeReference,
    RecipeCommandArgument,
    RecipeCommandRunner,
    RecipeDefinition,
    RecipeDependency,
    RecipeExport,
    RecipeHumanRunner,
    RecipeInput,
    RecipeLiteralBinding,
    RecipeOutput,
    RecipeOutputBinding,
    RecipeParameter,
    RecipeParameterBinding,
    RecipeResolvedWorkspace,
    RecipeRetryPolicy,
    RecipeRoute,
    RecipeStep,
    RecipeWorkspacePolicy,
)
from backend.persistence.execution_contract_repo import ExecutionContractRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.recipe_repo import RecipeRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.artifacts.artifact_service import ArtifactService, resolve_artifact_path
from backend.services.events.event_bus import EventBus
from backend.services.git.git_service import GitService
from backend.services.job.approval_service import ApprovalService
from backend.services.recipe.command_runner import CommandStepRunner
from backend.services.recipe.definition_service import RecipeDefinitionService
from backend.services.recipe.workspace import prepare_workspace
from backend.services.runtime import RuntimeService
from backend.tests.unit.test_recipe_definitions import agent
from backend.tests.unit.test_recipe_runs import create_run
from backend.tests.unit.test_recipe_runs import sessions as sessions


def configure_test_contract(runtime):
    implementation = ImplementationPin(
        id="explicit-unit-test-double",
        sha256=hashlib.sha256(b"explicit-unit-test-double").hexdigest(),
    )
    contract = ExecutionContract(
        id="unit-test-contract",
        policy=implementation,
        parser=implementation,
        grouping=implementation,
        title=implementation,
    )
    runtime.configure_execution_contracts(available={contract.id: contract}, selected_for_new=contract.id)


async def test_unknown_command_start_cannot_succeed_or_release_the_writer(sessions, tmp_path, monkeypatch):
    host, _, run, instance, attempt = await command_context(sessions, tmp_path, monkeypatch, "printf checked")
    result, outputs = await CommandStepRunner(host).run(run, instance, attempt)
    evidence = result.evidence.model_copy(update={"started": None})
    result = result.model_copy(update={"evidence": evidence})
    outputs["receipt"] = evidence.model_dump(mode="json")
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        await repository.finish_attempt(attempt.id, result, outputs)
        await session.commit()
        retained = await repository.get_attempt(attempt.id)
        assert retained.result.outcome == "interrupted"
        assert retained.result.evidence.uncertain
        assert (
            await session.scalar(
                select(RecipeWorkspaceLeaseRow.attempt_id).where(RecipeWorkspaceLeaseRow.attempt_id == attempt.id)
            )
            == attempt.id
        )
        with pytest.raises(StateConflictError):
            await repository.resolve_run(run.id, "succeeded")


async def test_parallel_snapshot_preparation_reuses_one_complete_worktree(sessions, tmp_path, monkeypatch):
    graph = RecipeDefinition(
        name="Shared snapshots", entry_steps=["check", "peer"], steps=[agent("check"), agent("peer")]
    )
    host, _, run, first, _ = await command_context(sessions, tmp_path, monkeypatch, "unused", graph=graph, admit=False)
    git = host._git_service
    async with sessions() as session:
        job = await JobRepository(session).get(run.job_id)
        second = await RecipeRunRepository(session).get_step_by_path(run.id, "peer")
    code = await git.rev_parse("HEAD", cwd=job.repo)
    run = run.model_copy(update={"code_ref": RecipeCodeReference(workspace=job.repo, commit=code)})
    entered = asyncio.Event()
    release = asyncio.Event()
    attempted = asyncio.Event()
    original_create = git.create_worktree
    creates = 0

    async def gated_create(*args, **kwargs):
        nonlocal creates
        creates += 1
        entered.set()
        await release.wait()
        return await original_create(*args, **kwargs)

    monkeypatch.setattr(git, "create_worktree", gated_create)
    first_task = asyncio.create_task(
        prepare_workspace(git, job, run, first, {}, preparation_control=host._execution_control)
    )
    await asyncio.wait_for(entered.wait(), timeout=5)

    async def prepare_second():
        attempted.set()
        return await prepare_workspace(git, job, run, second, {}, preparation_control=host._execution_control)

    second_task = asyncio.create_task(prepare_second())
    await asyncio.wait_for(attempted.wait(), timeout=5)
    release.set()
    results = await asyncio.wait_for(asyncio.gather(first_task, second_task, return_exceptions=True), timeout=10)
    assert not any(isinstance(result, BaseException) for result in results), results
    assert creates == 1
    assert results[0].path == results[1].path
    assert await git.rev_parse("HEAD", cwd=results[0].path) == code
    (Path(results[0].path) / "source.txt").write_text("Unexpected modification\n")
    with pytest.raises(StateConflictError, match="differs from its pinned snapshot"):
        await prepare_workspace(git, job, run, first, {}, preparation_control=host._execution_control)
    assert (Path(results[0].path) / "source.txt").read_text() == "Unexpected modification\n"


async def command_context(
    sessions,
    tmp_path,
    monkeypatch,
    command,
    *,
    restricted=False,
    timeout=30,
    admit=True,
    graph=None,
):
    root = tmp_path / "repo"
    root.mkdir()
    config = CPLConfig()
    git = GitService(config)
    await git.run_git("init", cwd=root)
    await git.run_git("config", "user.name", "Recipe test", cwd=root)
    await git.run_git("config", "user.email", "recipe@example.invalid", cwd=root)
    (root / "source.txt").write_text("recorded source\n")
    await git.add_all(cwd=root)
    await git.commit("Initial fixture", cwd=root)
    code = await git.rev_parse("HEAD", cwd=root)
    step = RecipeStep(
        id="check",
        title="Check",
        runner=RecipeCommandRunner(argv=["sh", "-c", command], timeout_seconds=timeout),
        capabilities=["read", "shell"] if restricted else ["read", "shell", "write", "external_access"],
        workspace=RecipeWorkspacePolicy(mode="mutable"),
        outputs={"receipt": RecipeOutput(type="object"), "code": RecipeOutput(type="code_ref")},
        routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
    )
    run = await create_run(
        sessions,
        graph or RecipeDefinition(name="Real command", entry_steps=["check"], steps=[step]),
        repo_path=str(root),
    )
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        await JobRepository(session).update_worktree_path(run.job_id, str(root))
        instance = await repo.get_step_by_path(run.id, "check")
        attempt = None
        if admit:
            instance = await repo.project_step(instance, "ready", "Configured command")
            attempt = await repo.claim_attempt(
                instance,
                inputs={},
                runner=step.runner,
                capabilities=step.capabilities,
                workspace=RecipeResolvedWorkspace(path=str(root), code_ref=code, policy=step.workspace),
            )
        await session.commit()
    monkeypatch.setattr(
        "backend.services.artifacts.artifact_service.get_artifacts_base",
        lambda: tmp_path / "artifacts",
    )
    runtime = RuntimeService(
        session_factory=sessions,
        event_bus=EventBus(),
        adapter_registry=AdapterRegistry(),
        config=config,
        git_service=git,
        approval_service=ApprovalService(sessions),
    )
    router = SimpleNamespace(route=AsyncMock(return_value=SimpleNamespace(proceed=True)), cleanup_job=MagicMock())
    runtime._policy_routers[run.job_id] = router
    configure_test_contract(runtime)
    return runtime, router, run, instance, attempt


async def start_runtime(host, run, sessions, monkeypatch):
    router = host._policy_routers[run.job_id]

    async def setup_policy(*args, **kwargs):
        async with sessions() as session:
            current_run = await RecipeRunRepository(session).get_for_job(run.job_id)
        assert await ExecutionContractRepository(sessions).get(run.job_id, current_run.id) is not None
        host._policy_routers[run.job_id] = router

    host._setup_action_policy = AsyncMock(side_effect=setup_policy)
    host._run_job = AsyncMock()
    host._preflight_curator = object()
    host._run_preflight_curator = AsyncMock()
    host._finalize_artifacts_safe = AsyncMock()
    async with sessions() as session:
        job = await JobRepository(session).get(run.job_id)
    monkeypatch.setattr(
        "backend.services.runtime.service.build_session_config",
        lambda *_: SessionConfig(workspace_path=job.worktree_path, prompt=job.prompt, job_id=job.id),
    )
    await host._start_job(job)
    return host._tasks[job.id]


@pytest.mark.parametrize("retry", [False, True])
async def test_canceled_outcome_route_cancels_the_job_without_cleanup_failure(sessions, tmp_path, monkeypatch, retry):
    step = RecipeStep(
        id="check",
        title="Canceled route",
        runner=RecipeCommandRunner(argv=["sh", "-c", "exit 1"]),
        capabilities=["read", "shell", "write", "external_access"],
        workspace=RecipeWorkspacePolicy(mode="mutable"),
        routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="canceled")},
        retry=RecipeRetryPolicy(max_attempts=2, retry_on=["failed"], exhaustion=RecipeRoute(terminal="canceled"))
        if retry
        else RecipeRetryPolicy(),
    )
    host, _, run, _, _ = await command_context(
        sessions,
        tmp_path,
        monkeypatch,
        "",
        admit=False,
        graph=RecipeDefinition(name="Canceled route", entry_steps=["check"], steps=[step]),
    )
    await asyncio.wait_for(await start_runtime(host, run, sessions, monkeypatch), timeout=15)
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        finished = await repository.get(run.id)
        job = await JobRepository(session).get(run.job_id)
        current = await repository.get_step_by_path(run.id, "check")
        attempts = await repository.list_attempts(current.id)
    assert finished.state == "canceled" and finished.resolved_at is not None
    assert job.state == "canceled" and job.failure_reason is None
    assert len(attempts) == (2 if retry else 1)


async def test_selected_command_stop_retains_uncertain_effects_until_explicit_reconciliation(
    sessions, tmp_path, monkeypatch
):
    from backend.models.schemas.recipes import RecipeReconcileRequest
    from backend.persistence.approval_repo import ApprovalRepository
    from backend.services.recipe.control import RecipeControlService
    from backend.services.recipe.inspection import RecipeInspectionService

    host, _, run, _, _ = await command_context(
        sessions, tmp_path, monkeypatch, "printf started > started.txt; sleep 60", admit=False
    )
    owner_task = await start_runtime(host, run, sessions, monkeypatch)
    try:
        async with asyncio.timeout(10):
            while not (tmp_path / "repo" / "started.txt").exists():
                await asyncio.sleep(0.02)
        async with sessions() as session:
            repository = RecipeRunRepository(session)
            step = await repository.get_step_by_path(run.id, "check")
            service = RecipeControlService(
                RecipeInspectionService(repository, JobRepository(session), ApprovalRepository(session)), host
            )
            context = await service.context(run.job_id, run.id, step.id, step.current_attempt_id)
            assert context.stop.enabled and not context.can_message
            rejected = await service.stop(run.job_id, run.id, step.id, step.current_attempt_id, "stale")
            assert rejected.status == "conflict"
            stopped = await service.stop(
                run.job_id, run.id, step.id, step.current_attempt_id, context.stop.expected_token
            )
            assert stopped.status == "stopped" and stopped.requires_reconciliation
        async with sessions() as session:
            repository = RecipeRunRepository(session)
            step = await repository.get_step(step.id)
            attempt = await repository.get_attempt(step.current_attempt_id)
            assert step.state == "interrupted" and attempt.result.evidence.uncertain
            original_result = attempt.result
            service = RecipeControlService(
                RecipeInspectionService(repository, JobRepository(session), ApprovalRepository(session)), host
            )
            commit = await host._git_service.capture_worktree_commit(
                cwd=attempt.workspace.path, snapshot_ref="refs/codeplane/recipe-inputs/operator-reconciled"
            )
            receipt = await service.reconcile(
                run.job_id,
                run.id,
                step.id,
                attempt.id,
                RecipeReconcileRequest(
                    expected_version=step.version,
                    effects_quiescent=True,
                    evidence="Inspected the owned local process tree and stopped-effect code snapshot.",
                    code=RecipeCodeReference(workspace=attempt.workspace.path, commit=commit),
                ),
            )
            assert receipt.status == "reconciled"
        await asyncio.wait_for(owner_task, timeout=10)
        async with sessions() as session:
            repository = RecipeRunRepository(session)
            assert (await repository.get_attempt(attempt.id)).result == original_result
            assert (await repository.get(run.id)).state == "canceled"
            assert (await JobRepository(session).get(run.job_id)).state == "canceled"
    finally:
        await host.shutdown()


async def test_runtime_drives_the_pinned_command_without_legacy_agent_or_scout(sessions, tmp_path, monkeypatch):
    host, router, run, _, _ = await command_context(
        sessions,
        tmp_path,
        monkeypatch,
        "printf 'runtime command evidence\\n'",
        admit=False,
    )
    task = await start_runtime(host, run, sessions, monkeypatch)
    await asyncio.wait_for(task, timeout=15)
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        finished = await repository.get(run.id)
        step = await repository.get_step_by_path(run.id, "check")
        attempt = await repository.get_attempt(step.current_attempt_id)
        updated_job = await JobRepository(session).get(run.job_id)
    assert finished.state == "succeeded" and finished.resolved_at is not None
    assert updated_job.state == JobState.review
    assert attempt.result.evidence.stdout_preview == "runtime command evidence\n"
    assert attempt.executions == []
    host._run_job.assert_not_awaited()
    host._run_preflight_curator.assert_not_awaited()
    router.route.assert_awaited_once()
    assert not host._adapter_registry._adapters
    assert not host._tasks


async def test_recipe_start_does_not_fall_back_to_main_repo_when_workspace_is_missing(sessions, tmp_path, monkeypatch):
    from backend.models.db import JobRow

    host, _, run, _, _ = await command_context(sessions, tmp_path, monkeypatch, "printf forbidden", admit=False)
    host._setup_action_policy = AsyncMock()
    async with sessions() as session:
        row = await session.get(JobRow, run.job_id)
        row.worktree_path = None
        await session.commit()
        job = await JobRepository(session).get(run.job_id)
    with pytest.raises(StateConflictError, match="prepared worktree"):
        await host._start_job(job)
    host._setup_action_policy.assert_not_awaited()
    assert not host._tasks and not host._adapter_registry._adapters


async def test_explicit_continuation_pins_current_code_and_deduplicates_same_request(sessions, tmp_path, monkeypatch):
    host, router, run, _, _ = await command_context(
        sessions,
        tmp_path,
        monkeypatch,
        "printf once >> counter.txt; printf checked",
        admit=False,
    )
    await asyncio.wait_for(await start_runtime(host, run, sessions, monkeypatch), timeout=15)
    arguments = {
        "expected_run_id": run.id,
        "start_request_key": "explicit-continuation",
    }
    first, duplicate = await asyncio.gather(
        host.resume_recipe_run(run.job_id, "Continue the requested work", **arguments),
        host.resume_recipe_run(run.job_id, "Continue the requested work", **arguments),
    )
    assert first[1].id == duplicate[1].id
    if active := host._tasks.get(run.job_id):
        await asyncio.wait_for(active, timeout=15)
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        previous = await repository.get(run.id)
        continued = await repository.get(first[1].id)
        history = await repository.list_runs(run.job_id)
        previous_step = await repository.get_step_by_path(previous.id, "check")
        next_step = await repository.get_step_by_path(continued.id, "check")
        job = await JobRepository(session).get(run.job_id)
    assert len(history.items) == 2
    assert continued.job_id == previous.job_id
    assert continued.revision_id == previous.revision_id
    assert continued.continuation_of_id == previous.id and continued.run_number == 2
    assert continued.code_ref.commit != previous.code_ref.commit
    assert previous_step.current_attempt_id != next_step.current_attempt_id
    assert job.session_count == 2
    assert (tmp_path / "repo" / "counter.txt").read_text() == "onceonce"
    _, replay = await host.resume_recipe_run(run.job_id, "Continue the requested work", **arguments)
    assert replay.id == continued.id
    with pytest.raises(StateConflictError, match="different"):
        await host.resume_recipe_run(run.job_id, "A different request", **arguments)
    assert (tmp_path / "repo" / "counter.txt").read_text() == "onceonce"
    assert router.route.await_count == 2
    old_pin = await ExecutionContractRepository(sessions).get(run.job_id, previous.id)
    new_pin = await ExecutionContractRepository(sessions).get(run.job_id, continued.id)
    assert old_pin.admission_id != new_pin.admission_id
    assert old_pin.budget_id == new_pin.budget_id == run.job_id


async def test_completed_unrecorded_run_can_explicitly_admit_a_new_pinned_continuation(sessions, tmp_path, monkeypatch):
    from sqlalchemy import delete

    from backend.models.db import EventRow

    host, _, run, _, _ = await command_context(sessions, tmp_path, monkeypatch, "printf checked", admit=False)
    await asyncio.wait_for(await start_runtime(host, run, sessions, monkeypatch), timeout=15)
    async with sessions() as session:
        await session.execute(delete(EventRow).where(EventRow.kind == "execution.contract.pinned"))
        await session.commit()
    assert await ExecutionContractRepository(sessions).get(run.job_id, run.id) is None
    _, following = await host.resume_recipe_run(
        run.job_id,
        "Explicit continuation",
        expected_run_id=run.id,
        start_request_key="after-unrecorded",
    )
    if active := host._tasks.get(run.job_id):
        await asyncio.wait_for(active, timeout=15)
    assert following.id != run.id
    assert await ExecutionContractRepository(sessions).get(run.job_id, run.id) is None
    assert await ExecutionContractRepository(sessions).get(run.job_id, following.id) is not None


@pytest.mark.parametrize("decision", [ApprovalResolution.approved, ApprovalResolution.rejected, None])
async def test_actual_command_waits_for_exact_human_gate_before_followup(sessions, tmp_path, monkeypatch, decision):
    check = RecipeStep(
        id="check",
        title="Check",
        runner=RecipeCommandRunner(argv=["printf", "before gate"]),
        capabilities=["read", "write", "shell", "external_access"],
        workspace=RecipeWorkspacePolicy(mode="mutable"),
        outputs={"receipt": RecipeOutput(type="object"), "code": RecipeOutput(type="code_ref")},
        routes={"succeeded": RecipeRoute(next_steps=["approve"]), "failed": RecipeRoute(terminal="failed")},
    )
    gate = RecipeStep(
        id="approve",
        title="Approve evidence",
        runner=RecipeHumanRunner(proposed_action="Continue after this exact check."),
        workspace=RecipeWorkspacePolicy(mode="none"),
        inputs={
            "evidence": RecipeInput(
                type="object",
                binding=RecipeOutputBinding(step_id="check", output="receipt"),
            )
        },
        depends_on=[RecipeDependency(step_id="check")],
        routes={"approved": RecipeRoute(next_steps=["followup"]), "rejected": RecipeRoute(terminal="failed")},
    )
    followup = RecipeStep(
        id="followup",
        title="Followup",
        runner=RecipeCommandRunner(argv=["printf", "after gate"]),
        capabilities=check.capabilities,
        workspace=check.workspace,
        depends_on=[RecipeDependency(step_id="approve", outcomes=["approved"])],
        routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
    )
    graph = RecipeDefinition(name="Evidence gate", entry_steps=["check"], steps=[check, gate, followup])
    host, router, run, _, _ = await command_context(sessions, tmp_path, monkeypatch, "", admit=False, graph=graph)
    task = await start_runtime(host, run, sessions, monkeypatch)
    approvals = host._approval_service
    try:
        async with asyncio.timeout(10):
            while not (pending := await approvals.list_pending(run.job_id)):
                await asyncio.sleep(0.01)
        async with sessions() as session:
            repo = RecipeRunRepository(session)
            node = await repo.get_step_by_path(run.id, "followup")
            assert node.current_attempt_id is None
        assert len(pending) == 1 and pending[0].requires_explicit_approval
        if decision is None:
            await host.cancel_job(run.job_id)
        else:
            await approvals.resolve(pending[0].id, decision)
        await asyncio.wait_for(task, timeout=15)
    finally:
        if not task.done():
            await host.shutdown()
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        ended = await repo.get(run.id)
        gate_instance = await repo.get_step_by_path(run.id, "approve")
        gate_attempt = await repo.get_attempt(gate_instance.current_attempt_id)
        followup_instance = await repo.get_step_by_path(run.id, "followup")
    assert gate_attempt.result.evidence.approval_id == pending[0].id
    assert gate_attempt.result.outcome == (decision.value if decision else "canceled")
    assert gate_attempt.executions == []
    assert ended.state == (
        "canceled" if decision is None else "succeeded" if decision == ApprovalResolution.approved else "failed"
    )
    assert ended.resolved_at is not None
    assert (followup_instance.current_attempt_id is not None) == (decision == ApprovalResolution.approved)
    assert router.route.await_count == (2 if decision == ApprovalResolution.approved else 1)
    assert not host._adapter_registry._adapters
    assert await approvals.list_pending(run.job_id) == []


@pytest.mark.parametrize("exit_status", [0, 3])
async def test_command_uses_policy_and_retains_exact_exit_and_streams(sessions, tmp_path, monkeypatch, exit_status):
    host, router, run, instance, attempt = await command_context(
        sessions,
        tmp_path,
        monkeypatch,
        f"printf 'exact stdout\\n'; printf 'exact stderr\\n' >&2; exit {exit_status}",
    )
    result, outputs = await CommandStepRunner(host).run(run, instance, attempt)
    assert result.outcome == ("succeeded" if exit_status == 0 else "failed")
    assert result.evidence.exit_status == exit_status
    assert result.evidence.start_code_ref == attempt.workspace.code_ref
    assert result.evidence.end_code_ref == attempt.workspace.code_ref
    router.route.assert_awaited_once()
    assert router.route.call_args.kwargs["cwd"] == attempt.workspace.path
    async with sessions() as session:
        artifacts = ArtifactService.from_session(session)
        for receipt, expected in (
            (result.evidence.stdout_artifact, b"exact stdout\n"),
            (result.evidence.stderr_artifact, b"exact stderr\n"),
        ):
            assert receipt.complete
            artifact = await artifacts.get(receipt.artifact_id)
            assert resolve_artifact_path(artifact, job_id=run.job_id).read_bytes() == expected
            assert receipt.checksum == hashlib.sha256(expected).hexdigest()
        finished = await RecipeRunRepository(session).finish_attempt(attempt.id, result, outputs)
        await session.commit()
        assert finished.result.outcome == result.outcome
        assert finished.executions == []
    assert host._adapter_registry._adapters == {}


async def test_pinned_block_runs_real_child_with_exact_invocation_parameters(sessions, tmp_path, monkeypatch):
    capabilities = ["read", "write", "shell", "external_access"]
    child = RecipeStep(
        id="verify",
        title="Verify",
        runner=RecipeCommandRunner(argv=["printf", RecipeCommandArgument(input="message")]),
        capabilities=capabilities,
        workspace=RecipeWorkspacePolicy(mode="mutable"),
        inputs={"message": RecipeInput(type="string", binding=RecipeParameterBinding(parameter="message"))},
        outputs={"receipt": RecipeOutput(type="object")},
        routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
    )
    definition = RecipeDefinition(
        name="Pinned check",
        kind="block",
        entry_steps=["verify"],
        steps=[child],
        parameters={"message": RecipeParameter(type="string")},
        outputs={
            "receipt": RecipeExport(type="object", binding=RecipeOutputBinding(step_id="verify", output="receipt"))
        },
    )
    async with sessions() as session:
        definitions = RecipeDefinitionService(RecipeRepository(session))
        draft = await definitions.create(definition)
        revision = await definitions.publish(draft.id, expected_version=1)
        await session.commit()
    wrapper = RecipeStep(
        id="check",
        title="Pinned block",
        runner=RecipeBlockRunner(revision_id=revision.id),
        capabilities=capabilities,
        workspace=RecipeWorkspacePolicy(mode="mutable"),
        inputs={
            "message": RecipeInput(type="string", binding=RecipeLiteralBinding(value="nested payload with spaces"))
        },
        outputs={"receipt": RecipeOutput(type="object")},
        routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
    )
    graph = RecipeDefinition(name="Block caller", entry_steps=["check"], steps=[wrapper])
    host, router, run, _, _ = await command_context(sessions, tmp_path, monkeypatch, "", admit=False, graph=graph)
    task = await start_runtime(host, run, sessions, monkeypatch)
    try:
        await asyncio.wait_for(task, timeout=15)
    finally:
        if not task.done():
            await host.shutdown()
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        ended = await repository.get(run.id)
        outer = await repository.get_step_by_path(run.id, "check")
        inner = await repository.get_step_by_path(run.id, "check@1/verify")
        outer_attempt = await repository.get_attempt(outer.current_attempt_id)
        inner_attempt = await repository.get_attempt(inner.current_attempt_id)
    assert ended.state == "succeeded"
    assert inner_attempt.inputs["message"].scope_attempt_id == outer_attempt.id
    assert inner_attempt.inputs["message"].value == "nested payload with spaces"
    assert inner_attempt.result.evidence.argv == ["printf", "nested payload with spaces"]
    assert outer_attempt.result.evidence.child_attempt_ids == [inner_attempt.id]
    command_receipt = outer_attempt.outputs[0].value
    assert isinstance(command_receipt, dict)
    assert command_receipt["stdout_preview"] == "nested payload with spaces"
    assert outer_attempt.executions == inner_attempt.executions == []
    router.route.assert_awaited_once()


async def test_real_failed_checks_exhaust_total_attempt_bound_before_human_gate(sessions, tmp_path, monkeypatch):
    check = RecipeStep(
        id="check",
        title="Bounded command check",
        runner=RecipeCommandRunner(argv=["sh", "-c", "printf diagnostic; exit 1"]),
        capabilities=["read", "write", "shell", "external_access"],
        workspace=RecipeWorkspacePolicy(mode="mutable"),
        outputs={"receipt": RecipeOutput(type="object")},
        retry=RecipeRetryPolicy(max_attempts=3, retry_on=["failed"], exhaustion=RecipeRoute(next_steps=["decide"])),
        routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
    )
    gate = RecipeStep(
        id="decide",
        title="Resolve exhausted attempts",
        runner=RecipeHumanRunner(proposed_action="Review the final failed check."),
        workspace=RecipeWorkspacePolicy(mode="none"),
        inputs={
            "diagnostic": RecipeInput(
                type="object",
                binding=RecipeOutputBinding(step_id="check", output="receipt"),
                optional=True,
                degraded_reason="This is failed diagnostic evidence, not a passing check.",
            )
        },
        depends_on=[RecipeDependency(step_id="check", outcomes=["failed"])],
        routes={"approved": RecipeRoute(terminal="succeeded"), "rejected": RecipeRoute(terminal="failed")},
    )
    host, router, run, _, _ = await command_context(
        sessions,
        tmp_path,
        monkeypatch,
        "",
        admit=False,
        graph=RecipeDefinition(name="Bounded check", entry_steps=["check"], steps=[check, gate]),
    )
    task = await start_runtime(host, run, sessions, monkeypatch)
    try:
        async with asyncio.timeout(10):
            while not (pending := await host._approval_service.list_pending(run.job_id)):
                await asyncio.sleep(0.01)
        async with sessions() as session:
            repository = RecipeRunRepository(session)
            step = await repository.get_step_by_path(run.id, "check")
            attempts = await repository.list_attempts(step.id)
            decision = await repository.get_step_by_path(run.id, "decide")
            consumed = await repository.get_attempt(decision.current_attempt_id)
        assert len(attempts) == 3
        assert all(item.result.outcome == "failed" and item.result.evidence.exit_status == 1 for item in attempts)
        assert consumed.inputs["diagnostic"].producing_attempt_id == attempts[-1].id
        assert consumed.inputs["diagnostic"].producer_outcome == "failed"
        await host._approval_service.resolve(pending[0].id, ApprovalResolution.rejected)
        await asyncio.wait_for(task, timeout=15)
    finally:
        if not task.done():
            await host.shutdown()
    assert router.route.await_count == 3


@pytest.mark.parametrize("merge_code", [False, True])
async def test_parallel_writers_require_actual_code_reconciliation(sessions, tmp_path, monkeypatch, merge_code):
    capabilities = ["read", "write", "shell", "external_access"]
    writers = [
        RecipeStep(
            id=name,
            title=name,
            runner=RecipeCommandRunner(argv=["sh", "-c", f"printf {name} > {name}.txt"]),
            capabilities=capabilities,
            workspace=RecipeWorkspacePolicy(mode="isolated", key=name),
            outputs={"code": RecipeOutput(type="code_ref")},
            routes={"succeeded": RecipeRoute(next_steps=["check"]), "failed": RecipeRoute(terminal="failed")},
        )
        for name in ["left", "right"]
    ]
    reconcile = RecipeStep(
        id="check",
        title="Reconcile code",
        runner=RecipeCommandRunner(
            argv=[
                "git",
                "merge",
                "--no-edit",
                RecipeCommandArgument(input="left", field="commit"),
                RecipeCommandArgument(input="right", field="commit"),
            ]
            if merge_code
            else ["true"],
            reconciles=["left", "right"],
        ),
        capabilities=capabilities,
        workspace=RecipeWorkspacePolicy(mode="mutable"),
        inputs={
            name: RecipeInput(type="code_ref", binding=RecipeOutputBinding(step_id=name, output="code"))
            for name in ["left", "right"]
        },
        depends_on=[RecipeDependency(step_id="left"), RecipeDependency(step_id="right")],
        outputs={"receipt": RecipeOutput(type="object"), "code": RecipeOutput(type="code_ref")},
        routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
    )
    host, _, run, _, _ = await command_context(
        sessions,
        tmp_path,
        monkeypatch,
        "",
        admit=False,
        graph=RecipeDefinition(name="Parallel code", entry_steps=["left", "right"], steps=[*writers, reconcile]),
    )
    task = await start_runtime(host, run, sessions, monkeypatch)
    try:
        if merge_code:
            async with asyncio.timeout(10):
                while not (pending := await host._approval_service.list_pending(run.job_id)):
                    await asyncio.sleep(0.01)
            assert pending[0].requires_explicit_approval
            await host._approval_service.resolve(pending[0].id, ApprovalResolution.approved)
        await asyncio.wait_for(task, timeout=20)
    finally:
        if not task.done():
            await host.shutdown()
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        ended = await repository.get(run.id)
        node = await repository.get_step_by_path(run.id, "check")
        attempt = await repository.get_attempt(node.current_attempt_id)
    assert attempt.result.evidence.exit_status == 0
    if merge_code:
        assert ended.state == "succeeded"
        assert (tmp_path / "repo" / "left.txt").read_text() == "left"
        assert (tmp_path / "repo" / "right.txt").read_text() == "right"
    else:
        assert ended.state == "failed"
        assert "ancestry" in attempt.result.reason
        assert not (tmp_path / "repo" / "left.txt").exists()
        assert not (tmp_path / "repo" / "right.txt").exists()


async def test_unproven_command_cannot_bypass_read_only_capabilities(sessions, tmp_path, monkeypatch):
    host, router, run, instance, attempt = await command_context(
        sessions,
        tmp_path,
        monkeypatch,
        "printf forbidden > changed.txt",
        restricted=True,
    )
    result, _ = await CommandStepRunner(host).run(run, instance, attempt)
    assert result.outcome == "failed" and not result.evidence.started
    assert result.evidence.exit_status is None
    assert not (Path(attempt.workspace.path) / "changed.txt").exists()
    router.route.assert_not_awaited()
    assert host._adapter_registry._adapters == {}


@pytest.mark.parametrize("failure", ["timeout", "output_limit"])
async def test_incomplete_capture_cannot_be_a_passing_check(sessions, tmp_path, monkeypatch, failure):
    command = "printf beginning; sleep 60" if failure == "timeout" else "printf oversized-output"
    host, _, run, instance, attempt = await command_context(
        sessions,
        tmp_path,
        monkeypatch,
        command,
        timeout=1,
    )
    if failure == "output_limit":
        monkeypatch.setattr("backend.services.recipe.command_runner._CAPTURE_LIMIT", 7)
    result, outputs = await CommandStepRunner(host).run(run, instance, attempt)
    assert result.outcome == "interrupted" and result.evidence.uncertain
    assert result.evidence.stdout_artifact.complete is False
    async with sessions() as session:
        finished = await RecipeRunRepository(session).finish_attempt(attempt.id, result, outputs)
        await session.commit()
        assert finished.result.outcome == "interrupted"
