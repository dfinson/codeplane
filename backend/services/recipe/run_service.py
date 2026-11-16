"""Resolve Job-owned recipe snapshots and exact attempt inputs."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from backend.models.domain import StateConflictError
from backend.models.schemas.recipes import (
    RecipeCommandRunner,
    RecipeInputReceipt,
    RecipeLiteralBinding,
    RecipeParameterBinding,
    RecipeValidationIssue,
    RecipeValidationResult,
)
from backend.services.recipe.definition_service import RecipeNotFoundError, RecipeValidationError
from backend.services.recipe.validation import content_checksum, value_matches

if TYPE_CHECKING:
    from pydantic import JsonValue

    from backend.models.schemas.recipes import (
        RecipeRevisionResponse,
        RecipeRunResponse,
        RecipeSelection,
        RecipeStepInstanceResponse,
    )
    from backend.persistence.recipe_run_repo import RecipeRunRepository
    from backend.services.recipe.definition_service import RecipeDefinitionService


def resolve_command_arguments(runner: RecipeCommandRunner, inputs: dict[str, RecipeInputReceipt]) -> list[str]:
    """Resolve argument values without shell interpolation or command reconstruction."""
    argv: list[str] = []
    for argument in runner.argv:
        if isinstance(argument, str):
            value = argument
        else:
            receipt = inputs.get(argument.input)
            if receipt is None or not receipt.present:
                raise StateConflictError(f"Command input '{argument.input}' is unavailable.")
            selected = receipt.value
            if argument.field is not None:
                if not isinstance(selected, dict):
                    raise StateConflictError("Command field selection requires a code reference.")
                selected = selected.get(argument.field)
            if not isinstance(selected, (str, int, float, bool)):
                raise StateConflictError(f"Command input '{argument.input}' is not a scalar argument.")
            value = selected if isinstance(selected, str) else json.dumps(selected, allow_nan=False)
        if "\x00" in value:
            raise StateConflictError("Command arguments cannot contain NUL characters.")
        argv.append(value)
    if not argv or not argv[0]:
        raise StateConflictError("The resolved command has no executable.")
    return argv


class RecipeRunService:
    def __init__(self, definitions: RecipeDefinitionService, runs: RecipeRunRepository) -> None:
        self._definitions = definitions
        self._runs = runs

    async def prepare_selection(
        self, selection: RecipeSelection
    ) -> tuple[RecipeRevisionResponse, dict[str, JsonValue]]:
        revision = await self._definitions.get_revision(selection.revision_id)
        parameters = revision.definition.parameters
        values = dict(selection.parameters)
        issues: list[RecipeValidationIssue] = []
        for name in values.keys() - parameters.keys():
            issues.append(
                RecipeValidationIssue(
                    path=f"parameters.{name}",
                    code="unknown_parameter",
                    message="This revision does not declare this input.",
                )
            )
        for name, parameter in parameters.items():
            if name not in values:
                if parameter.required:
                    issues.append(
                        RecipeValidationIssue(
                            path=f"parameters.{name}",
                            code="missing_input",
                            message="Supply this required recipe input.",
                        )
                    )
                    continue
                values[name] = parameter.default
            if not value_matches(values[name], parameter.type):
                issues.append(
                    RecipeValidationIssue(
                        path=f"parameters.{name}", code="type_mismatch", message=f"This input must be {parameter.type}."
                    )
                )
        if issues:
            raise RecipeValidationError(RecipeValidationResult(valid=False, issues=issues))
        return revision, values

    async def get_for_job(self, job_id: str) -> RecipeRunResponse | None:
        return await self._runs.get_for_job(job_id)

    async def get_step(self, job_id: str, step_instance_id: str) -> RecipeStepInstanceResponse:
        run = await self._runs.get_for_job(job_id)
        step = await self._runs.get_step(step_instance_id)
        if run is None or step is None or step.run_id != run.id:
            raise RecipeNotFoundError("The selected step does not belong to this Job's recipe run.")
        return step

    async def resolve_inputs(self, instance: RecipeStepInstanceResponse) -> dict[str, RecipeInputReceipt]:
        run = await self._runs.get(instance.run_id)
        if run is None:
            raise RecipeNotFoundError("The pinned recipe run no longer exists.")
        if run.snapshot_checksum != content_checksum(run.snapshot.model_dump(mode="json", exclude_unset=True)):
            raise StateConflictError("The run snapshot failed its content integrity check.")
        inputs: dict[str, RecipeInputReceipt] = {}
        for name, port in instance.definition.inputs.items():
            binding = port.binding
            if binding is None:
                raise StateConflictError(f"Input '{name}' has no published binding.")
            if isinstance(binding, RecipeLiteralBinding):
                value = binding.value
            elif isinstance(binding, RecipeParameterBinding):
                if binding.parameter not in run.inputs:
                    raise StateConflictError(f"Run parameter '{binding.parameter}' was not pinned.")
                value = run.inputs[binding.parameter]
            else:
                source = await self._runs.get_step_by_path(run.id, binding.step_id)
                attempt = (
                    await self._runs.get_attempt(source.current_attempt_id)
                    if source is not None
                    and source.current_attempt_id is not None
                    and (source.state == "succeeded" or (source.state == "failed" and port.optional))
                    else None
                )
                if attempt is not None and (
                    attempt.result is None or getattr(attempt.result.evidence, "uncertain", False)
                ):
                    attempt = None
                output = (
                    next((item for item in attempt.outputs if item.name == binding.output), None) if attempt else None
                )
                if output is None:
                    if not port.optional:
                        raise StateConflictError(
                            f"Required output '{binding.step_id}.{binding.output}' is unavailable."
                        )
                    inputs[name] = RecipeInputReceipt(
                        type=port.type, present=False, degraded_reason=port.degraded_reason
                    )
                    continue
                if output.checksum != content_checksum(output.value) or output.type != port.type:
                    raise StateConflictError(
                        f"Output '{binding.step_id}.{binding.output}' failed its integrity/type check."
                    )
                inputs[name] = RecipeInputReceipt(
                    type=port.type,
                    present=True,
                    value=output.value,
                    receipt_id=output.id,
                    producing_attempt_id=output.attempt_id,
                    producer_outcome=attempt.result.outcome if attempt and attempt.result else None,
                    checksum=output.checksum,
                    degraded_reason=port.degraded_reason if source and source.state == "failed" else None,
                )
                continue
            if not value_matches(value, port.type):
                raise StateConflictError(f"Input '{name}' does not satisfy its pinned type.")
            inputs[name] = RecipeInputReceipt(
                type=port.type, present=True, value=value, checksum=content_checksum(value)
            )
        return inputs
