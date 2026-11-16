"""Published compatibility graphs for the existing configured Job behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.config import DEFAULT_SELF_REVIEW_PROMPT, DEFAULT_VERIFY_PROMPT
from backend.models.schemas.recipes import (
    RecipeAgentRunner,
    RecipeDefinition,
    RecipeDependency,
    RecipeHumanRunner,
    RecipeInput,
    RecipeOutput,
    RecipeParameter,
    RecipeParameterBinding,
    RecipeRoute,
    RecipeStep,
    RecipeValueType,
    RecipeWorkspacePolicy,
)
from backend.services.adapters.integration_policy import IntegrationPolicy

if TYPE_CHECKING:
    from backend.config import CPLConfig


def compatibility_definition(config: CPLConfig, *, plan_first: bool = False) -> RecipeDefinition:
    requested = RecipeInput(type="string", binding=RecipeParameterBinding(parameter="request"))
    context = RecipeStep(
        id="context",
        title="Context brief",
        instructions=(
            "Read the relevant code and instructions. Return a concise, source-backed brief. "
            "Do not edit files or run commands."
        ),
        runner=RecipeAgentRunner(provider=IntegrationPolicy(config).default_sdk, model=config.runtime.utility_model),
        capabilities=["read"],
        workspace=RecipeWorkspacePolicy(mode="snapshot"),
        inputs={"request": requested},
        outputs={"brief": RecipeOutput(type="string")},
        routes={},
    )
    steps: list[RecipeStep] = [context]

    def output(step: str, name: str, *, kind: RecipeValueType = "string", degraded: str | None = None) -> RecipeInput:
        return RecipeInput.model_validate(
            {
                "type": kind,
                "binding": {"source": "output", "step_id": step, "output": name},
                "optional": degraded is not None,
                "degraded_reason": degraded,
            }
        )

    brief = output(
        "context",
        "brief",
        degraded="Context failed. Continue from the Job request without claiming a successful brief.",
    )

    def execution_chain(suffix: str, plan_id: str | None = None, gate_id: str | None = None) -> str:
        implement_id = f"implement{suffix}"
        implementation = RecipeStep(
            id=implement_id,
            title="Implement",
            instructions=(
                "Implement the Job request using the received context and, when present, the exact approved plan. "
                "Return a concise summary of the work."
            ),
            runner=RecipeAgentRunner(),
            capabilities=["read", "write", "shell", "delegate", "external_access"],
            workspace=RecipeWorkspacePolicy(mode="mutable"),
            inputs={"request": requested, "brief": brief},
            outputs={"summary": RecipeOutput(type="string"), "code": RecipeOutput(type="code_ref")},
            depends_on=[RecipeDependency(step_id="context", outcomes=["succeeded", "failed"])],
            routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="failed")},
        )
        if plan_id and gate_id:
            implementation.inputs["plan"] = output(plan_id, "plan")
            implementation.inputs["code"] = output(plan_id, "code", kind="code_ref")
            implementation.workspace.source_input = "code"
            implementation.depends_on.extend(
                [
                    RecipeDependency(step_id=plan_id),
                    RecipeDependency(step_id=gate_id, outcomes=["approved"]),
                ]
            )
        steps.append(implementation)
        predecessor = implementation
        phases = []
        if config.verification.verify:
            phases.append(
                (
                    "verify",
                    "Agent verification",
                    config.verification.verify_prompt or DEFAULT_VERIFY_PROMPT,
                    config.verification.max_turns,
                )
            )
        if config.verification.self_review:
            phases.append(
                (
                    "self_review",
                    "Agent self-review",
                    config.verification.self_review_prompt or DEFAULT_SELF_REVIEW_PROMPT,
                    1,
                )
            )
        for name, title, instructions, turns in phases:
            step_id = f"{name}{suffix}"
            phase = RecipeStep(
                id=step_id,
                title=title,
                instructions=instructions,
                runner=RecipeAgentRunner(session="reuse", max_turns=turns),
                capabilities=["read", "write", "shell", "delegate", "external_access"],
                workspace=RecipeWorkspacePolicy(
                    mode="mutable", source_input="code" if predecessor is implementation else None
                ),
                inputs={
                    "code": output(
                        predecessor.id,
                        "code",
                        kind="code_ref",
                        degraded=None
                        if predecessor is implementation
                        else "Retain known failed agent-turn evidence; this is not a passing command check.",
                    )
                },
                outputs={"report": RecipeOutput(type="string"), "code": RecipeOutput(type="code_ref")},
                depends_on=[
                    RecipeDependency(
                        step_id=predecessor.id,
                        outcomes=["succeeded", "failed"] if predecessor is not implementation else ["succeeded"],
                    )
                ],
                routes={"succeeded": RecipeRoute(terminal="succeeded"), "failed": RecipeRoute(terminal="succeeded")},
            )
            predecessor.routes["succeeded"] = RecipeRoute(next_steps=[step_id])
            if predecessor is not implementation:
                predecessor.routes["failed"] = RecipeRoute(next_steps=[step_id])
            steps.append(phase)
            predecessor = phase
        return implement_id

    if not plan_first:
        first = execution_chain("")
        context.routes = {"succeeded": RecipeRoute(next_steps=[first]), "failed": RecipeRoute(next_steps=[first])}
    else:
        context.routes = {"succeeded": RecipeRoute(next_steps=["plan_1"]), "failed": RecipeRoute(next_steps=["plan_1"])}
        for iteration in range(1, 7):
            plan_id, gate_id = f"plan_{iteration}", f"approve_plan_{iteration}"
            planner = RecipeStep(
                id=plan_id,
                title="Plan" if iteration == 1 else f"Revise plan {iteration - 1}",
                instructions=(
                    "Read the code and propose a concrete numbered execution plan. Include files and verification. "
                    "Do not implement. Incorporate any exact previous plan and operator feedback supplied as inputs."
                ),
                runner=RecipeAgentRunner(),
                capabilities=["read"],
                workspace=RecipeWorkspacePolicy(mode="snapshot"),
                inputs={"request": requested, "brief": brief},
                outputs={"plan": RecipeOutput(type="string"), "code": RecipeOutput(type="code_ref")},
                depends_on=[RecipeDependency(step_id="context", outcomes=["succeeded", "failed"])],
                routes={"succeeded": RecipeRoute(next_steps=[gate_id]), "failed": RecipeRoute(terminal="failed")},
            )
            if iteration > 1:
                planner.inputs["previous_plan"] = output(f"plan_{iteration - 1}", "plan")
                planner.inputs["feedback"] = output(f"approve_plan_{iteration - 1}", "feedback")
                planner.depends_on.extend(
                    [
                        RecipeDependency(step_id=f"plan_{iteration - 1}"),
                        RecipeDependency(step_id=f"approve_plan_{iteration - 1}", outcomes=["rejected"]),
                    ]
                )
            steps.append(planner)
            first = execution_chain(f"_{iteration}", plan_id, gate_id)
            steps.append(
                RecipeStep(
                    id=gate_id,
                    title="Approve plan",
                    runner=RecipeHumanRunner(
                        proposed_action="Implement this exact proposed plan against its pinned code reference."
                    ),
                    capabilities=[],
                    workspace=RecipeWorkspacePolicy(mode="none"),
                    inputs={"plan": output(plan_id, "plan"), "code": output(plan_id, "code", kind="code_ref")},
                    outputs={"feedback": RecipeOutput(type="string")},
                    depends_on=[RecipeDependency(step_id=plan_id)],
                    routes={
                        "approved": RecipeRoute(next_steps=[first]),
                        "rejected": RecipeRoute(next_steps=[f"plan_{iteration + 1}"])
                        if iteration < 6
                        else RecipeRoute(terminal="failed"),
                    },
                )
            )
    return RecipeDefinition(
        name="Standard implementation",
        kind="recipe",
        description=(
            "Configured Scout, implementation and optional agent verification/self-review. "
            "Agent-turn completion is not a passing test."
        )
        + (" Plan First includes explicit approval and at most five replans." if plan_first else ""),
        parameters={"request": RecipeParameter(type="string", description="The Job request.")},
        entry_steps=["context"],
        steps=steps,
    )
