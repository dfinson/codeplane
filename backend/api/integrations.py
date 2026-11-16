"""Authenticated operator history registration; no execution admission."""

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException

from backend.models.api_schemas import JobResponse, RegisterObservationRequest
from backend.services.ingest.history_registration import HistoryRegistration

router = APIRouter(prefix="/integrations", tags=["integrations"], route_class=DishkaRoute)


@router.post("/observations", response_model=JobResponse, status_code=201)
async def register_observation(
    body: RegisterObservationRequest, service: FromDishka[HistoryRegistration]
) -> JobResponse:
    try:
        job = await service.register(
            binding_id=body.binding_id, project_id=body.project_id, format_id=body.format_id,
            external_session_id=body.external_session_id, title=body.title, repo=body.repo, base_ref=body.base_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JobResponse.from_domain(job)
