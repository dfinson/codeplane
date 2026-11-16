"""Agent runner contracts using an explicit adapter test double, not live inference."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import CPLConfig
from backend.models.domain import SessionConfig
from backend.models.events import EventMetadata, new_event
from backend.models.schemas.recipes import RecipeCodeReference, RecipeDefinition, RecipeOutput
from backend.persistence.event_repo import EventRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.action_policy.classifier import RepoPolicy
from backend.services.events.event_bus import EventBus
from backend.services.recipe.agent_runner import AgentStepRunner, InterruptedAgentError
from backend.services.runtime import RuntimeService
from backend.tests.unit.test_recipe_command_runner import configure_test_contract
from backend.tests.unit.test_recipe_definitions import agent
from backend.tests.unit.test_recipe_runs import create_run, ready_attempt
from backend.tests.unit.test_recipe_runs import sessions as sessions


async def agent_context(sessions, *, reuse=False, max_turns=1, output_type="string"):
    work = agent("work")
    work.runner.session = "reuse" if reuse else "new"
    work.runner.max_turns = max_turns
    if output_type is None:
        work.outputs = {}
    elif output_type == "code_ref":
        work.outputs = {"code": RecipeOutput(type="code_ref")}
    run = await create_run(sessions, RecipeDefinition(name="Agent contract", entry_steps=["work"], steps=[work]))
    async with sessions() as session:
        attempt = await ready_attempt(session, run, "work")
        instance = await RecipeRunRepository(session).get_step(attempt.step_instance_id)
    adapter = MagicMock()
    adapter.attach_attempt_policy = MagicMock()
    adapter.abort_session = AsyncMock()
    configurations = []

    async def create(config):
        configurations.append(config)
        return config.resume_sdk_session_id or f"test-session-{len(configurations)}"

    async def send(session_id, prompt):
        async with sessions() as session:
            persisted = await RecipeRunRepository(session).get_attempt(configurations[-1].attempt_id)
            assert persisted.executions[-1].session_id == session_id
        assert configurations[-1].defer_start
        assert prompt

    adapter.create_session = AsyncMock(side_effect=create)
    adapter.send_message = AsyncMock(side_effect=send)
    registry = MagicMock()
    registry.get_adapter.return_value = adapter
    processor = MagicMock()

    async def persist(job_id, event):
        async with sessions() as session:
            await EventRepository(session).append(event)
            await session.commit()

    processor.process_event = AsyncMock(side_effect=persist)
    processor.on_attempt_terminal = AsyncMock()

    def runtime():
        host = RuntimeService(
            session_factory=sessions,
            event_bus=EventBus(),
            adapter_registry=registry,
            config=CPLConfig(),
            event_processor=processor,
        )
        host._policy_routers[run.job_id] = MagicMock()
        host._repo_policies[run.job_id] = RepoPolicy()
        configure_test_contract(host)
        return host

    def emit(kind, payload):
        config = configurations[-1]
        return new_event(
            config.job_id,
            kind,
            payload,
            metadata=EventMetadata(
                source_framework="copilot",
                recipe_run_id=config.recipe_run_id,
                step_instance_id=config.step_instance_id,
                attempt_id=config.attempt_id,
                execution_epoch=config.execution_epoch,
                run_id=config.resume_sdk_session_id or f"test-session-{len(configurations)}",
            ),
        )

    return run, instance, attempt, adapter, configurations, runtime, emit


@pytest.mark.parametrize(
    "messages,expected_turns,expected_outcome,expected_outputs",
    [
        (['{"result":"done"}', '{"result":"unwanted extra work"}'], 1, "succeeded", {"result": "done"}),
        (
            ['{"outcome":"failed","outputs":{"result":"negative"}}', '{"result":"overwritten failure"}'],
            1,
            "failed",
            {"result": "negative"},
        ),
        (['{"missing":"incomplete"}', '{"result":"done"}'], 2, "succeeded", {"result": "done"}),
        (['{"missing":"incomplete"}'], 3, "failed", {}),
    ],
)
async def test_turn_ceiling_stops_at_a_conclusive_contract(
    sessions, messages, expected_turns, expected_outcome, expected_outputs
):
    run, instance, attempt, adapter, configurations, make_runtime, emit = await agent_context(sessions, max_turns=3)

    async def stream(_session_id):
        index = min(adapter.send_message.await_count - 1, len(messages) - 1)
        yield emit("message.assistant", {"content": messages[index]})

    adapter.stream_events = stream
    host = make_runtime()
    pin = await host.prepare_execution_contract(run.job_id, run.id, recovery=False)
    result, outputs = await AgentStepRunner(host).run(
        run,
        instance,
        attempt,
        SessionConfig(workspace_path="/repo/snapshot", prompt="Job request"),
        contract_pin=pin,
    )
    assert len(configurations) == expected_turns
    assert result.outcome == expected_outcome and outputs == expected_outputs
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        assert (await repository.agent_progress(attempt.id))[0] == expected_turns
        assert len(await repository.list_attempts(instance.id)) == 1


@pytest.mark.parametrize("output_type", [None, "code_ref"])
@pytest.mark.parametrize(
    "message,outcome",
    [
        ("Implemented the requested change.", "succeeded"),
        ('{"outcome":"failed","outputs":{}}', "failed"),
    ],
)
async def test_no_model_output_ports_accept_summary_but_preserve_explicit_failure(
    sessions, output_type, message, outcome
):
    run, instance, attempt, adapter, configurations, make_runtime, emit = await agent_context(
        sessions, max_turns=3, output_type=output_type
    )

    async def stream(_session_id):
        yield emit("message.assistant", {"content": message})

    adapter.stream_events = stream
    host = make_runtime()
    pin = await host.prepare_execution_contract(run.job_id, run.id, recovery=False)
    result, outputs = await AgentStepRunner(host).run(
        run,
        instance,
        attempt,
        SessionConfig(workspace_path="/repo/snapshot", prompt="Job request"),
        contract_pin=pin,
    )
    assert result.outcome == outcome and outputs == {}
    assert len(configurations) == 1


async def test_agent_persists_real_handle_before_send_and_keeps_reported_model(sessions):
    run, instance, attempt, adapter, configurations, make_runtime, emit = await agent_context(sessions)

    async def stream(_session_id):
        yield emit("model.observed", {"requested_model": None, "actual_model": "reported-model", "model_match": None})
        yield emit("tool.call.started", {"tool_call_id": "call-1", "tool_name": "read"})
        yield emit("tool.call.failed", {"tool_call_id": "call-1", "tool_name": "read", "success": False})
        yield emit("message.assistant", {"content": '{"result":"typed result"}'})

    adapter.stream_events = stream
    host = make_runtime()
    pin = await host.prepare_execution_contract(run.job_id, run.id, recovery=False)
    result, outputs = await AgentStepRunner(host).run(
        run,
        instance,
        attempt,
        SessionConfig(workspace_path="/repo/snapshot", prompt="Job request"),
        contract_pin=pin,
    )
    assert result.outcome == "succeeded" and outputs == {"result": "typed result"}
    assert result.evidence.actual_models == ["reported-model"]
    assert configurations[0].execution_capabilities == frozenset({"read"})
    assert host._attempt_bindings.get(run.job_id, attempt.id) is None
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        retained = await repository.get_attempt(attempt.id)
        completed, progress, mismatch = await repository.agent_progress(attempt.id)
    assert retained.executions[0].model is None
    assert completed == 1 and progress.actual_models == ["reported-model"] and not mismatch


@pytest.mark.parametrize("case", ["creation", "delivery_unknown", "bad_outputs"])
async def test_failed_or_unconfirmed_agent_work_is_not_success(sessions, case):
    run, instance, attempt, adapter, _, make_runtime, emit = await agent_context(sessions)

    async def stream(_session_id):
        yield emit("message.assistant", {"content": '{"unexpected":"wrong port"}'})

    adapter.stream_events = stream
    if case == "creation":
        adapter.create_session.side_effect = RuntimeError("Provider unavailable")
    elif case == "delivery_unknown":
        adapter.send_message.side_effect = ConnectionError("Response lost")
    host = make_runtime()
    pin = await host.prepare_execution_contract(run.job_id, run.id, recovery=False)
    result, outputs = await AgentStepRunner(host).run(
        run,
        instance,
        attempt,
        SessionConfig(workspace_path="/repo/snapshot", prompt="Job request"),
        contract_pin=pin,
    )
    assert result.outcome == ("interrupted" if case == "delivery_unknown" else "failed")
    assert not outputs
    if case == "delivery_unknown":
        assert result.evidence.uncertain
    if case == "creation":
        assert result.evidence.session_ids == []


async def test_safe_recovery_reuses_unfinished_attempt_but_gets_fresh_execution_fence(sessions):
    run, instance, attempt, adapter, configurations, make_runtime, emit = await agent_context(sessions)
    waiting = asyncio.Event()

    async def interrupted_stream(_session_id):
        yield emit("message.assistant", {"content": "partial progress"})
        waiting.set()
        await asyncio.Event().wait()

    adapter.stream_events = interrupted_stream
    original = make_runtime()
    pin = await original.prepare_execution_contract(run.job_id, run.id, recovery=False)
    task = asyncio.create_task(
        AgentStepRunner(original).run(
            run,
            instance,
            attempt,
            SessionConfig(workspace_path="/repo/snapshot", prompt="Job request"),
            contract_pin=pin,
        )
    )
    await asyncio.wait_for(waiting.wait(), timeout=5)
    task.cancel()
    with pytest.raises(InterruptedAgentError) as caught:
        await task
    stopped = caught.value
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        await repository.interrupt_attempt(
            attempt.id,
            "Confirmed shutdown",
            stop_confirmed=True,
            resume_safe=stopped.resume_safe,
            execution_epoch=original.execution_epoch,
            expected_execution=stopped.execution_token,
            expected_instance_version=stopped.instance_version,
        )
        await session.commit()
        interrupted = await repository.get_step(instance.id)
        resumed_attempt = await repository.resume_interrupted(interrupted)
        resumed_instance = await repository.get_step(instance.id)
        await session.commit()

    async def completed_stream(_session_id):
        yield emit("message.assistant", {"content": '{"result":"finished after recovery"}'})

    adapter.stream_events = completed_stream
    restarted = make_runtime()
    recovered_pin = await restarted.prepare_execution_contract(run.job_id, run.id, recovery=True)
    result, outputs = await AgentStepRunner(restarted).run(
        run,
        resumed_instance,
        resumed_attempt,
        SessionConfig(workspace_path="/repo/snapshot", prompt="Resume"),
        contract_pin=recovered_pin,
    )
    assert result.outcome == "succeeded" and outputs == {"result": "finished after recovery"}
    assert configurations[-1].resume_sdk_session_id == "test-session-1"
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        retained = await repository.get_attempt(attempt.id)
        assert retained.number == 1 and len(await repository.list_attempts(instance.id)) == 1
        assert len(retained.executions) == 2
        assert retained.executions[0].execution_token != retained.executions[1].execution_token
        assert (await repository.agent_progress(attempt.id))[0] == 1


async def test_new_run_reuses_compatible_native_history_without_inheriting_attempts(sessions):
    run, instance, attempt, adapter, configurations, make_runtime, emit = await agent_context(sessions, reuse=True)

    async def stream(_session_id):
        yield emit("message.assistant", {"content": '{"result":"current execution result"}'})

    adapter.stream_events = stream
    host = make_runtime()
    first_pin = await host.prepare_execution_contract(run.job_id, run.id, recovery=False)
    result, outputs = await AgentStepRunner(host).run(
        run,
        instance,
        attempt,
        SessionConfig(workspace_path="/repo/snapshot", prompt="Job request"),
        contract_pin=first_pin,
    )
    async with sessions() as session:
        repository = RecipeRunRepository(session)
        await repository.finish_attempt(attempt.id, result, outputs)
        previous = await repository.resolve_run(run.id, "succeeded")
        following = await repository.create(
            run.job_id,
            run.snapshot,
            run.inputs,
            start_request_key="native-continuation",
            continuation_of_id=previous.id,
            instruction="New explicit instruction",
            code_ref=RecipeCodeReference(workspace="/repo/snapshot", commit="a" * 40),
        )
        await session.commit()
        next_attempt = await ready_attempt(session, following, "work")
        next_instance = await repository.get_step(next_attempt.step_instance_id)
    next_pin = await host.prepare_execution_contract(run.job_id, following.id, recovery=False)
    next_result, next_outputs = await AgentStepRunner(host).run(
        following,
        next_instance,
        next_attempt,
        SessionConfig(workspace_path="/repo/snapshot", prompt="Continuation"),
        contract_pin=next_pin,
    )
    assert next_result.outcome == "succeeded" and next_outputs == outputs
    assert configurations[-1].resume_sdk_session_id == "test-session-1"
    assert "New explicit instruction" in configurations[-1].prompt
    assert next_attempt.id != attempt.id and next_instance.id != instance.id
    assert next_attempt.executions == [] and next_attempt.result is None
    assert next_pin.admission_id != first_pin.admission_id
    assert next_pin.budget_id == first_pin.budget_id
