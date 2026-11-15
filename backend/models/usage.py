"""Typed source usage. Native cost units and estimates are never verified USD."""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 - Pydantic resolves this field at runtime.
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UsageAmount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    unit: Literal[
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "premium_requests",
        "copilot_cost_unit",
        "USD",
    ]
    producer: str
    model: str | None
    job_id: str | None
    execution_epoch: str | None
    role: Literal["primary", "scout", "monitor", "assessment", "title", "utility"]
    source: str
    verified: bool = False
    currency: Literal["USD"] | None = None

    @model_validator(mode="after")
    def _currency_is_proven(self) -> Self:
        if self.unit == "USD":
            if not self.verified or self.currency != "USD":
                raise ValueError("Monetary usage requires explicitly verified USD provenance")
        elif self.currency is not None:
            raise ValueError("Native usage units cannot be labeled as currency")
        return self
