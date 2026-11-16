"""Side-effect-free validation of bounded recipe graphs and pinned blocks."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import TYPE_CHECKING

from backend.models.schemas.recipes import (
    RecipeAgentRunner,
    RecipeBlockRunner,
    RecipeCommandRunner,
    RecipeHumanRunner,
    RecipeLiteralBinding,
    RecipeOutputBinding,
    RecipeParameterBinding,
    RecipeValidationIssue,
    RecipeValidationResult,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from backend.models.schemas.recipes import (
        RecipeDefinition,
        RecipeInput,
        RecipePinnedBlock,
        RecipeRoute,
        RecipeStep,
        RecipeValueType,
    )


def content_checksum(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def value_matches(value: JsonValue, expected: RecipeValueType) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return type(value) in (int, float)
    if expected == "boolean":
        return type(value) is bool
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if not isinstance(value, dict):
        return False
    workspace = value.get("workspace")
    commit = value.get("commit")
    return (
        isinstance(workspace, str)
        and bool(workspace)
        and isinstance(commit, str)
        and re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", commit) is not None
    )


def outcome_routes(step: RecipeStep) -> dict[str, RecipeRoute]:
    routes = dict(step.routes)
    if "failed" in step.retry.retry_on:
        routes["failed"] = step.retry.exhaustion
    return routes


def validate_definition(
    definition: RecipeDefinition,
    blocks: dict[str, RecipePinnedBlock] | None = None,
) -> RecipeValidationResult:
    issues: list[RecipeValidationIssue] = []
    pinned = blocks or {}

    def error(path: str, code: str, message: str) -> None:
        issues.append(RecipeValidationIssue(path=path, code=code, message=message))

    steps = {step.id: step for step in definition.steps}
    if len(steps) != len(definition.steps):
        error("steps", "duplicate_step", "Each step needs a distinct stable ID.")
    if not steps or not definition.entry_steps:
        error("entrySteps", "missing_entry", "Choose at least one entry step in a nonempty recipe.")
    for name, parameter in definition.parameters.items():
        if parameter.default is not None and not value_matches(parameter.default, parameter.type):
            error(f"parameters.{name}.default", "type_mismatch", f"Default must be {parameter.type}.")
        if not parameter.required and parameter.default is None:
            error(f"parameters.{name}", "missing_default", "Optional parameters need a typed default.")

    edges: dict[str, set[str]] = {key: set() for key in steps}
    route_edges: dict[str, set[str]] = {key: set() for key in steps}
    predecessors: dict[str, set[str]] = {key: set() for key in steps}

    def connect(source: str, target: str, path: str, *, route: bool = False) -> None:
        if target not in steps:
            error(path, "unknown_step", f"Step '{target}' does not exist.")
            return
        edges[source].add(target)
        predecessors[target].add(source)
        if route:
            route_edges[source].add(target)

    def check_route(step: RecipeStep, route: RecipeRoute, path: str, *, active: bool = True) -> None:
        if bool(route.next_steps) == bool(route.terminal):
            error(path, "invalid_route", "Choose successor steps or a terminal outcome, not both or neither.")
        if len(route.next_steps) != len(set(route.next_steps)):
            error(path, "duplicate_target", "Route targets must be unique.")
        for target in route.next_steps:
            if active:
                connect(step.id, target, path, route=True)
            elif target not in steps:
                error(path, "unknown_step", f"Step '{target}' does not exist.")

    for entry in definition.entry_steps:
        if entry not in steps:
            error("entrySteps", "unknown_step", f"Entry step '{entry}' does not exist.")
    if len(definition.entry_steps) != len(set(definition.entry_steps)):
        error("entrySteps", "duplicate_entry", "Entry steps must be unique.")

    for step in definition.steps:
        path = f"steps.{step.id}"
        runner = step.runner
        outcomes = set(runner.decisions) if isinstance(runner, RecipeHumanRunner) else {"succeeded", "failed"}
        if not outcomes <= set(step.routes):
            error(
                f"{path}.routes",
                "missing_route",
                f"Declare routes for: {', '.join(sorted(outcomes - step.routes.keys()))}.",
            )
        if set(step.routes) - outcomes:
            error(f"{path}.routes", "unknown_outcome", "Routes must use runner outcomes, not titles or prose.")
        for outcome, route in step.routes.items():
            check_route(
                step,
                route,
                f"{path}.routes.{outcome}",
                active=outcome != "failed" or "failed" not in step.retry.retry_on,
            )
        check_route(
            step,
            step.retry.exhaustion,
            f"{path}.retry.exhaustion",
            active="failed" in step.retry.retry_on,
        )
        if step.retry.max_attempts > 1 and not step.retry.retry_on:
            error(f"{path}.retry", "missing_retry_condition", "Bounded retries need explicit retry conditions.")
        if isinstance(runner, RecipeHumanRunner):
            if step.retry.max_attempts != 1:
                error(f"{path}.retry", "human_retry", "A human gate cannot be retried or bypassed automatically.")
            if not runner.proposed_action.strip() or not {"approved", "rejected"} <= outcomes:
                error(
                    f"{path}.runner",
                    "invalid_gate",
                    "Name the proposed action and include approved/rejected decisions.",
                )
            if len(outcomes) != len(runner.decisions):
                error(f"{path}.runner.decisions", "duplicate_decision", "Decisions must be unique.")
            if step.capabilities or step.workspace.mode != "none":
                error(path, "human_authority", "Human decisions do not execute tools or own an agent workspace.")
        if isinstance(runner, RecipeAgentRunner) and not step.instructions.strip():
            error(f"{path}.instructions", "missing_instructions", "Agent steps need explicit instructions.")
        if isinstance(runner, RecipeCommandRunner):
            if (
                not runner.argv
                or not isinstance(runner.argv[0], str)
                or not runner.argv[0]
                or any("\x00" in arg for arg in runner.argv if isinstance(arg, str))
            ):
                error(f"{path}.runner.argv", "missing_command", "Supply an executable and exact argument list.")
            for index, argument in enumerate(runner.argv):
                if isinstance(argument, str):
                    continue
                argument_input = step.inputs.get(argument.input)
                if argument_input is None or argument_input.optional:
                    error(
                        f"{path}.runner.argv.{index}",
                        "missing_command_input",
                        "Command arguments must bind a required declared input.",
                    )
                elif (argument.field is not None and argument_input.type != "code_ref") or (
                    argument.field is None and argument_input.type in {"code_ref", "object", "array"}
                ):
                    error(
                        f"{path}.runner.argv.{index}",
                        "invalid_command_input",
                        "Use a scalar input or select the workspace/commit field of a code reference.",
                    )
            if "shell" not in step.capabilities:
                error(f"{path}.capabilities", "missing_capability", "Command steps require the shell capability.")
        if step.workspace.source_input is not None:
            workspace_input = step.inputs.get(step.workspace.source_input)
            if workspace_input is None or workspace_input.type != "code_ref" or workspace_input.optional:
                error(
                    f"{path}.workspace.sourceInput",
                    "invalid_workspace_source",
                    "Bind the workspace source to a required code_ref input.",
                )
        if "write" in step.capabilities and step.workspace.mode not in {"mutable", "isolated"}:
            error(f"{path}.workspace", "write_workspace", "Writers need a mutable or isolated workspace.")
        if step.workspace.mode == "none" and step.capabilities:
            error(f"{path}.workspace", "workspace_required", "Tool execution needs an explicit workspace policy.")
        if len(step.capabilities) != len(set(step.capabilities)):
            error(f"{path}.capabilities", "duplicate_capability", "Capabilities must be unique.")
        for dep in step.depends_on:
            if dep.step_id not in steps:
                error(f"{path}.dependsOn", "unknown_step", f"Dependency '{dep.step_id}' does not exist.")
                continue
            connect(dep.step_id, step.id, f"{path}.dependsOn")
            dependency_step = steps[dep.step_id]
            if not set(dep.outcomes) <= set(dependency_step.routes):
                error(
                    f"{path}.dependsOn", "unknown_outcome", f"Dependency '{dep.step_id}' cannot produce those outcomes."
                )
        if len(step.depends_on) != len({dep.step_id for dep in step.depends_on}):
            error(f"{path}.dependsOn", "duplicate_dependency", "Dependencies must be unique.")

    incoming = {key: len(value) for key, value in predecessors.items()}
    queue = deque(key for key, count in incoming.items() if count == 0)
    ancestors: dict[str, set[str]] = {key: set() for key in steps}
    visited: list[str] = []
    while queue:
        source = queue.popleft()
        visited.append(source)
        for target in edges[source]:
            ancestors[target].update(ancestors[source] | {source})
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    if len(visited) != len(steps):
        error("steps", "cycle", "Dependencies and outcome routes must be acyclic. Use a bounded retry policy.")

    # Mandatory branch conditions prove exclusion; the runtime lease remains
    # authoritative even when two conditional paths cannot be selected together.
    conditions: dict[str, dict[str, set[str]]] = {key: {} for key in steps}
    for key in visited:
        candidates: list[dict[str, set[str]]] = []
        if key in definition.entry_steps:
            candidates.append({})
        for source_id, source_step in steps.items():
            selected = {outcome for outcome, route in outcome_routes(source_step).items() if key in route.next_steps}
            if selected:
                candidate = {name: set(values) for name, values in conditions[source_id].items()}
                candidate[source_id] = selected
                candidates.append(candidate)
        if candidates:
            common = set.intersection(*(set(candidate) for candidate in candidates))
            conditions[key] = {name: set.union(*(candidate[name] for candidate in candidates)) for name in common}
        for dependency in steps[key].depends_on:
            if dependency.step_id not in conditions:
                continue
            required = {**conditions[dependency.step_id], dependency.step_id: set(dependency.outcomes)}
            for source_id, allowed in required.items():
                conditions[key][source_id] = conditions[key].get(source_id, set(allowed)) & allowed
                if not conditions[key][source_id]:
                    error(f"steps.{key}", "impossible_join", "Required branch decisions are mutually exclusive.")

    reachable: set[str] = set()
    queue = deque(key for key in definition.entry_steps if key in steps)
    while queue:
        source = queue.popleft()
        if source in reachable:
            continue
        reachable.add(source)
        queue.extend(route_edges[source] - reachable)
    for key in steps.keys() - reachable:
        error(f"steps.{key}", "unreachable", "Add an explicit outcome route from an entry step.")
    for entry in set(definition.entry_steps) & steps.keys():
        if predecessors[entry]:
            error(f"steps.{entry}", "blocked_entry", "Entry steps cannot depend on or be routed from another step.")

    def check_input(name: str, port: RecipeInput, step: RecipeStep) -> None:
        path = f"steps.{step.id}.inputs.{name}"
        binding = port.binding
        if port.optional and not (port.degraded_reason or "").strip():
            error(path, "missing_degraded_reason", "Optional inputs must explain the permitted degraded execution.")
        if binding is None:
            error(path, "missing_binding", "Bind this input to a parameter, exact output, or literal.")
        elif isinstance(binding, RecipeLiteralBinding):
            if not value_matches(binding.value, port.type):
                error(path, "type_mismatch", f"Literal must be {port.type}.")
        elif isinstance(binding, RecipeParameterBinding):
            parameter = definition.parameters.get(binding.parameter)
            if parameter is None:
                error(path, "unknown_parameter", f"Parameter '{binding.parameter}' does not exist.")
            elif parameter.type != port.type:
                error(path, "type_mismatch", f"Parameter is {parameter.type}, input requires {port.type}.")
        else:
            source = steps.get(binding.step_id)
            output = source.outputs.get(binding.output) if source else None
            if output is None:
                error(path, "unknown_output", f"Output '{binding.step_id}.{binding.output}' is not declared.")
            elif output.type != port.type:
                error(path, "type_mismatch", f"Output is {output.type}, input requires {port.type}.")
            if binding.step_id not in ancestors[step.id]:
                error(path, "unordered_input", "The producing step must precede this consumer.")
            producing_outcomes = (
                set(source.runner.decisions)
                if source is not None and isinstance(source.runner, RecipeHumanRunner)
                else {"succeeded"}
            )
            if not port.optional and not any(
                dep.step_id == binding.step_id and set(dep.outcomes) <= producing_outcomes for dep in step.depends_on
            ):
                error(
                    path,
                    "unprotected_input",
                    "Required outputs need a successful producer or explicit human decision dependency.",
                )

    for step in definition.steps:
        for name, port in step.inputs.items():
            check_input(name, port, step)
        if isinstance(step.runner, RecipeBlockRunner):
            block = pinned.get(step.runner.revision_id)
            if block is None:
                error(
                    f"steps.{step.id}.runner.revisionId",
                    "missing_revision",
                    "Select an existing published block revision.",
                )
                continue
            if block.definition.kind != "block":
                error(f"steps.{step.id}.runner", "not_block", "Reusable references must name a published block.")
            for name, parameter in block.definition.parameters.items():
                block_input = step.inputs.get(name)
                if parameter.required and block_input is None:
                    error(f"steps.{step.id}.inputs.{name}", "missing_binding", "Bind the required block parameter.")
                elif block_input is not None and (
                    block_input.type != parameter.type or (parameter.required and block_input.optional)
                ):
                    error(
                        f"steps.{step.id}.inputs.{name}",
                        "type_mismatch",
                        "Block parameter type/requiredness does not match.",
                    )
            if step.inputs.keys() - block.definition.parameters.keys():
                error(
                    f"steps.{step.id}.inputs", "unknown_parameter", "Remove inputs not declared by this block revision."
                )
            expected_outputs = {key: port.type for key, port in block.definition.outputs.items()}
            if {key: port.type for key, port in step.outputs.items()} != expected_outputs:
                error(
                    f"steps.{step.id}.outputs", "block_outputs", "Outputs must match the pinned block's exported ports."
                )
            nested_capabilities = {cap for child in block.definition.steps for cap in child.capabilities}
            if not nested_capabilities <= set(step.capabilities):
                error(
                    f"steps.{step.id}.capabilities",
                    "block_capabilities",
                    "Declare every capability required by the block.",
                )

    for name, export in definition.outputs.items():
        export_step = steps.get(export.binding.step_id)
        output = export_step.outputs.get(export.binding.output) if export_step else None
        if output is None or output.type != export.type:
            error(f"outputs.{name}", "invalid_export", "Bind an export to a declared output of the same type.")

    writers = [step for step in definition.steps if "write" in step.capabilities]
    for index, left in enumerate(writers):
        for right in writers[index + 1 :]:
            if left.id in ancestors[right.id] or right.id in ancestors[left.id]:
                continue
            if any(
                not (conditions[left.id][source_id] & conditions[right.id][source_id])
                for source_id in conditions[left.id].keys() & conditions[right.id].keys()
            ):
                continue
            if left.workspace.mode != "isolated" or right.workspace.mode != "isolated":
                error(
                    f"steps.{right.id}.workspace",
                    "parallel_writers",
                    f"Order writers '{left.id}'/'{right.id}', or use isolated workspaces with reconciliation.",
                )
                continue
            if left.workspace.key == right.workspace.key:
                error(
                    f"steps.{right.id}.workspace",
                    "shared_isolation",
                    "Parallel isolated writers need distinct workspace keys.",
                )
            if not any(
                isinstance(step.runner, RecipeCommandRunner)
                and {left.id, right.id} <= set(step.runner.reconciles)
                and {left.id, right.id} <= ancestors[step.id]
                and all(
                    any(
                        port.type == "code_ref"
                        and not port.optional
                        and isinstance(port.binding, RecipeOutputBinding)
                        and port.binding.step_id == writer.id
                        for port in step.inputs.values()
                    )
                    for writer in (left, right)
                )
                for step in definition.steps
            ):
                error(
                    "steps", "missing_reconciliation", f"Reconcile exact code refs from '{left.id}' and '{right.id}'."
                )

    return RecipeValidationResult(valid=not issues, issues=issues)
