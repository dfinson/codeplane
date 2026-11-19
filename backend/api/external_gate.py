"""Thin scoped-credential transports for the same external preflight service."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from backend.models.api_schemas import (
    ExternalGateConsume,
    ExternalGateDecision,
    ExternalGatePreflight,
    ExternalGateReport,
    ExternalGateSourceRequest,
    ExternalGateSourceResponse,
)
from backend.services.adapters.external_gate_wire import decode_hook
from backend.services.adapters.native_gates.lifecycle import retain_task
from backend.services.ingest.otel_decoder import decode_json_body
from backend.services.runtime.external_gate import ExternalGateAuthenticationError
from backend.services.runtime.service import RuntimeService

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from backend.services.runtime.external_gate import ExternalGates, _Source

router = APIRouter(tags=["external-gate"], route_class=DishkaRoute)


def _body_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": model.model_json_schema()}}}}


async def _json(request: Request) -> Any:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 131072:
            raise HTTPException(413, "External gate envelope exceeds 128 KiB")
        raw.extend(chunk)
    try:
        return decode_json_body(bytes(raw))
    except ValueError as exc:
        raise HTTPException(400, "Invalid gate JSON") from exc


def _validate[T: BaseModel](model: type[T], raw: Any) -> T:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_input=False, include_context=False, include_url=False)) from exc


async def _source(request: Request, runtime: RuntimeService, source_id: str) -> _Source:
    try:
        return await runtime.external_gates.authenticate(source_id, request.headers.get("authorization"))
    except ExternalGateAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from exc


async def _connected[T](
    request: Request, service: ExternalGates, source: _Source, request_id: str, operation: Awaitable[T]
) -> T:
    task = asyncio.ensure_future(operation)

    async def disconnected() -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(0.05)

    gone = asyncio.create_task(disconnected())
    try:
        await asyncio.wait((task, gone), return_when=asyncio.FIRST_COMPLETED)
        if gone.done() or await request.is_disconnected():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if request_id in source.requests:
                await service.retire(source, request_id, "caller_disconnected")
            raise HTTPException(409, "Gate caller disconnected")
        return await task
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if request_id in source.requests:
            await retain_task(asyncio.create_task(service.retire(source, request_id, "caller_disconnected")))
        raise
    finally:
        gone.cancel()
        await asyncio.gather(gone, return_exceptions=True)


@router.post("/jobs/{job_id}/external-gates", response_model=ExternalGateSourceResponse)
async def issue_source(
    job_id: str,
    body: ExternalGateSourceRequest,
    runtime: FromDishka[RuntimeService],
) -> ExternalGateSourceResponse:
    return await runtime.external_gates.issue(job_id, body)


@router.post(
    "/gate/{source_id}/requests", response_model=ExternalGateDecision, openapi_extra=_body_schema(ExternalGatePreflight)
)
async def preflight(source_id: str, request: Request, runtime: FromDishka[RuntimeService]) -> ExternalGateDecision:
    source = await _source(request, runtime, source_id)
    body = _validate(ExternalGatePreflight, await _json(request))
    return await _connected(
        request, runtime.external_gates, source, body.request_id, runtime.external_gates.preflight(source, body)
    )


@router.get("/gate/{source_id}/requests/{request_id}", response_model=ExternalGateDecision)
async def status(
    source_id: str,
    request_id: str,
    request: Request,
    runtime: FromDishka[RuntimeService],
    wait_seconds: float = Query(0, alias="waitSeconds", ge=0, le=25),
) -> ExternalGateDecision:
    source = await _source(request, runtime, source_id)
    return await _connected(
        request,
        runtime.external_gates,
        source,
        request_id,
        runtime.external_gates.wait(source, request_id, wait_seconds),
    )


@router.post(
    "/gate/{source_id}/requests/{request_id}/consume",
    response_model=ExternalGateDecision,
    openapi_extra=_body_schema(ExternalGateConsume),
)
async def consume(
    source_id: str,
    request_id: str,
    request: Request,
    runtime: FromDishka[RuntimeService],
) -> ExternalGateDecision:
    source = await _source(request, runtime, source_id)
    body = _validate(ExternalGateConsume, await _json(request))
    return await _connected(
        request, runtime.external_gates, source, request_id, runtime.external_gates.consume(source, request_id, body)
    )


@router.delete("/gate/{source_id}/requests/{request_id}", response_model=ExternalGateDecision)
async def retire(
    source_id: str, request_id: str, request: Request, runtime: FromDishka[RuntimeService]
) -> ExternalGateDecision:
    source = await _source(request, runtime, source_id)
    return await runtime.external_gates.retire(source, request_id, "caller_cancelled")


@router.post(
    "/gate/{source_id}/requests/{request_id}/observations",
    status_code=204,
    openapi_extra=_body_schema(ExternalGateReport),
)
async def reported(source_id: str, request_id: str, request: Request, runtime: FromDishka[RuntimeService]) -> None:
    source = await _source(request, runtime, source_id)
    body = _validate(ExternalGateReport, await _json(request))
    await runtime.external_gates.reported(source, request_id, body)


@router.post("/gate/{source_id}/invoke", response_model=ExternalGateDecision)
async def legacy_invoke(source_id: str, request: Request, runtime: FromDishka[RuntimeService]) -> ExternalGateDecision:
    source = await _source(request, runtime, source_id)
    try:
        action = decode_hook(await _json(request))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "Unsupported gate hook envelope") from exc
    credential = request.headers["authorization"].partition(" ")[2]
    return await _connected(
        request,
        runtime.external_gates,
        source,
        action.request_id,
        runtime.external_gates.before_call(source_id, credential, action),
    )
