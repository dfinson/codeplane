"""Review evidence and rationale, separate from messaging and delivery."""

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query

from backend.models.api_schemas import (
    PrepareReviewRequest,
    RecordReviewRequest,
    ReviewHistoryPage,
    ReviewPreparation,
    ReviewRecord,
)
from backend.services.recipe.review_service import ReviewService

router = APIRouter(prefix="/jobs/{job_id}/reviews", tags=["reviews"], route_class=DishkaRoute)


@router.post("/prepare", response_model=ReviewPreparation, status_code=201)
async def prepare_review(
    job_id: str, body: PrepareReviewRequest, service: FromDishka[ReviewService]
) -> ReviewPreparation:
    return await service.prepare(job_id, body)


@router.post("", response_model=ReviewRecord, status_code=201)
async def record_review(job_id: str, body: RecordReviewRequest, service: FromDishka[ReviewService]) -> ReviewRecord:
    return await service.record(job_id, body)


@router.get("", response_model=ReviewHistoryPage)
async def review_history(
    job_id: str,
    service: FromDishka[ReviewService],
    after: int = Query(0, ge=0),
    high_water: int | None = Query(None, alias="highWater", ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> ReviewHistoryPage:
    return await service.history(job_id, after=after, high_water=high_water, limit=limit)
