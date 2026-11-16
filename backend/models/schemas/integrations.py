"""Operator-facing registration contract for non-executing integration history."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator

from backend.models.schemas.base import CamelModel
from backend.models.session_event import CanonicalSessionEvent  # noqa: TC001 - Pydantic resolves at runtime.


class RegisterObservationRequest(CamelModel):
    binding_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    format_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    external_session_id: str = Field(min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=512)
    repo: str | None = Field(default=None, max_length=4096)
    base_ref: str | None = Field(default=None, max_length=256)


class ObservationRecordBatch(CamelModel):
    source_key: str = Field(min_length=1, max_length=128)
    start_index: int = Field(ge=0)
    prefix_sha256: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    records: list[dict[str, Any]] = Field(max_length=1000)
    end_session: bool = False

    @model_validator(mode="after")
    def require_records_or_end(self) -> Self:
        if not self.records and not self.end_session:
            raise ValueError("An empty batch must explicitly end the source session")
        return self


class ObservationRecordReceipt(CamelModel):
    accepted_records: int
    next_index: int
    prefix_sha256: str
    checkpoint_version: int
    replay: bool = False
    projection_version: int | None = None
    session_closed: bool = False


class ObservationEventList(CamelModel):
    events: list[CanonicalSessionEvent]
    next_cursor: int
