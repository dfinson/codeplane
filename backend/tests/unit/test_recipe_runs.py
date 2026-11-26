"""Persisted recipe admission, typed handoff and cursor safety."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.models.db import Base, JobRow, ProjectRow, RecipeAttemptRow, RecipeWorkspaceLeaseRow
from backend.models.domain import ApprovalResolution, StateConflictError
from backend.models.schemas.recipes import (
    RecipeAgentResult,
    RecipeAgentRunner,
    RecipeAttemptExecution,
    RecipeAttemptResult,
    RecipeBlockResult,
    RecipeBlockRunner,
    RecipeCommandArgument,
    RecipeCommandResult,
    RecipeCommandRunner,
    RecipeDefinition,
    RecipeDependency,
    RecipeGateProposal,
    RecipeHumanResult,
    RecipeHumanRunner,
    RecipeInput,
    RecipeInputReceipt,
    RecipeOutput,
    RecipeParameter,
    RecipeParameterBinding,
    RecipeResolvedWorkspace,
    RecipeRetryPolicy,
    RecipeRoute,
    RecipeSelection,
    RecipeStep,
    RecipeWorkspacePolicy,
)
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.recipe_repo import RecipeRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.job.approval_service import ApprovalService
from backend.services.project.repo_membership import normalize_repo_path
from backend.services.recipe.definition_service import RecipeDefinitionService
from backend.services.recipe.run_service import RecipeRunService, resolve_command_arguments
from backend.services.recipe.run_state import can_retry, completed_route, completed_run_state, project_cursor
from backend.services.recipe.validation import content_checksum, validate_definition
from backend.tests.unit.test_recipe_definitions import agent, received

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from backend.persistence import database

    monkeypatch.setattr(database, "_write_lock", None)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runs.db'}")
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_run(sessions, definition, job_id="job", *, repo_path="/repo"):
    async with sessions() as session:
        now = datetime.now(UTC)
        session.add(
            ProjectRow(id=job_id, name=job_id, repo_paths=json.dumps([repo_path]), created_at=now, updated_at=now)
        )
        await session.flush()
        session.add(
            JobRow(
                id=job_id,
                project_id=job_id,
                repo=repo_path,
                prompt="A real Job owns this run",
                state="running",
                base_ref="main",
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        definitions = RecipeDefinitionService(RecipeRepository(session))
        draft = await definitions.create(definition)
        revision = await definitions.publish(draft.id, expected_version=1)
        service = RecipeRunService(definitions, RecipeRunRepository(session))
        snapshot, parameters = await service.prepare_selection(RecipeSelection(revision_id=revision.id))
        run = await service._runs.create(job_id, snapshot, parameters)
        await session.commit()
        return run


async def ready_attempt(session, run, step_id, workspace="/repo/snapshot"):
    repo = RecipeRunRepository(session)
    instance = await repo.get_step_by_path(run.id, step_id)
    instance = await repo.project_step(instance, "ready", "Selected by the cursor.")
    service = RecipeRunService(RecipeDefinitionService(RecipeRepository(session)), repo)
    inputs = await service.resolve_inputs(instance)
    runner = instance.definition.runner
    if isinstance(runner, RecipeAgentRunner):
        job = await session.get(JobRow, run.job_id)
        provider = runner.provider or job.sdk
        runner = runner.model_copy(
            update={
                "provider": provider,
                "model": runner.model if runner.model is not None else job.model if provider == job.sdk else None,
            }
        )
    attempt = await repo.claim_attempt(
        instance,
        inputs=inputs,
        runner=runner,
        capabilities=instance.definition.capabilities,
        workspace=RecipeResolvedWorkspace(
            path=normalize_repo_path(workspace), code_ref="immutable-code", policy=instance.definition.workspace
        ),
    )
    await session.commit()
    return attempt


def agent_result(outcome="succeeded", *, uncertain=False):
    return RecipeAttemptResult(
        outcome=outcome,
        evidence=RecipeAgentResult(session_ids=[], text="Exact result", uncertain=uncertain),
        reason=None,
        completed_at=datetime.now(UTC),
    )


def test_command_arguments_preserve_exact_values_without_shell_interpolation():
    text = "$(touch unwanted); with spaces"
    inputs = {
        "name": RecipeInputReceipt(type="string", present=True, value=text, checksum=content_checksum(text)),
        "code": RecipeInputReceipt(
            type="code_ref",
            present=True,
            value={"workspace": "/space in/path", "commit": "a" * 40},
            checksum=content_checksum({"workspace": "/space in/path", "commit": "a" * 40}),
        ),
    }
    runner = RecipeCommandRunner(
        argv=["check", "", RecipeCommandArgument(input="name"), RecipeCommandArgument(input="code", field="commit")]
    )
    assert resolve_command_arguments(runner, inputs) == ["check", "", text, "a" * 40]
    inputs["name"] = RecipeInputReceipt(type="string", present=False, degraded_reason="Missing")
    with pytest.raises(StateConflictError, match="unavailable"):
        resolve_command_arguments(runner, inputs)


async def test_cancellation_before_claim_prevents_dispatch_identity(sessions):
    run = await create_run(sessions, RecipeDefinition(name="Cancel", entry_steps=["work"], steps=[agent("work")]))
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        instance = await repo.get_step_by_path(run.id, "work")
        instance = await repo.project_step(instance, "ready", "Ready")
        await repo.request_cancel(run.id)
        await session.commit()
        with pytest.raises(StateConflictError, match="no longer admits"):
            async with session.begin_nested():
                await repo.claim_attempt(
                    instance,
                    inputs={},
                    runner=instance.definition.runner,
                    capabilities=["read"],
                    workspace=RecipeResolvedWorkspace(path="/repo", code_ref="sha", policy=RecipeWorkspacePolicy()),
                )
        assert await session.scalar(select(func.count()).select_from(RecipeAttemptRow)) == 0


async def test_claim_is_fenced_by_exact_instance_version(sessions):
    run = await create_run(sessions, RecipeDefinition(name="Fence", entry_steps=["work"], steps=[agent("work")]))
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        before = await repo.get_step_by_path(run.id, "work")
        attempt = await ready_attempt(session, run, "work")
        with pytest.raises(StateConflictError):
            async with session.begin_nested():
                await repo.claim_attempt(
                    before,
                    inputs={},
                    runner=before.definition.runner,
                    capabilities=["read"],
                    workspace=attempt.workspace,
                )
        assert [item.id for item in await repo.list_attempts(before.id)] == [attempt.id]


async def test_missing_outputs_fail_and_cannot_satisfy_a_join(sessions):
    context = agent("context", next_steps=["join"])
    join = agent("join")
    join.inputs = {"context": received("context")}
    join.depends_on = [RecipeDependency(step_id="context")]
    definition = RecipeDefinition(name="Join", entry_steps=["context"], steps=[context, join])
    run = await create_run(sessions, definition)
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "context")
        ended = await repo.finish_attempt(attempt.id, agent_result(), {})
        await session.commit()
        assert ended.result.outcome == "failed"
        assert not ended.outputs
        instances = {step.step_path: step for step in await repo.list_steps(run.id)}
        decisions = project_cursor(definition, instances, {"context": ended})
        assert [(item.step_id, item.state) for item in decisions] == [("join", "skipped")]
        service = RecipeRunService(RecipeDefinitionService(RecipeRepository(session)), repo)
        with pytest.raises(StateConflictError, match="unavailable"):
            await service.resolve_inputs(instances["join"])


async def test_exact_handoff_keeps_content_checksum_and_producing_attempt(sessions):
    context = agent("context", next_steps=["implement"])
    implement = agent("implement")
    implement.inputs = {"brief": received("context")}
    implement.depends_on = [RecipeDependency(step_id="context")]
    definition = RecipeDefinition(name="Handoff", entry_steps=["context"], steps=[context, implement])
    run = await create_run(sessions, definition)
    brief = "Exact brief\n\nwith whitespace and receipts."
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        first = await ready_attempt(session, run, "context")
        producer = await repo.finish_attempt(first.id, agent_result(), {"result": brief})
        await session.commit()
        ready = await repo.get_step_by_path(run.id, "implement")
        ready = await repo.project_step(ready, "ready", "Producer succeeded")
        service = RecipeRunService(RecipeDefinitionService(RecipeRepository(session)), repo)
        forged = await service.resolve_inputs(ready)
        forged["brief"] = forged["brief"].model_copy(update={"receipt_id": None, "producing_attempt_id": None})
        with pytest.raises(StateConflictError, match="exact producing output receipt"):
            await repo.claim_attempt(
                ready,
                inputs=forged,
                runner=ready.definition.runner,
                capabilities=ready.definition.capabilities,
                workspace=RecipeResolvedWorkspace(
                    path="/repo/snapshot", code_ref="immutable-code", policy=ready.definition.workspace
                ),
            )
        consumer = await ready_attempt(session, run, "implement")
        receipt = consumer.inputs["brief"]
        assert receipt.value == brief and receipt.present
        assert receipt.checksum == producer.outputs[0].checksum == content_checksum(brief)
        assert receipt.receipt_id == producer.outputs[0].id
        assert receipt.producing_attempt_id == first.id
    async with sessions() as restarted_session:
        retained = await RecipeRunRepository(restarted_session).get_attempt(consumer.id)
        assert retained.inputs["brief"] == receipt


async def test_explicit_degraded_route_retains_failed_evidence_without_satisfying_success_join(sessions):
    check = agent("check")
    check.routes["failed"] = RecipeRoute(next_steps=["repair"])
    repair = agent("repair")
    repair.inputs = {"diagnostic": received("check", optional=True)}
    repair.depends_on = [RecipeDependency(step_id="check", outcomes=["failed"])]
    definition = RecipeDefinition(name="Diagnostic route", entry_steps=["check"], steps=[check, repair])
    run = await create_run(sessions, definition)
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "check")
        await repo.finish_attempt(attempt.id, agent_result("failed"), {"result": "Actual failure evidence"})
        await session.commit()
        consumer = await ready_attempt(session, run, "repair")
        receipt = consumer.inputs["diagnostic"]
        assert receipt.present and receipt.value == "Actual failure evidence"
        assert receipt.producer_outcome == "failed"
        assert receipt.degraded_reason == repair.inputs["diagnostic"].degraded_reason
        assert receipt.producing_attempt_id == attempt.id


@pytest.mark.parametrize("exit_status, expected", [(1, "failed"), (None, "interrupted")])
async def test_command_exit_and_capture_override_success_claims(sessions, exit_status, expected):
    check = agent("check")
    check.runner = RecipeCommandRunner(argv=["check"])
    check.capabilities = ["read", "shell"]
    check.outputs = {"result": RecipeOutput(type="object")}
    definition = RecipeDefinition(name="Command evidence", entry_steps=["check"], steps=[check])
    run = await create_run(sessions, definition)
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "check")
        evidence = RecipeCommandResult(
            argv=["check"],
            exit_status=exit_status,
            stdout="Actual captured output",
            stderr="",
            workspace=attempt.workspace.path,
            start_code_ref=attempt.workspace.code_ref,
            end_code_ref=attempt.workspace.code_ref,
        )
        result = RecipeAttemptResult(
            outcome="succeeded", evidence=evidence, reason=None, completed_at=datetime.now(UTC)
        )
        wrong = result.model_copy(deep=True)
        wrong.evidence.workspace = "/other/workspace"
        with pytest.raises(StateConflictError, match="different invocation"):
            await repo.finish_attempt(attempt.id, wrong, {"result": evidence.model_dump(mode="json")})
        ended = await repo.finish_attempt(attempt.id, result, {"result": evidence.model_dump(mode="json")})
        await session.commit()
        assert ended.result.outcome == expected
        assert ended.result.evidence.exit_status == exit_status
        assert bool(await session.scalar(select(RecipeWorkspaceLeaseRow.attempt_id))) == (expected == "interrupted")


async def test_failed_attempts_are_preserved_and_repair_exhaustion_is_bounded(sessions):
    work = agent("work")
    work.retry = RecipeRetryPolicy(max_attempts=2, retry_on=["failed"])
    definition = RecipeDefinition(name="Repair", entry_steps=["work"], steps=[work])
    run = await create_run(sessions, definition)
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        first = await ready_attempt(session, run, "work")
        first = await repo.finish_attempt(first.id, agent_result("failed"), {})
        await session.commit()
        instances = {step.step_path: step for step in await repo.list_steps(run.id)}
        assert can_retry(work, first) and completed_route(work, first) is None
        assert completed_run_state(definition, instances, {"work": first}) is None
        assert project_cursor(definition, instances, {"work": first})[0].state == "ready"
        second = await ready_attempt(session, run, "work")
        second = await repo.finish_attempt(second.id, agent_result("failed"), {})
        await session.commit()
        assert second.number == 2 and not can_retry(work, second)
        assert completed_route(work, second).terminal == "failed"
        assert [item.id for item in await repo.list_attempts(first.step_instance_id)] == [first.id, second.id]
        with pytest.raises(StateConflictError, match="immutable"):
            await repo.finish_attempt(first.id, agent_result(), {"result": "Overwrite"})
        with pytest.raises(StateConflictError, match="limit"):
            async with session.begin_nested():
                await ready_attempt(session, run, "work")


async def test_mutable_workspace_excludes_other_jobs_until_writer_finishes(sessions):
    work = agent("work")
    work.capabilities = ["read", "write"]
    work.workspace.mode = "mutable"
    definition = RecipeDefinition(name="Writer", entry_steps=["work"], steps=[work])
    left = await create_run(sessions, definition, job_id="left")
    right = await create_run(sessions, definition, job_id="right")
    async with sessions() as session:
        first = await ready_attempt(session, left, "work", workspace="/same/workspace")
    async with sessions() as session:
        with pytest.raises(StateConflictError, match="writer already"):
            await ready_attempt(session, right, "work", workspace="/same/workspace")
        await session.rollback()
    async with sessions() as session:
        await RecipeRunRepository(session).finish_attempt(first.id, agent_result(), {"result": "Finished"})
        await session.commit()
    async with sessions() as session:
        second = await ready_attempt(session, right, "work", workspace="/same/workspace")
        assert second.id != first.id
        assert await session.scalar(select(func.count()).select_from(RecipeWorkspaceLeaseRow)) == 1


async def test_uncertain_effect_retains_writer_lease_and_forbids_blind_retry(sessions):
    work = agent("work")
    work.capabilities = ["read", "write"]
    work.workspace.mode = "mutable"
    work.retry = RecipeRetryPolicy(max_attempts=2, retry_on=["failed"])
    definition = RecipeDefinition(name="Uncertain", entry_steps=["work"], steps=[work])
    run = await create_run(sessions, definition)
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "work")
        ended = await repo.finish_attempt(attempt.id, agent_result("failed", uncertain=True), {})
        await session.commit()
        assert ended.result.outcome == "interrupted"
        assert not can_retry(work, ended)
        assert completed_route(work, ended) is None
        assert await session.scalar(select(RecipeWorkspaceLeaseRow.attempt_id)) == attempt.id


async def test_canceled_uncertainty_without_a_writer_lease_still_blocks_a_successor(sessions):
    run = await create_run(
        sessions, RecipeDefinition(name="Unknown effect", entry_steps=["work"], steps=[agent("work")])
    )
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "work")
        await repo.request_cancel(run.id)
        ended = await repo.finish_attempt(attempt.id, agent_result("failed", uncertain=True), {})
        assert ended.result.outcome == "canceled"
        assert await session.scalar(select(RecipeWorkspaceLeaseRow.attempt_id)) is None
        with pytest.raises(StateConflictError, match="remains unresolved"):
            await repo.resolve_run(run.id, "canceled")
        assert (await repo.get(run.id)).resolved_at is None


async def test_gate_recovery_does_not_resurrect_a_concurrently_resolved_request(sessions, monkeypatch):
    await create_run(sessions, RecipeDefinition(name="Gate owner", entry_steps=["work"], steps=[agent("work")]))
    original = ApprovalService(sessions)
    approval = await original.create_request("job", "Pending decision", requires_explicit_approval=True)
    stale = await original.list_pending("job")
    await original.resolve(approval.id, ApprovalResolution.approved)
    recovered = ApprovalService(sessions)
    make_repo = recovered._make_repo

    def stale_pending(session):
        from unittest.mock import AsyncMock

        repo = make_repo(session)
        repo.list_pending = AsyncMock(return_value=stale)
        return repo

    monkeypatch.setattr(recovered, "_make_repo", stale_pending)
    assert await recovered.recover_pending_approvals(job_id="job") == 0
    assert await recovered.wait_for_resolution(approval.id) == ApprovalResolution.approved


@pytest.mark.parametrize("safe", [False, True])
async def test_interrupted_agent_resumes_same_attempt_only_after_confirmed_safe_stop(sessions, safe):
    work = agent("work")
    work.capabilities = ["read", "write"]
    work.workspace.mode = "mutable"
    run = await create_run(sessions, RecipeDefinition(name="Recover", entry_steps=["work"], steps=[work]))
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "work")
        execution = RecipeAttemptExecution(
            execution_token="epoch-one-session-one",
            session_id="real-session-one",
            provider="copilot",
            model=None,
            started_at=datetime.now(UTC),
        )
        await repo.record_execution(attempt.id, execution)
        before = await repo.get_step(attempt.step_instance_id)
        await repo.interrupt_attempt(
            attempt.id,
            "Server shutdown",
            stop_confirmed=safe,
            resume_safe=True,
            execution_epoch="epoch-one",
            expected_execution=execution.execution_token,
            expected_instance_version=before.version,
        )
        await session.commit()
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        instance = await repo.get_step(attempt.step_instance_id)
        if not safe:
            with pytest.raises(StateConflictError, match="confirmed-safe"):
                await repo.resume_interrupted(instance)
            assert await session.scalar(select(RecipeWorkspaceLeaseRow.attempt_id)) == attempt.id
            return
        resumed = await repo.resume_interrupted(instance)
        await session.commit()
        assert resumed.id == attempt.id and resumed.number == 1 and resumed.result is None
        assert [item.session_id for item in resumed.executions] == ["real-session-one"]
        assert await session.scalar(select(RecipeWorkspaceLeaseRow.attempt_id)) == attempt.id
        with pytest.raises(StateConflictError, match="no longer current"):
            await repo.interrupt_attempt(
                attempt.id,
                "Delayed old shutdown callback",
                stop_confirmed=True,
                resume_safe=True,
                execution_epoch="epoch-one",
                expected_execution=execution.execution_token,
                expected_instance_version=before.version,
            )
        assert (await repo.get_step(attempt.step_instance_id)).state == "running"
        assert len(await repo.list_attempts(attempt.step_instance_id)) == 1


async def test_cancellation_fences_previously_safe_interrupted_recovery(sessions):
    run = await create_run(sessions, RecipeDefinition(name="Stop", entry_steps=["work"], steps=[agent("work")]))
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "work")
        instance = await repo.get_step(attempt.step_instance_id)
        await repo.interrupt_attempt(
            attempt.id,
            "Before dispatch",
            stop_confirmed=True,
            resume_safe=True,
            execution_epoch="epoch-one",
            expected_execution=None,
            expected_instance_version=instance.version,
        )
        await repo.request_cancel(run.id)
        await session.commit()
        instance = await repo.get_step(instance.id)
        with pytest.raises(StateConflictError, match="no longer admits"):
            await repo.resume_interrupted(instance)
        assert (await repo.get_step(instance.id)).state == "interrupted"


async def test_block_retry_has_independent_children_and_exact_parameter_scope(sessions):
    child = agent("child")
    child.outputs = {}
    child.inputs = {
        "message": RecipeInput(type="string", binding=RecipeParameterBinding(parameter="message")),
        "count": RecipeInput(type="integer", binding=RecipeParameterBinding(parameter="count")),
    }
    block = RecipeDefinition(
        name="Reusable work",
        kind="block",
        entry_steps=["child"],
        steps=[child],
        parameters={
            "message": RecipeParameter(type="string", required=False, default="fallback"),
            "count": RecipeParameter(type="integer", required=False, default=7),
        },
    )
    async with sessions() as session:
        definitions = RecipeDefinitionService(RecipeRepository(session))
        draft = await definitions.create(block)
        revision = await definitions.publish(draft.id, expected_version=1)
        await session.commit()
    call = RecipeStep(
        id="call",
        title="Call block",
        runner=RecipeBlockRunner(revision_id=revision.id),
        capabilities=["read"],
        inputs={"message": received("source", optional=True)},
        depends_on=[RecipeDependency(step_id="source", outcomes=["failed"])],
        retry=RecipeRetryPolicy(max_attempts=2, retry_on=["failed"]),
        routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
    )
    source = agent("source")
    source.routes["failed"] = RecipeRoute(next_steps=["call"])
    run = await create_run(sessions, RecipeDefinition(name="Caller", entry_steps=["source"], steps=[source, call]))
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        producing = await ready_attempt(session, run, "source")
        await repo.finish_attempt(producing.id, agent_result("failed"), {"result": "exact input"})
        await session.commit()
        first = await ready_attempt(session, run, "call")
        await repo.ensure_scope(run.id, "call@1", block)
        child_instance = await repo.get_step_by_path(run.id, "call@1/child")
        child_instance = await repo.project_step(child_instance, "ready", "Pinned block invocation")
        service = RecipeRunService(RecipeDefinitionService(RecipeRepository(session)), repo)
        forged = await service.resolve_inputs(child_instance)
        forged["message"] = forged["message"].model_copy(
            update={
                "receipt_id": None,
                "producing_attempt_id": None,
                "producer_outcome": None,
            }
        )
        with pytest.raises(StateConflictError, match="source provenance"):
            await repo.claim_attempt(
                child_instance,
                inputs=forged,
                runner=child.runner,
                capabilities=child.capabilities,
                workspace=RecipeResolvedWorkspace(path="/repo/snapshot", code_ref="code", policy=child.workspace),
            )
        child_one = await ready_attempt(session, run, "call@1/child")
        assert child_one.inputs["message"].value == "exact input"
        assert child_one.inputs["message"].scope_attempt_id == first.id
        assert child_one.inputs["message"].producing_attempt_id == producing.id
        assert child_one.inputs["message"].producer_outcome == "failed"
        assert child_one.inputs["count"].value == 7
        await repo.finish_attempt(child_one.id, agent_result("failed"), {})
        await repo.finish_attempt(
            first.id,
            RecipeAttemptResult(
                outcome="failed",
                evidence=RecipeBlockResult(revision_id=revision.id, child_attempt_ids=[child_one.id]),
                reason="Child failed",
                completed_at=datetime.now(UTC),
            ),
            {},
        )
        await session.commit()
        second = await ready_attempt(session, run, "call")
        await repo.ensure_scope(run.id, "call@2", block)
        child_two = await ready_attempt(session, run, "call@2/child")
        assert child_two.id != child_one.id and child_two.step_instance_id != child_one.step_instance_id
        assert child_two.number == 1
        assert child_two.inputs["message"].scope_attempt_id == second.id
        assert (await repo.get_attempt(child_one.id)).result.outcome == "failed"
        with pytest.raises(StateConflictError, match="current unfinished"):
            await repo.ensure_scope(run.id, "call@1", block)
        altered = block.model_copy(deep=True)
        altered.steps[0].instructions = "Not the published instructions"
        with pytest.raises(StateConflictError, match="pinned invocation"):
            await repo.ensure_scope(run.id, "call@2", altered)


async def test_command_that_never_started_cannot_report_a_passing_check(sessions):
    step = agent("check")
    step.runner = RecipeCommandRunner(argv=["missing-check"])
    step.capabilities = ["shell"]
    step.outputs = {}
    run = await create_run(sessions, RecipeDefinition(name="Launch failure", entry_steps=["check"], steps=[step]))
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "check")
        finished = await repo.finish_attempt(
            attempt.id,
            RecipeAttemptResult(
                outcome="succeeded",
                reason="Executable missing",
                completed_at=datetime.now(UTC),
                evidence=RecipeCommandResult(
                    argv=["missing-check"],
                    started=False,
                    exit_status=None,
                    stdout=None,
                    stderr=None,
                    workspace=attempt.workspace.path,
                    start_code_ref=attempt.workspace.code_ref,
                    end_code_ref=None,
                ),
            ),
            {},
        )
        await session.commit()
        assert finished.result.outcome == "failed"
        assert finished.result.evidence.exit_status is None
        assert not finished.result.evidence.uncertain
        assert await session.scalar(select(RecipeWorkspaceLeaseRow.attempt_id)) is None


async def test_human_output_is_typed_handoff_and_requires_the_exact_approval(sessions):
    gate = RecipeStep(
        id="decision",
        title="Decision",
        runner=RecipeHumanRunner(proposed_action="Proceed to implementation"),
        workspace=RecipeWorkspacePolicy(mode="none"),
        outputs={"result": RecipeOutput(type="string")},
        routes={"approved": RecipeRoute(next_steps=["implement"]), "rejected": RecipeRoute(terminal="failed")},
    )
    implement = agent("implement")
    implement.inputs = {"authority": received("decision")}
    implement.depends_on = [RecipeDependency(step_id="decision", outcomes=["approved"])]
    definition = RecipeDefinition(name="Authority", entry_steps=["decision"], steps=[gate, implement])
    assert validate_definition(definition).valid
    run = await create_run(sessions, definition)
    async with sessions() as session:
        attempt = await ready_attempt(session, run, "decision")
    approvals = ApprovalService(session_factory=sessions)
    proposal = RecipeGateProposal(
        recipe_run_id=run.id,
        step_instance_id=attempt.step_instance_id,
        attempt_id=attempt.id,
        input_checksum=content_checksum({}),
        proposed_action=gate.runner.proposed_action,
        next_steps={name: route.next_steps for name, route in gate.routes.items()},
    )
    approval = await approvals.create_request(
        "job",
        "Approve the exact decision output",
        proposal.model_dump_json(by_alias=True),
        requires_explicit_approval=True,
    )
    result = RecipeAttemptResult(
        outcome="approved",
        evidence=RecipeHumanResult(
            approval_id=approval.id, decision="approved", input_checksum=proposal.input_checksum
        ),
        reason=None,
        completed_at=datetime.now(UTC),
    )
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        await repo.bind_gate(proposal, approval.id)
        await session.commit()
        with pytest.raises(StateConflictError, match="Only a live agent"):
            await repo.record_execution(
                attempt.id,
                RecipeAttemptExecution(
                    execution_token="fictional",
                    session_id="fictional",
                    provider="copilot",
                    model=None,
                    started_at=datetime.now(UTC),
                ),
            )
        with pytest.raises(StateConflictError, match="decision is missing"):
            await repo.finish_attempt(attempt.id, result, {"result": "approved"})
    await approvals.cleanup_job("job")
    assert (await approvals.get(approval.id)).resolution is None
    await approvals.resolve(approval.id, ApprovalResolution.approved)
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        wrong = result.model_copy(deep=True)
        wrong.evidence.input_checksum = "stale"
        with pytest.raises(StateConflictError, match="decision is missing"):
            await repo.finish_attempt(attempt.id, wrong, {"result": "approved"})
        await repo.finish_attempt(attempt.id, result, {"result": "approved"})
        await session.commit()
        consumer = await ready_attempt(session, run, "implement")
        assert consumer.inputs["authority"].producing_attempt_id == attempt.id
        assert consumer.inputs["authority"].value == "approved"
