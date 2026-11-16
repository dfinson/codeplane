"""Read exact persisted recipe identities without admitting or recovering work."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.models.domain import StateConflictError
from backend.models.schemas.recipes import (
    RecipeAttemptInspection,
    RecipeCurrentRunResponse,
    RecipeGateInspection,
    RecipeStepInspection,
    RecipeStepListResponse,
)
from backend.services.recipe.definition_service import RecipeNotFoundError

if TYPE_CHECKING:
    from backend.models.schemas.recipes import RecipeRunListResponse, RecipeRunResponse, RecipeStepInstanceResponse
    from backend.persistence.approval_repo import ApprovalRepository
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.recipe_run_repo import RecipeRunRepository


class RecipeInspectionService:
    def __init__(self, runs: RecipeRunRepository, jobs: JobRepository, approvals: ApprovalRepository) -> None:
        self._runs = runs
        self._jobs = jobs
        self._approvals = approvals

    async def _require_job(self, job_id: str) -> None:
        if await self._jobs.get(job_id) is None:
            raise RecipeNotFoundError("The selected Job does not exist.")

    async def current(self, job_id: str) -> RecipeCurrentRunResponse:
        await self._require_job(job_id)
        return RecipeCurrentRunResponse(run=await self._runs.get_for_job(job_id))

    async def history(self, job_id: str, *, after: int, limit: int, high_water: int | None) -> RecipeRunListResponse:
        await self._require_job(job_id)
        return await self._runs.list_runs(job_id, after=after, limit=limit, high_water=high_water)

    async def run(self, job_id: str, run_id: str) -> RecipeRunResponse:
        run = await self._runs.get(run_id)
        if run is None or run.job_id != job_id:
            raise RecipeNotFoundError("The selected recipe run does not belong to this Job.")
        return run

    async def steps(self, job_id: str, run_id: str, *, after: str, limit: int) -> RecipeStepListResponse:
        await self.run(job_id, run_id)
        if not 1 <= limit <= 100:
            raise StateConflictError("Recipe step page size must be between 1 and 100.")
        rows = await self._runs.list_steps(run_id, after=after, limit=limit + 1)
        return RecipeStepListResponse(
            items=rows[:limit], next_cursor=rows[limit - 1].step_path if len(rows) > limit else None
        )

    async def _step(self, job_id: str, run_id: str, step_id: str) -> RecipeStepInstanceResponse:
        await self.run(job_id, run_id)
        step = await self._runs.get_step(step_id)
        if step is None or step.run_id != run_id:
            raise RecipeNotFoundError("The selected step does not belong to this recipe run.")
        return step

    async def step(self, job_id: str, run_id: str, step_id: str) -> RecipeStepInspection:
        step = await self._step(job_id, run_id, step_id)
        return RecipeStepInspection(step=step, attempts=await self._runs.list_attempt_summaries(step.id))

    async def attempt(self, job_id: str, run_id: str, step_id: str, attempt_id: str) -> RecipeAttemptInspection:
        await self._step(job_id, run_id, step_id)
        attempt = await self._runs.get_attempt(attempt_id)
        if attempt is None or attempt.step_instance_id != step_id:
            raise RecipeNotFoundError("The selected attempt does not belong to this step.")
        binding = await self._runs.get_gate(attempt.id)
        gate = None
        if binding is not None:
            approval = await self._approvals.get(binding.approval_id)
            if approval is None or approval.job_id != job_id:
                raise StateConflictError("The retained gate approval is missing or belongs to another Job.")
            gate = RecipeGateInspection(
                binding=binding,
                resolution=approval.resolution,
                notes=approval.notes,
                resolved_at=approval.resolved_at,
            )
        return RecipeAttemptInspection(attempt=attempt, gate=gate)
