"""Action policy settings API — preset, batch window, per-preset USD ceilings, MCP configs, export/import.

Wholesale governance adoption retired the hand-rolled path/action/cost rule editors
and the pattern-trust grant editor: the DECISION (rules, protected paths,
count/effect budget, reason-code trust) is owned by ``traceforge.governance``. What
CodePlane still configures here is the product surface it owns — the preset (which
selects a TraceForge profile), the approval batch window, the per-preset USD
ceiling overlay (enforced natively by ``JobSpendCeilingAssessor``), and the MCP
server launch configs. The operator "trust this whole session" action lives on
``POST /jobs/{job_id}/approvals/trust`` (a reason-code session grant), not here.
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.events import EventKind, new_event
from backend.models.schemas.base import CamelModel
from backend.persistence.policy_repo import PolicyRepository
from backend.services.events.event_bus import EventBus

router = APIRouter(prefix="/settings/policy", tags=["policy"], route_class=DishkaRoute)
log = structlog.get_logger()

_PRESET_PATTERN = r"^(autonomous|supervised|locked)$"


async def _notify_policy_changed(event_bus: EventBus) -> None:
    """Publish a policy_settings_changed event so running jobs reload policy."""
    await event_bus.publish(
        new_event(session_id="", timestamp=datetime.now(UTC), kind=EventKind.policy_settings_changed, payload={})
    )


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class PolicyConfigResponse(CamelModel):
    preset: str
    batch_window_seconds: float


class UpdatePresetRequest(CamelModel):
    preset: str = Field(pattern=_PRESET_PATTERN)


class UpdateConfigRequest(CamelModel):
    preset: str | None = Field(default=None, pattern=_PRESET_PATTERN)
    batch_window_seconds: float | None = None


class UsdCeilingEntry(CamelModel):
    """A per-preset USD ceiling: warn line and hard ceiling (either may be null)."""

    warn_usd: float | None = None
    ceiling_usd: float | None = None


class UsdCeilingsResponse(CamelModel):
    """Effective per-preset ceilings (baked profile default overlaid with overrides)."""

    ceilings: dict[str, UsdCeilingEntry] = Field(default_factory=dict)


class UpdateUsdCeilingsRequest(CamelModel):
    ceilings: dict[str, UsdCeilingEntry]


class MCPServerRequest(CamelModel):
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    contained: bool = False
    reversible: bool = False
    trusted: bool = False
    tool_overrides: dict[str, dict[str, bool]] = Field(default_factory=dict)


class MCPServerResponse(CamelModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    contained: bool = False
    reversible: bool = False
    trusted: bool = False
    tool_overrides: dict[str, dict[str, bool]] = Field(default_factory=dict)
    created_at: str


class FullPolicyResponse(CamelModel):
    config: PolicyConfigResponse
    usd_ceilings: dict[str, UsdCeilingEntry] = Field(default_factory=dict)
    mcp_servers: list[MCPServerResponse] = Field(default_factory=list)


class PolicyImportRequest(CamelModel):
    version: int = 2
    config: dict[str, Any] | None = None
    usd_ceilings: dict[str, Any] = Field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)


def _effective_ceilings(overrides: dict[str, tuple[float | None, float | None]]) -> dict[str, UsdCeilingEntry]:
    """Merge stored per-preset overrides over each profile's baked default ceiling."""
    from backend.services.action_policy.preset_profiles import PROFILES, profile_for

    out: dict[str, UsdCeilingEntry] = {}
    for preset in PROFILES:
        profile = profile_for(preset)
        warn, ceiling = profile.warn_usd, profile.ceiling_usd
        if preset.value in overrides:
            o_warn, o_ceiling = overrides[preset.value]
            warn, ceiling = o_warn, o_ceiling
        out[preset.value] = UsdCeilingEntry(warn_usd=warn, ceiling_usd=ceiling)
    return out


# ---------------------------------------------------------------------------
# Policy config
# ---------------------------------------------------------------------------


@router.get("", response_model=FullPolicyResponse)
async def get_policy(sf: FromDishka[async_sessionmaker[AsyncSession]]) -> FullPolicyResponse:
    async with sf() as session:
        repo = PolicyRepository(session)
        config = await repo.get_config()
        overrides = await repo.get_usd_ceilings()
        return FullPolicyResponse(
            config=PolicyConfigResponse(**config),
            usd_ceilings=_effective_ceilings(overrides),
            mcp_servers=[MCPServerResponse(**r) for r in await repo.list_mcp_configs()],
        )


@router.put("/preset")
async def update_preset(
    body: UpdatePresetRequest,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    event_bus: FromDishka[EventBus],
) -> PolicyConfigResponse:
    async with sf() as session:
        repo = PolicyRepository(session)
        result = await repo.update_config(preset=body.preset)
        await session.commit()
    await _notify_policy_changed(event_bus)
    return PolicyConfigResponse(**result)


@router.put("/config")
async def update_config(
    body: UpdateConfigRequest,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    event_bus: FromDishka[EventBus],
) -> PolicyConfigResponse:
    async with sf() as session:
        repo = PolicyRepository(session)
        updates = body.model_dump(exclude_none=True)
        result = await repo.update_config(**updates)
        await session.commit()
    await _notify_policy_changed(event_bus)
    return PolicyConfigResponse(**result)


# ---------------------------------------------------------------------------
# Per-preset USD ceilings (native cost-ceiling overlay)
# ---------------------------------------------------------------------------


@router.get("/usd-ceilings", response_model=UsdCeilingsResponse)
async def get_usd_ceilings(
    sf: FromDishka[async_sessionmaker[AsyncSession]],
) -> UsdCeilingsResponse:
    async with sf() as session:
        repo = PolicyRepository(session)
        overrides = await repo.get_usd_ceilings()
    return UsdCeilingsResponse(ceilings=_effective_ceilings(overrides))


@router.put("/usd-ceilings", response_model=UsdCeilingsResponse)
async def update_usd_ceilings(
    body: UpdateUsdCeilingsRequest,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    event_bus: FromDishka[EventBus],
) -> UsdCeilingsResponse:
    ceilings = {
        preset: {"warn_usd": entry.warn_usd, "ceiling_usd": entry.ceiling_usd}
        for preset, entry in body.ceilings.items()
    }
    async with sf() as session:
        repo = PolicyRepository(session)
        await repo.set_usd_ceilings(ceilings)
        overrides = await repo.get_usd_ceilings()
        await session.commit()
    await _notify_policy_changed(event_bus)
    return UsdCeilingsResponse(ceilings=_effective_ceilings(overrides))


# ---------------------------------------------------------------------------
# MCP server configs
# ---------------------------------------------------------------------------


@router.get("/mcp-servers")
async def list_mcp_servers(
    sf: FromDishka[async_sessionmaker[AsyncSession]],
) -> list[dict[str, Any]]:
    async with sf() as session:
        repo = PolicyRepository(session)
        return await repo.list_mcp_configs()


@router.post("/mcp-servers")
async def create_mcp_server(
    body: MCPServerRequest,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    event_bus: FromDishka[EventBus],
    name: str | None = None,
) -> dict[str, Any]:
    # name comes from query param for POST
    if not name:
        raise HTTPException(400, "name query parameter required")
    async with sf() as session:
        repo = PolicyRepository(session)
        result = await repo.upsert_mcp_config(
            name,
            command=body.command,
            args=body.args,
            env=body.env,
            contained=body.contained,
            reversible=body.reversible,
            trusted=body.trusted,
            tool_overrides=body.tool_overrides,
        )
        await session.commit()
    await _notify_policy_changed(event_bus)
    return result


@router.put("/mcp-servers/{name}")
async def update_mcp_server(
    name: str,
    body: MCPServerRequest,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    event_bus: FromDishka[EventBus],
) -> dict[str, Any]:
    async with sf() as session:
        repo = PolicyRepository(session)
        result = await repo.upsert_mcp_config(
            name,
            command=body.command,
            args=body.args,
            env=body.env,
            contained=body.contained,
            reversible=body.reversible,
            trusted=body.trusted,
            tool_overrides=body.tool_overrides,
        )
        await session.commit()
    await _notify_policy_changed(event_bus)
    return result


@router.delete("/mcp-servers/{name}")
async def delete_mcp_server(
    name: str,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    event_bus: FromDishka[EventBus],
) -> dict[str, str]:
    async with sf() as session:
        repo = PolicyRepository(session)
        deleted = await repo.delete_mcp_config(name)
        if not deleted:
            raise HTTPException(404, "MCP server config not found")
        await session.commit()
    await _notify_policy_changed(event_bus)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_policy(
    sf: FromDishka[async_sessionmaker[AsyncSession]],
) -> dict[str, Any]:
    async with sf() as session:
        repo = PolicyRepository(session)
        return await repo.export_all()


@router.post("/import")
async def import_policy(
    body: PolicyImportRequest,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    event_bus: FromDishka[EventBus],
) -> dict[str, str]:
    async with sf() as session:
        repo = PolicyRepository(session)
        try:
            await repo.import_all(body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
    await _notify_policy_changed(event_bus)
    return {"status": "imported"}
