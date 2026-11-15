"""Durable tracker delivery contracts, independent of provider credentials."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves these annotations at runtime
from enum import StrEnum
from typing import Literal

from pydantic import Field, computed_field, field_validator

from backend.models.schemas.base import CamelModel


class TrackerOperationState(StrEnum):
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    dispatching = "dispatching"
    applied = "applied"
    rejected = "rejected"
    failed = "failed"
    uncertain = "uncertain"
    not_applied = "not_applied"


TrackerTargetKind = Literal["comment", "github_issue_state", "jira_workflow_transition", "azure_devops_work_item_state"]


class TrackerProviderReceipt(CamelModel):
    """Only allowlisted acknowledgement fields, never response bodies or headers."""

    id: str | None = None
    url: str | None = None
    http_status: int | None = None


class TrackerApprovalSnapshot(CamelModel):
    id: str
    job_id: str
    description: str
    proposed_action: str | None
    requested_at: datetime
    resolved_at: datetime | None = None
    resolution: Literal["approved", "rejected"] | None = None
    requires_explicit_approval: bool
    notes: str | None = None


class TrackerReconciliation(CamelModel):
    resolution: Literal["applied", "not_applied"]
    evidence: str
    actor: str
    recorded_at: datetime


class TrackerOperationResponse(CamelModel):
    id: str
    source_key: str
    project_id: str | None
    job_id: str | None
    task_link_id: str | None
    project_ref: str
    job_ref: str
    task_link_ref: str | None
    tracker_link_id: str | None
    tracker_link_ref: str
    provider: str
    base_url: str
    external_ref: str
    credential_id_ref: str
    ticket_ref: str
    action: Literal["comment", "transition"]
    target_kind: TrackerTargetKind
    value: str
    normalized_value: str | None
    approval_id: str | None = None
    approval_ref: str | None = None
    approval_snapshot: TrackerApprovalSnapshot | None = None
    state: TrackerOperationState
    claim_token: str | None = None
    claim_expires_at: datetime | None = None
    version: int = Field(ge=0)
    retry_of_id: str | None = None
    provider_receipt: TrackerProviderReceipt | None = None
    error_code: str | None = None
    error_detail: str | None = None
    reconciliation: TrackerReconciliation | None = None
    created_at: datetime
    updated_at: datetime
    dispatch_started_at: datetime | None = None
    completed_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def operation_id(self) -> str:
        return self.id

    @computed_field  # type: ignore[prop-decorator]
    @property
    def normalized_target(self) -> str | None:
        return self.normalized_value


class TrackerOperationListResponse(CamelModel):
    items: list[TrackerOperationResponse]


class ReconcileTrackerOperationRequest(CamelModel):
    expected_version: int = Field(ge=0)
    resolution: Literal["applied", "not_applied"]
    evidence: str = Field(min_length=1, max_length=4000)

    @field_validator("evidence")
    @classmethod
    def _require_evidence(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Reconciliation requires delivery evidence")
        return value


class RetryTrackerOperationRequest(CamelModel):
    expected_version: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=200)

    @field_validator("request_id")
    @classmethod
    def _require_request_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Retry requires a caller request identity")
        return value
