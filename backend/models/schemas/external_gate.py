"""Execution-bound external permission protocol, separate from event ingestion."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from backend.models.schemas.base import CamelModel


class ExternalGatePhase(StrEnum):
    pending = "pending"
    allowed = "allowed"
    denied = "denied"
    retired = "retired"
    consumed = "consumed"


class ExternalGateSourceRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")
    execution_token: str = Field(min_length=1, max_length=128)
    attempt_id: str | None = Field(default=None, max_length=128)
    external_session_id: str | None = Field(default=None, max_length=512)


class ExternalGateSourceResponse(CamelModel):
    source_id: str
    credential: str
    job_id: str
    execution_binding_id: str
    execution_epoch: str
    execution_token: str


class ExternalGatePreflight(CamelModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    tool_name: str = Field(min_length=1, max_length=128)
    tool_input: dict[str, Any] | str
    target: str | None = Field(default=None, max_length=4096)
    session_id: str | None = Field(default=None, max_length=512)
    tool_call_id: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def bounded_action(self) -> Self:
        encoded = json.dumps(self.model_dump(), allow_nan=False, ensure_ascii=False).encode()
        if len(encoded) > 65536:
            raise ValueError("External gate action exceeds 64 KiB")
        return self


class ExternalGateConsume(ExternalGatePreflight):
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExternalGateDecision(CamelModel):
    source_id: str
    request_id: str
    action_digest: str
    phase: ExternalGatePhase
    decision: Literal["allow", "deny"] = "deny"
    reason: str
    approval_id: str | None = None


class ExternalGateReport(CamelModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    phase: Literal["started", "completed", "failed"]
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_report(self) -> Self:
        if len(json.dumps(self.details, allow_nan=False, ensure_ascii=False).encode()) > 65536:
            raise ValueError("External execution observation exceeds 64 KiB")
        return self
