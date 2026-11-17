"""Deterministic per-run cursor projection, independent of agent prose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.models.schemas.recipes import RecipeHumanRunner
from backend.services.recipe.validation import outcome_routes

if TYPE_CHECKING:
    from backend.models.schemas.recipes import (
        RecipeAttemptResponse,
        RecipeDefinition,
        RecipeRoute,
        RecipeState,
        RecipeStep,
        RecipeStepInstanceResponse,
    )

_FINISHED = frozenset({"succeeded", "failed", "skipped", "canceled"})


@dataclass(frozen=True)
class CursorDecision:
    step_id: str
    state: RecipeState
    reason: str


def can_retry(step: RecipeStep, attempt: RecipeAttemptResponse) -> bool:
    result = attempt.result
    return (
        result is not None
        and result.outcome == "failed"
        and not getattr(result.evidence, "uncertain", False)
        and "failed" in step.retry.retry_on
        and attempt.number < step.retry.max_attempts
        and not isinstance(step.runner, RecipeHumanRunner)
    )


def completed_route(step: RecipeStep, attempt: RecipeAttemptResponse) -> RecipeRoute | None:
    result = attempt.result
    if result is None or getattr(result.evidence, "uncertain", False):
        return None
    if result.outcome == "failed" and "failed" in step.retry.retry_on:
        return None if can_retry(step, attempt) else step.retry.exhaustion
    return outcome_routes(step).get(result.outcome)


def project_cursor(
    definition: RecipeDefinition,
    instances: dict[str, RecipeStepInstanceResponse],
    attempts: dict[str, RecipeAttemptResponse],
    *,
    canceled: bool = False,
) -> list[CursorDecision]:
    """Project readiness from committed outcomes. Never launch as a side effect."""
    decisions: list[CursorDecision] = []
    definitions = {step.id: step for step in definition.steps}
    incoming: dict[str, set[str]] = {step.id: set() for step in definition.steps}
    fired: dict[str, set[str]] = {step.id: set() for step in definition.steps}
    for step in definition.steps:
        for route in outcome_routes(step).values():
            for target in route.next_steps:
                incoming[target].add(step.id)
        attempt = attempts.get(step.id)
        if attempt is not None:
            selected_route = completed_route(step, attempt)
            if selected_route is not None:
                for target in selected_route.next_steps:
                    fired[target].add(step.id)

    for step in definition.steps:
        instance = instances[step.id]
        if canceled:
            if instance.state not in _FINISHED and instance.state not in {"running", "needs_decision", "interrupted"}:
                decisions.append(CursorDecision(step.id, "canceled", "Run canceled before dispatch."))
            continue
        attempt = attempts.get(step.id)
        if instance.state == "failed" and attempt is not None and can_retry(step, attempt):
            decisions.append(CursorDecision(step.id, "ready", f"Retry {attempt.number + 1}/{step.retry.max_attempts}."))
            continue
        if instance.state != "blocked":
            continue
        activated = step.id in definition.entry_steps or bool(fired[step.id])
        waiting: list[str] = []
        impossible: list[str] = []
        for dependency in step.depends_on:
            dependency_instance = instances[dependency.step_id]
            dependency_attempt = attempts.get(dependency.step_id)
            dependency_result = dependency_attempt.result if dependency_attempt else None
            outcome = dependency_result.outcome if dependency_result else None
            if (
                outcome in dependency.outcomes
                and dependency_instance.state in _FINISHED
                and dependency_result is not None
                and not getattr(dependency_result.evidence, "uncertain", False)
                and not (dependency_attempt and can_retry(definitions[dependency.step_id], dependency_attempt))
            ):
                continue
            if dependency_instance.state in _FINISHED and not (
                dependency_attempt is not None and can_retry(definitions[dependency.step_id], dependency_attempt)
            ):
                impossible.append(dependency.step_id)
            else:
                waiting.append(dependency.step_id)
        if impossible:
            decisions.append(
                CursorDecision(step.id, "skipped", f"Required outcomes unavailable: {', '.join(impossible)}.")
            )
        elif (
            not activated
            and incoming[step.id]
            and all(
                instances[source].state in _FINISHED
                and not (attempts.get(source) and can_retry(definitions[source], attempts[source]))
                for source in incoming[step.id]
            )
        ):
            decisions.append(CursorDecision(step.id, "skipped", "No selected outcome route reaches this step."))
        elif activated and not waiting:
            decisions.append(CursorDecision(step.id, "ready", "Selected route and required outcomes are satisfied."))
        else:
            reason = f"Waiting for: {', '.join(waiting)}." if waiting else "Waiting for an explicit outcome route."
            if instance.reason != reason:
                decisions.append(CursorDecision(step.id, "blocked", reason))
    return decisions


def completed_run_state(
    definition: RecipeDefinition,
    instances: dict[str, RecipeStepInstanceResponse],
    attempts: dict[str, RecipeAttemptResponse],
) -> RecipeState | None:
    if any(instance.state not in _FINISHED for instance in instances.values()):
        return None
    if any(attempts.get(step.id) and can_retry(step, attempts[step.id]) for step in definition.steps):
        return None
    terminals: set[str] = set()
    for step in definition.steps:
        attempt = attempts.get(step.id)
        if attempt:
            route = completed_route(step, attempt)
            if route and route.terminal:
                terminals.add(route.terminal)
            elif instances[step.id].state == "canceled":
                terminals.add("canceled")
    if "failed" in terminals:
        return "failed"
    if "canceled" in terminals:
        return "canceled"
    return "succeeded" if "succeeded" in terminals else "failed"
