"""Base classes for tool parameters."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BaseParams(BaseModel):
    """Base class for all tool parameters.

    Includes common fields like session_id per Spec §23.4.
    Uses extra="forbid" to reject unknown fields with clear errors.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    gate_token: str | None = Field(
        default=None,
        description="Gate confirmation token from a previous gate block. Required when responding to a gate.",
    )
    gate_reason: str | None = Field(
        default=None,
        description="Justification for passing the gate. Required when responding to a gate.",
    )
