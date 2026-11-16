"""Immutable admission contracts, separate from live execution fences."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identity = Annotated[str, Field(min_length=1, max_length=256)]


class ImplementationPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identity
    sha256: Digest


class ExecutionContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identity
    policy: ImplementationPin
    parser: ImplementationPin
    grouping: ImplementationPin
    title: ImplementationPin
    grammars: tuple[ImplementationPin, ...] = Field(default=(), max_length=64)
    mappings: tuple[ImplementationPin, ...] = Field(default=(), max_length=64)

    @field_validator("grammars", "mappings")
    @classmethod
    def unique_implementations(cls, values: tuple[ImplementationPin, ...]) -> tuple[ImplementationPin, ...]:
        if len({value.id for value in values}) != len(values):
            raise ValueError("Implementation identities must be unique within a contract")
        return tuple(sorted(values, key=lambda value: value.id))


class ExecutionContractPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: Identity
    admission_id: Identity
    budget_id: Identity
    contract: ExecutionContract


class ExecutionAdmission(BaseModel):
    """A declared legacy invocation, not a successful execution or SDK session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identity
    job_id: Identity
    origin: Literal["creation", "continuation"]
    previous_admission_id: Identity | None = None
