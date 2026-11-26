"""Operator navigation and peer-note relay. Agent callers use bound SDK tools."""

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.api_schemas import (
    PeerMessageListResponse,
    PeerMessageRequest,
    PeerMessageResponse,
    SessionDirectoryResponse,
)
from backend.services.runtime import RuntimeService
from backend.services.session_coordination import SessionCoordination

router = APIRouter(prefix="/jobs", tags=["session-coordination"], route_class=DishkaRoute)


@router.get("/{job_id}/sessions", response_model=SessionDirectoryResponse)
async def session_directory(
    job_id: str,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    runtime: FromDishka[RuntimeService],
    offset: int = Query(default=0, ge=0),
) -> SessionDirectoryResponse:
    return await SessionCoordination(sf, runtime).directory(job_id, offset)


@router.get("/{job_id}/peer-messages", response_model=PeerMessageListResponse)
async def peer_messages(
    job_id: str,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    runtime: FromDishka[RuntimeService],
    offset: int = Query(default=0, ge=0),
) -> PeerMessageListResponse:
    return await SessionCoordination(sf, runtime).messages(job_id, offset)


@router.post("/{job_id}/peer-messages", response_model=PeerMessageResponse)
async def relay_peer_message(
    job_id: str,
    body: PeerMessageRequest,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    runtime: FromDishka[RuntimeService],
) -> PeerMessageResponse:
    return await SessionCoordination(sf, runtime).send(job_id, body, origin="operator")
