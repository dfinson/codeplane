"""Operator-facing registration contract for non-executing integration history."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from backend.models.schemas.base import CamelModel
from backend.models.session_event import CanonicalSessionEvent  # noqa: TC001 - Pydantic resolves at runtime.
from backend.validators import INTEGRATION_BINDING_RE, validate_integration_binding_id


class RegisterObservationRequest(CamelModel):
    binding_id: str = Field(min_length=1, max_length=128, pattern=INTEGRATION_BINDING_RE.pattern)
    project_id: str = Field(min_length=1, max_length=128)
    format_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    external_session_id: str = Field(min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=512)
    repo: str | None = Field(default=None, max_length=4096)
    base_ref: str | None = Field(default=None, max_length=256)

    @field_validator("binding_id")
    @classmethod
    def addressable_binding(cls, value: str) -> str:
        return validate_integration_binding_id(value)


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


class IssueIngestCredentialRequest(CamelModel):
    enabled: bool = False


class IssuedIngestCredential(CamelModel):
    token: str
    enabled: bool
    header: str = "x-codeplane-ingest-token"


class OtlpExportResponse(CamelModel):
    """Empty successful OTLP ExportTraceServiceResponse (no partial acceptance)."""


class OtlpTraceRequest(CamelModel):
    """OTLP JSON envelope. Typed attributes/spans are validated by the bounded codec."""

    resource_spans: list[dict[str, Any]]


class IntegrationDependencyReadiness(CamelModel):
    ready: bool
    reason: str | None = None
    installed_version: str | None = None


class IntegrationOperation(CamelModel):
    id: str
    supported: bool = True
    implemented: bool
    producer: str | None = None
    dependency: IntegrationDependencyReadiness
    validation_status: Literal["not-qualified", "local-fixture-qualified"] = "not-qualified"
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class IntegrationProfileResponse(CamelModel):
    profile_id: str
    name: str
    adapter_id: str | None
    provider_family: str
    acceptance_kind: str
    descriptor_version: int = 1
    supported_versions: list[str]
    native_formats: dict[str, str]
    hooks: list[str]
    observation_channels: list[str]
    declared_ownership: str
    declared_steering: str
    declared_cancellation: str
    declared_interception: str
    identity_sources: list[str]
    usage_units: list[str]
    utility_completion: bool
    operations: list[IntegrationOperation]
    admission_enabled: bool
    binding_enabled: bool | None = None
    live_ready: bool = False
    live_readiness_reason: str = "Discovery supplies no live binding identity or capability acknowledgment"
    historical_import_available: bool
    source_reference: str


class IntegrationProfileList(CamelModel):
    specification_sha256: str
    profiles: list[IntegrationProfileResponse]
