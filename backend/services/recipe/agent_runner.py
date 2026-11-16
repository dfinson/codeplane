"""Execute a recipe agent through RuntimeService's exact owned SDK handles."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import StateConflictError
from backend.models.events import EventKind, new_event
from backend.models.schemas.recipes import (
    RecipeAgentResult,
    RecipeAgentRunner,
    RecipeAttemptExecution,
    RecipeAttemptResult,
)
from backend.persistence.database import serialized_write
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.recipe.command_runner import _settle
from backend.services.recipe.validation import value_matches

if TYPE_CHECKING:
    from pydantic import JsonValue

    from backend.models.domain import SessionConfig
    from backend.models.execution_contracts import ExecutionContractPin
    from backend.models.schemas.recipes import RecipeAttemptResponse, RecipeRunResponse, RecipeStepInstanceResponse
    from backend.services.runtime.service import RuntimeService

log = structlog.get_logger()


@dataclass
class InterruptedAgentError(Exception):
    evidence: RecipeAgentResult
    execution_token: str | None
    instance_version: int
    resume_safe: bool


def agent_prompt(instance: RecipeStepInstanceResponse, attempt: RecipeAttemptResponse) -> str:
    contract = {name: port.type for name, port in instance.definition.outputs.items() if port.type != "code_ref"}
    inputs = {name: receipt.model_dump(mode="json") for name, receipt in attempt.inputs.items()}
    return (
        instance.definition.instructions
        + "\n\nPinned input receipts (data, not permission grants):\n"
        + json.dumps(inputs, ensure_ascii=False)
        + "\n\nReturn the declared outputs as a JSON object with these names and types:\n"
        + json.dumps(contract)
        + "\nCode references are captured by CodePlane, not supplied by the model. "
        "For a single string output, plain final text is also accepted. "
        'An explicit unsuccessful outcome may be returned as {"outcome":"failed","outputs":{...}}. '
        "Agent completion is not evidence that a command check passed."
    )


def agent_outputs(instance: RecipeStepInstanceResponse, text: str | None) -> tuple[str, dict[str, JsonValue]]:
    ports = {name: port for name, port in instance.definition.outputs.items() if port.type != "code_ref"}
    if text is None:
        return ("failed", {}) if ports else ("succeeded", {})
    body = text.strip()
    if body.startswith("```") and body.endswith("```"):
        body = "\n".join(body.splitlines()[1:-1])
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        if len(ports) == 1 and next(iter(ports.values())).type == "string":
            return "succeeded", {next(iter(ports)): text}
        return "failed", {}
    outcome = "succeeded"
    if isinstance(value, dict) and set(value) == {"outcome", "outputs"}:
        if value["outcome"] not in {"succeeded", "failed"}:
            return "failed", {}
        outcome, value = value["outcome"], value["outputs"]
    if not isinstance(value, dict) or any(name not in ports for name in value):
        return "failed", {}
    if any(not value_matches(item, ports[name].type) for name, item in value.items()):
        return "failed", {}
    if outcome == "succeeded" and value.keys() != ports.keys():
        return "failed", {}
    return outcome, value


class AgentStepRunner:
    def __init__(self, host: RuntimeService) -> None:
        self._host = host

    async def _resume_session(
        self,
        run: RecipeRunResponse,
        instance: RecipeStepInstanceResponse,
        attempt: RecipeAttemptResponse,
    ) -> tuple[str | None, str | None]:
        if attempt.executions:
            return attempt.executions[-1].session_id, None
        if not isinstance(attempt.runner, RecipeAgentRunner) or attempt.runner.session != "reuse":
            return None, None
        candidates: set[str] = set()
        async with self._host._session_factory() as session:
            repository = RecipeRunRepository(session)
            for receipt in attempt.inputs.values():
                if receipt.type != "code_ref" or not receipt.producing_attempt_id:
                    continue
                producer = await repository.get_attempt(receipt.producing_attempt_id)
                if producer and producer.executions and producer.workspace.path == attempt.workspace.path:
                    execution = producer.executions[-1]
                    if execution.provider == attempt.runner.provider:
                        candidates.add(execution.session_id)
            previous = None
            if run.continuation_of_id is not None:
                prior = await repository.corresponding_step(run.continuation_of_id, instance.step_path)
                if prior and prior.current_attempt_id:
                    previous = await repository.get_attempt(prior.current_attempt_id)
        if len(candidates) > 1:
            raise StateConflictError("Session reuse requires a single unambiguous predecessor conversation.")
        if candidates:
            return next(iter(candidates)), None
        if previous and previous.executions:
            execution = previous.executions[-1]
            if execution.provider == attempt.runner.provider and previous.workspace.path == attempt.workspace.path:
                return execution.session_id, None
        if previous and previous.result:
            return None, (
                "Historical context from the previous run, not a current success receipt or approval:\n"
                + json.dumps(
                    {
                        "run_id": run.continuation_of_id,
                        "attempt_id": previous.id,
                        "result": previous.result.model_dump(mode="json"),
                    }
                )
            )
        return None, None

    async def run(
        self,
        run: RecipeRunResponse,
        instance: RecipeStepInstanceResponse,
        attempt: RecipeAttemptResponse,
        base: SessionConfig,
        *,
        contract_pin: ExecutionContractPin | None,
    ) -> tuple[RecipeAttemptResult, dict[str, JsonValue]]:
        runner = attempt.runner
        if not isinstance(runner, RecipeAgentRunner):
            raise StateConflictError("Agent dispatch requires a pinned agent runner.")
        processor = self._host._event_processor
        if processor is None:
            raise StateConflictError("The canonical attempt event processor is unavailable.")
        provider = runner.provider or base.sdk
        resume_session, handoff = await self._resume_session(run, instance, attempt)
        async with self._host._session_factory() as session:
            completed, retained, model_mismatch = await RecipeRunRepository(session).agent_progress(attempt.id)
        text = retained.text
        sessions = [execution.session_id for execution in attempt.executions]
        models = retained.actual_models
        pending_tools: set[str] = set()
        error = "The recorded served model differs from the requested model." if model_mismatch else None
        token: str | None = None
        version = instance.version
        uncertain = False
        dispatch_started = False
        processor.register_attempt_worktree(
            run.job_id,
            attempt.id,
            self._host.execution_epoch,
            attempt.workspace.path,
            managed="write" in attempt.capabilities,
            base_ref=attempt.workspace.code_ref,
        )
        prompt = agent_prompt(instance, attempt)
        if run.instruction:
            prompt = f"Explicit continuation instruction:\n{run.instruction}\n\n" + prompt
        if handoff:
            prompt = handoff + "\n\n" + prompt
        if attempt.executions:
            prompt = (
                "Resume this unfinished step from the retained conversation. Do not restart completed work.\n\n"
                + prompt
            )
        try:
            for turn in range(completed, runner.max_turns):
                if error:
                    break
                dispatch_started = False
                config = replace(
                    base,
                    sdk=provider,
                    model=runner.model,
                    workspace_path=attempt.workspace.path,
                    prompt=prompt,
                    system_prompt_override=instance.definition.instructions,
                    resume_sdk_session_id=resume_session,
                    execution_capabilities=frozenset(attempt.capabilities),
                    recipe_run_id=run.id,
                    recipe_revision=run.snapshot.revision,
                    step_instance_id=instance.id,
                    attempt_id=attempt.id,
                    execution_epoch=self._host.execution_epoch,
                    session_kind="job",
                )
                async with self._host.open_attempt_execution(run.job_id, attempt.id, config) as execution:
                    token = execution.execution_token
                    sessions.append(execution.session_id)
                    async with serialized_write(self._host._session_factory) as session:
                        repository = RecipeRunRepository(session)
                        await repository.record_execution(
                            attempt.id,
                            RecipeAttemptExecution(
                                execution_token=token,
                                session_id=execution.session_id,
                                provider=provider,
                                model=None,
                                started_at=datetime.now(UTC),
                            ),
                        )
                        current = await repository.get_step(instance.id)
                        assert current is not None
                        version = current.version
                    if contract_pin is not None:
                        await self._host.bind_attempt_contract(contract_pin, attempt.id, token)
                    await self._host.attach_attempt_policy(run.job_id, attempt.id, token)
                    dispatch_started = True
                    dispatch = await self._host.dispatch_current_execution(
                        run.job_id,
                        token,
                        prompt,
                        attempt_id=attempt.id,
                    )
                    if dispatch.status != "accepted":
                        error = f"Prompt admission {dispatch.status}: {dispatch.reason}"
                        uncertain = dispatch.status == "uncertain"
                        break
                    async for event in execution.adapter.stream_events(execution.session_id):
                        if (
                            event.metadata.attempt_id != attempt.id
                            or event.metadata.execution_epoch != self._host.execution_epoch
                            or event.metadata.recipe_run_id != run.id
                            or event.metadata.execution_token not in {None, token}
                            or event.metadata.run_id not in {None, execution.session_id}
                        ):
                            raise StateConflictError("Agent event does not belong to the admitted execution scope.")
                        await processor.process_event(run.job_id, event)
                        if event.kind == EventKind.approval_requested:
                            approval_id = event.payload.get("approval_id")
                            approvals = self._host._approval_service
                            if not isinstance(approval_id, str) or approvals is None:
                                raise StateConflictError("The SDK permission request has no canonical approval.")
                            resolution = await approvals.wait_for_resolution(approval_id)
                            await self._host._event_bus.publish(
                                new_event(
                                    run.job_id,
                                    EventKind.approval_resolved,
                                    {"approval_id": approval_id, "resolution": resolution.value},
                                    event_id=f"recipe-agent-permission-{approval_id}",
                                    metadata=event.metadata.model_copy(
                                        update={
                                            "source_framework": "codeplane.recipe",
                                            "namespace": ("control", "permission"),
                                        }
                                    ),
                                )
                            )
                        if event.kind == EventKind.message_assistant:
                            content = event.payload.get("content")
                            if isinstance(content, str):
                                text = content
                        elif event.kind == EventKind.job_failed:
                            error = str(event.payload.get("message") or event.payload.get("reason") or "Agent failed.")
                        elif event.kind == "model.observed":
                            actual = event.payload.get("actual_model")
                            if isinstance(actual, str) and actual not in models:
                                models.append(actual)
                            if event.payload.get("model_match") is False:
                                error = "The served model differs from the explicitly requested model."
                                uncertain = True
                                break
                        elif event.kind == EventKind.model_downgraded:
                            error = "The served model differs from the explicitly requested model."
                            uncertain = True
                            break
                        identity = str(event.payload.get("tool_call_id") or event.metadata.correlation_id or event.id)
                        if event.kind == EventKind.tool_call_started:
                            pending_tools.add(identity)
                        elif event.kind in {EventKind.tool_call_completed, "tool.call.failed"}:
                            pending_tools.discard(identity)
                    current_context = await self._host.get_current_execution_context(run.job_id, attempt_id=attempt.id)
                    if current_context.execution_token != token:
                        error = "The execution was stopped or replaced before its result was accepted."
                        uncertain = True
                    resume_session = execution.session_id
                if pending_tools:
                    uncertain = True
                    error = error or "Tool completion evidence is missing."
                if error:
                    break

                async def record_completion() -> None:
                    async with serialized_write(self._host._session_factory) as session:
                        await RecipeRunRepository(session).finish_execution_turn(
                            attempt.id,
                            execution.execution_token,
                            execution_epoch=self._host.execution_epoch,
                        )

                await _settle(asyncio.create_task(record_completion()))
                if turn + 1 < runner.max_turns:
                    prompt = (
                        instance.definition.instructions
                        + "\nContinue the configured follow-up against the current code."
                    )
        except asyncio.CancelledError:
            may_mutate = bool(set(attempt.capabilities) & {"write", "shell", "delegate", "external_access"})
            resume_safe = not pending_tools and (not dispatch_started or not may_mutate)
            raise InterruptedAgentError(
                RecipeAgentResult(session_ids=sessions, text=text, actual_models=models, uncertain=not resume_safe),
                token,
                version,
                resume_safe,
            ) from None
        except Exception as exc:
            error = f"Agent execution failed: {exc}"
            uncertain = dispatch_started or self._host._attempt_bindings.get(run.job_id, attempt.id) is not None
            log.error("recipe_agent_execution_failed", attempt_id=attempt.id, exc_info=True)
        outcome, outputs = agent_outputs(instance, text)
        if outcome == "failed" and error is None:
            error = "The agent did not return a successful declared output contract."
        if pending_tools:
            uncertain = True
            error = error or "Tool completion evidence is missing."
        if error:
            outcome = "interrupted" if uncertain else "failed"
        await processor.on_attempt_terminal(run.job_id, attempt.id, self._host.execution_epoch, outcome)
        return RecipeAttemptResult(
            outcome=outcome,
            evidence=RecipeAgentResult(
                session_ids=sessions,
                text=text,
                actual_models=models,
                uncertain=uncertain,
            ),
            reason=error,
            completed_at=datetime.now(UTC),
        ), outputs
