"""Bounded Job-owned recipe inspection. GET requests never start runners."""

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query

from backend.models.api_schemas import (
    RecipeAttemptInspection,
    RecipeCurrentRunResponse,
    RecipeRunListResponse,
    RecipeRunResponse,
    RecipeStepInspection,
    RecipeStepListResponse,
)
from backend.services.recipe.inspection import RecipeInspectionService

router = APIRouter(
    prefix="/jobs/{job_id}/recipe",
    tags=["recipes"],
    route_class=DishkaRoute,
)


@router.get("/current", response_model=RecipeCurrentRunResponse, response_model_exclude_unset=True)
async def current_run(job_id: str, service: FromDishka[RecipeInspectionService]) -> RecipeCurrentRunResponse:
    return await service.current(job_id)


@router.get("/runs", response_model=RecipeRunListResponse, response_model_exclude_unset=True)
async def run_history(
    job_id: str,
    service: FromDishka[RecipeInspectionService],
    after: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    high_water: int | None = Query(None, alias="highWater", ge=0),
) -> RecipeRunListResponse:
    return await service.history(job_id, after=after, limit=limit, high_water=high_water)


@router.get("/runs/{run_id}", response_model=RecipeRunResponse, response_model_exclude_unset=True)
async def get_run(job_id: str, run_id: str, service: FromDishka[RecipeInspectionService]) -> RecipeRunResponse:
    return await service.run(job_id, run_id)


@router.get("/runs/{run_id}/steps", response_model=RecipeStepListResponse, response_model_exclude_unset=True)
async def list_steps(
    job_id: str,
    run_id: str,
    service: FromDishka[RecipeInspectionService],
    after: str = Query("", max_length=4096),
    limit: int = Query(30, ge=1, le=100),
) -> RecipeStepListResponse:
    return await service.steps(job_id, run_id, after=after, limit=limit)


@router.get("/runs/{run_id}/steps/{step_id}", response_model=RecipeStepInspection, response_model_exclude_unset=True)
async def inspect_step(
    job_id: str, run_id: str, step_id: str, service: FromDishka[RecipeInspectionService]
) -> RecipeStepInspection:
    return await service.step(job_id, run_id, step_id)


@router.get(
    "/runs/{run_id}/steps/{step_id}/attempts/{attempt_id}",
    response_model=RecipeAttemptInspection,
    response_model_exclude_unset=True,
)
async def inspect_attempt(
    job_id: str,
    run_id: str,
    step_id: str,
    attempt_id: str,
    service: FromDishka[RecipeInspectionService],
) -> RecipeAttemptInspection:
    return await service.attempt(job_id, run_id, step_id, attempt_id)
