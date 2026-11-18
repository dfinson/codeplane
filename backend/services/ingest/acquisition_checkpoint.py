"""Internal acquisition metadata committed with an ordinary observation batch."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.services.ingest.mapping import canonical_record
from backend.services.ingest.otel_decoder import decode_json_body


class AcquisitionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    source_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_checkpoint_version: int = Field(ge=0)
    state: dict[str, Any]

    @model_validator(mode="after")
    def bounded_metadata(self) -> AcquisitionCheckpoint:
        # Reject non-finite numbers and use the same structure/size limits as
        # acquired JSON before any decoder or durable cursor can advance.
        encoded = json.dumps(self.model_dump(), allow_nan=False).encode("utf-8")
        decode_json_body(encoded)
        return self

    def saved(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_identity_sha256": self.source_identity_sha256,
            "state": self.state,
        }

    def receipt_digest(self) -> str:
        return hashlib.sha256(canonical_record(self.model_dump())).hexdigest()
