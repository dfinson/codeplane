"""Authenticated operator history registration; no execution admission."""

import sqlite3

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from sqlalchemy.exc import OperationalError

from backend.config import CPLConfig
from backend.models.api_schemas import (
    IntegrationProfileList,
    IssuedIngestCredential,
    IssueIngestCredentialRequest,
    JobResponse,
    ObservationEventList,
    ObservationRecordBatch,
    ObservationRecordReceipt,
    OtlpExportResponse,
    OtlpTraceRequest,
    RegisterObservationRequest,
)
from backend.models.domain import StateConflictError
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.ingest.credentials import INGEST_TOKEN_HEADER, IngestCredentials
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.record_service import ObservationRecords

router = APIRouter(prefix="/integrations", tags=["integrations"], route_class=DishkaRoute)


@router.get("/profiles", response_model=IntegrationProfileList)
async def list_integration_profiles(config: FromDishka[CPLConfig]) -> IntegrationProfileList:
    return AdapterRegistry(config=config).list_profiles()


@router.post("/{binding_id}/ingest-credential", response_model=IssuedIngestCredential)
async def issue_ingest_credential(
    binding_id: str,
    body: IssueIngestCredentialRequest,
    bindings: FromDishka[IntegrationRepository],
    config: FromDishka[CPLConfig],
    response: Response,
) -> IssuedIngestCredential:
    try:
        token = await IngestCredentials(bindings, config).issue(binding_id, enabled=body.enabled)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return IssuedIngestCredential(token=token, enabled=body.enabled)


@router.delete("/{binding_id}/ingest-credential", status_code=204)
async def revoke_ingest_credential(
    binding_id: str,
    bindings: FromDishka[IntegrationRepository],
    config: FromDishka[CPLConfig],
) -> None:
    try:
        await IngestCredentials(bindings, config).revoke(binding_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post(
    "/{binding_id}/traces",
    response_model=OtlpExportResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": OtlpTraceRequest.model_json_schema()}},
        },
    },
)
async def ingest_traces(
    binding_id: str,
    request: Request,
    bindings: FromDishka[IntegrationRepository],
    config: FromDishka[CPLConfig],
    service: FromDishka[ObservationRecords],
    ingest_token: str | None = Header(default=None, alias=INGEST_TOKEN_HEADER),
) -> OtlpExportResponse:
    try:
        authorization = await IngestCredentials(bindings, config).authorize(binding_id, ingest_token)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(status_code=415, detail="Only OTLP HTTP/JSON is currently implemented")
        await service.append_otlp(binding_id, await request.body(), authorization=authorization)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OperationalError as exc:
        code = getattr(exc.orig, "sqlite_errorcode", 0)
        if code & 0xFF not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise
        raise HTTPException(status_code=503, detail="Ingest storage is busy", headers={"Retry-After": "1"}) from exc
    return OtlpExportResponse()


@router.post("/observations", response_model=JobResponse, status_code=201)
async def register_observation(
    body: RegisterObservationRequest, service: FromDishka[HistoryRegistration]
) -> JobResponse:
    try:
        job = await service.register(
            binding_id=body.binding_id,
            project_id=body.project_id,
            format_id=body.format_id,
            external_session_id=body.external_session_id,
            title=body.title,
            repo=body.repo,
            base_ref=body.base_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JobResponse.from_domain(job)


@router.post("/{binding_id}/records", response_model=ObservationRecordReceipt)
async def append_observation_records(
    binding_id: str,
    body: ObservationRecordBatch,
    service: FromDishka[ObservationRecords],
) -> ObservationRecordReceipt:
    try:
        return await service.append(binding_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{binding_id}/events", response_model=ObservationEventList)
async def get_observation_events(
    binding_id: str,
    bindings: FromDishka[IntegrationRepository],
    events: FromDishka[EventRepository],
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> ObservationEventList:
    binding = await bindings.get(binding_id)
    if binding is None or binding.job_id is None:
        raise HTTPException(status_code=404, detail="Observation history not found")
    stored = await events.list_after(after, job_id=binding.job_id, limit=limit)
    return ObservationEventList(
        events=[item.event for item in stored],
        next_cursor=stored[-1].storage_cursor if stored else after,
    )
