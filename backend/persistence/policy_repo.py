"""Application-owned policy settings, including verified-USD overrides."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update

from backend.models.db import MCPServerConfigRow, PolicyConfigRow
from backend.persistence.repository import BaseRepository


class PolicyRepository(BaseRepository):
    """Database access for action policy configuration."""

    # --- Policy config (singleton) ---

    async def get_config(self) -> dict[str, Any]:
        result = await self._session.execute(select(PolicyConfigRow).where(PolicyConfigRow.id == 1))
        row = result.scalar_one_or_none()
        if row is None:
            return {"preset": "supervised", "batch_window_seconds": 5.0}
        return {
            "preset": row.preset,
            "batch_window_seconds": row.batch_window_seconds,
        }

    async def update_config(self, **kwargs: Any) -> dict[str, Any]:
        allowed = {"preset", "batch_window_seconds"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return await self.get_config()
        if "preset" in updates and updates["preset"] not in {"autonomous", "supervised", "locked"}:
            raise ValueError("Unknown policy preset")
        if "batch_window_seconds" in updates:
            value = updates["batch_window_seconds"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.5 <= value <= 30:
                raise ValueError("Approval batch window must be between 0.5 and 30 seconds")
        await self._ensure_config()
        await self._session.execute(update(PolicyConfigRow).where(PolicyConfigRow.id == 1).values(**updates))
        return await self.get_config()

    async def _ensure_config(self) -> None:
        if await self._session.get(PolicyConfigRow, 1) is None:
            self._session.add(PolicyConfigRow(id=1))
            await self._session.flush()

    # --- Per-preset USD ceilings (native cost-ceiling overlay) ---

    async def get_usd_ceilings(self) -> dict[str, tuple[float | None, float | None]]:
        """Return ``{preset: (warn_usd, ceiling_usd)}`` operator overrides.

        Empty when unset. Explicit zero and null override the preset defaults.
        Invalid persisted settings fail visibly rather than changing enforcement.
        """
        result = await self._session.execute(select(PolicyConfigRow).where(PolicyConfigRow.id == 1))
        row = result.scalar_one_or_none()
        if row is None or not row.usd_ceilings_json:
            return {}
        raw = _validate_ceilings(json.loads(row.usd_ceilings_json))
        return {name: (pair["warn_usd"], pair["ceiling_usd"]) for name, pair in raw.items()}

    async def set_usd_ceilings(self, ceilings: dict[str, dict[str, float | None]]) -> dict[str, Any]:
        """Persist per-preset USD ceiling overrides on the singleton config row.

        ``ceilings`` maps a preset name to ``{"warn_usd": .., "ceiling_usd": ..}``;
        either value may be ``None`` (no line). Returns the stored mapping.
        """
        ceilings = _validate_ceilings(ceilings)
        payload = json.dumps(ceilings, allow_nan=False) if ceilings else None
        await self._ensure_config()
        await self._session.execute(
            update(PolicyConfigRow).where(PolicyConfigRow.id == 1).values(usd_ceilings_json=payload)
        )
        return {"usd_ceilings": ceilings}

    # --- MCP server configs ---

    async def list_mcp_configs(self) -> list[dict[str, Any]]:
        result = await self._session.execute(select(MCPServerConfigRow).order_by(MCPServerConfigRow.name))
        return [_mcp_row_to_dict(r) for r in result.scalars()]

    async def get_mcp_config(self, name: str) -> dict[str, Any] | None:
        result = await self._session.execute(select(MCPServerConfigRow).where(MCPServerConfigRow.name == name))
        row = result.scalar_one_or_none()
        return _mcp_row_to_dict(row) if row else None

    async def upsert_mcp_config(self, name: str, **kwargs: Any) -> dict[str, Any]:
        result = await self._session.execute(select(MCPServerConfigRow).where(MCPServerConfigRow.name == name))
        row = result.scalar_one_or_none()
        if row is None:
            row = MCPServerConfigRow(
                name=name,
                command=kwargs.get("command", ""),
                args_json=json.dumps(kwargs["args"]) if "args" in kwargs else None,
                env_json=json.dumps(kwargs["env"]) if "env" in kwargs else None,
                contained=kwargs.get("contained", False),
                reversible=kwargs.get("reversible", False),
                trusted=kwargs.get("trusted", False),
                tool_overrides_json=json.dumps(kwargs["tool_overrides"]) if "tool_overrides" in kwargs else None,
                created_at=datetime.now(UTC).isoformat(),
            )
            self._session.add(row)
        else:
            for k in ("command", "contained", "reversible", "trusted"):
                if k in kwargs:
                    setattr(row, k, kwargs[k])
            if "args" in kwargs:
                row.args_json = json.dumps(kwargs["args"])
            if "env" in kwargs:
                row.env_json = json.dumps(kwargs["env"])
            if "tool_overrides" in kwargs:
                row.tool_overrides_json = json.dumps(kwargs["tool_overrides"])
        return _mcp_row_to_dict(row)

    async def delete_mcp_config(self, name: str) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(delete(MCPServerConfigRow).where(MCPServerConfigRow.name == name)),
        )
        return result.rowcount > 0

    # --- Export / Import ---

    async def export_all(self) -> dict[str, Any]:
        config = await self.get_config()
        return {
            "version": 2,
            "config": config,
            "usd_ceilings": await self.get_usd_ceilings(),
            "mcp_servers": await self.list_mcp_configs(),
        }

    async def import_all(self, data: dict[str, Any]) -> None:
        config = data.get("config") or {}
        ceilings = _validate_ceilings(data.get("usd_ceilings") or {})
        await self.update_config(**config)
        await self.set_usd_ceilings(ceilings)
        for srv in data.get("mcp_servers", []):
            values = dict(srv)
            name = values.pop("name")
            await self.upsert_mcp_config(name, **values)


def _validate_ceilings(raw: Any) -> dict[str, dict[str, float | None]]:
    if not isinstance(raw, dict):
        raise ValueError("USD overrides require a per-preset mapping")
    result = {}
    for preset, pair in raw.items():
        if preset not in {"autonomous", "supervised", "locked"}:
            raise ValueError("Unknown USD policy preset")
        # Version-2 exports used pairs, while the settings endpoint uses named fields.
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            pair = dict(zip(("warn_usd", "ceiling_usd"), pair, strict=True))
        if not isinstance(pair, dict) or set(pair) - {"warn_usd", "ceiling_usd"}:
            raise ValueError("USD overrides require warning and ceiling fields")
        parsed: dict[str, float | None] = {}
        for key in ("warn_usd", "ceiling_usd"):
            value = pair.get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0
            ):
                raise ValueError("USD thresholds must be non-negative finite numbers or null")
            parsed[key] = None if value is None else float(value)
        result[preset] = parsed
    return result


def _mcp_row_to_dict(row: MCPServerConfigRow) -> dict[str, Any]:
    return {
        "name": row.name,
        "command": row.command,
        "args": json.loads(row.args_json) if row.args_json else [],
        "env": json.loads(row.env_json) if row.env_json else {},
        "contained": row.contained,
        "reversible": row.reversible,
        "trusted": row.trusted,
        "tool_overrides": json.loads(row.tool_overrides_json) if row.tool_overrides_json else {},
        "created_at": row.created_at,
    }
