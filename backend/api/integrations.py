"""Authenticated operator history registration; no execution admission."""

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Query

from backend.models.api_schemas import (
    JobResponse,
    ObservationEventList,
    ObservationRecordBatch,
    ObservationRecordReceipt,
    RegisterObservationRequest,
)
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.record_service import ObservationRecords

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


@router.post("/{binding_id}/records", response_model=ObservationRecordReceipt)
async def append_observation_records(
    binding_id: str, body: ObservationRecordBatch, service: FromDishka[ObservationRecords],
) -> ObservationRecordReceipt:
    try:
        return await service.append(binding_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{binding_id}/events", response_model=ObservationEventList)
async def get_observation_events(
    binding_id: str, bindings: FromDishka[IntegrationRepository], events: FromDishka[EventRepository],
    after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000),
) -> ObservationEventList:
    binding = await bindings.get(binding_id)
    if binding is None or binding.job_id is None:
        raise HTTPException(status_code=404, detail="Observation history not found")
    stored = await events.list_after(after, job_id=binding.job_id, limit=limit)
    return ObservationEventList(
        events=[item.event for item in stored],
        next_cursor=stored[-1].storage_cursor if stored else after,
    )
