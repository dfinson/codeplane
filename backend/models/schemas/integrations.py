"""Operator-facing registration contract for non-executing integration history."""

from __future__ import annotations

from pydantic import Field

from backend.models.schemas.base import CamelModel


class RegisterObservationRequest(CamelModel):
    binding_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    format_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    external_session_id: str = Field(min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=512)
    repo: str | None = Field(default=None, max_length=4096)
    base_ref: str | None = Field(default=None, max_length=256)
