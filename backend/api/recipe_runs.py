"""Bounded Job-owned recipe inspection. GET requests never start runners."""

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, Response

from backend.models.api_schemas import (
    JobPendingDecisionPage,
    RecipeAttemptControl,
    RecipeAttemptInspection,
    RecipeAttemptResumeRequest,
    RecipeCurrentRunResponse,
    RecipeGateDecisionRequest,
    RecipeMessageReceipt,
    RecipeMessageRequest,
    RecipeOperationReceipt,
    RecipeReconcileRequest,
    RecipeRunListResponse,
    RecipeRunResponse,
    RecipeStepInspection,
    RecipeStepListResponse,
    RecipeStopRequest,
)
from backend.services.recipe.control import RecipeControlService
from backend.services.recipe.inspection import RecipeInspectionService

router = APIRouter(
    prefix="/jobs/{job_id}/recipe",
    tags=["recipes"],
    route_class=DishkaRoute,
)


@router.get("/current", response_model=RecipeCurrentRunResponse, response_model_exclude_unset=True)
async def current_run(job_id: str, service: FromDishka[RecipeInspectionService]) -> RecipeCurrentRunResponse:
    return await service.current(job_id)


@router.get("/decisions", response_model=JobPendingDecisionPage)
async def pending_decisions(
    job_id: str,
    service: FromDishka[RecipeInspectionService],
    after: str = Query("", max_length=128),
    limit: int = Query(20, ge=1, le=100),
) -> JobPendingDecisionPage:
    return await service.pending_decisions(job_id, after=after, limit=limit)


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


@router.get(
    "/runs/{run_id}/steps/{step_id}/attempts/{attempt_id}/control",
    response_model=RecipeAttemptControl,
)
async def attempt_control(
    job_id: str,
    run_id: str,
    step_id: str,
    attempt_id: str,
    service: FromDishka[RecipeControlService],
) -> RecipeAttemptControl:
    return await service.context(job_id, run_id, step_id, attempt_id)


@router.post(
    "/runs/{run_id}/steps/{step_id}/attempts/{attempt_id}/messages",
    response_model=RecipeMessageReceipt,
    responses={409: {"model": RecipeMessageReceipt}},
)
async def message_attempt(
    job_id: str,
    run_id: str,
    step_id: str,
    attempt_id: str,
    body: RecipeMessageRequest,
    response: Response,
    service: FromDishka[RecipeControlService],
) -> RecipeMessageReceipt:
    receipt = await service.message(job_id, run_id, step_id, attempt_id, body)
    if receipt.status == "conflict":
        response.status_code = 409
    return receipt


@router.post("/runs/{run_id}/steps/{step_id}/attempts/{attempt_id}/stop", response_model=RecipeOperationReceipt)
async def stop_attempt(
    job_id: str,
    run_id: str,
    step_id: str,
    attempt_id: str,
    body: RecipeStopRequest,
    service: FromDishka[RecipeControlService],
) -> RecipeOperationReceipt:
    return await service.stop(job_id, run_id, step_id, attempt_id, body.expected_token)


@router.post("/runs/{run_id}/steps/{step_id}/attempts/{attempt_id}/resume", response_model=RecipeOperationReceipt)
async def resume_attempt(
    job_id: str,
    run_id: str,
    step_id: str,
    attempt_id: str,
    body: RecipeAttemptResumeRequest,
    service: FromDishka[RecipeControlService],
) -> RecipeOperationReceipt:
    return await service.resume(job_id, run_id, step_id, attempt_id, body.expected_version)


@router.post("/runs/{run_id}/steps/{step_id}/attempts/{attempt_id}/reconcile", response_model=RecipeOperationReceipt)
async def reconcile_attempt(
    job_id: str,
    run_id: str,
    step_id: str,
    attempt_id: str,
    body: RecipeReconcileRequest,
    service: FromDishka[RecipeControlService],
) -> RecipeOperationReceipt:
    return await service.reconcile(job_id, run_id, step_id, attempt_id, body)


@router.post("/runs/{run_id}/steps/{step_id}/attempts/{attempt_id}/decision", response_model=RecipeOperationReceipt)
async def decide_attempt(
    job_id: str,
    run_id: str,
    step_id: str,
    attempt_id: str,
    body: RecipeGateDecisionRequest,
    service: FromDishka[RecipeControlService],
) -> RecipeOperationReceipt:
    return await service.decide(job_id, run_id, step_id, attempt_id, body)
