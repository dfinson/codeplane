"""Base classes for tool parameters."""

from __future__ import annotations

from pydantic import BaseModel


class BaseParams(BaseModel):
    """Base class for all tool parameters.

    Includes common fields like session_id per Spec §23.4.
    """

    session_id: str | None = None
