"""Job-owned review records, not approvals or delivery commands."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from pydantic import Field, JsonValue

from backend.models.schemas.base import CamelModel
from backend.models.schemas.recipes import (
    RecipeArtifactReceipt,
    RecipeAttemptResponse,
    RecipeCodeReference,
    RecipeModel,
    RecipeStepInstanceResponse,
)


class PrepareReviewRequest(RecipeModel):
    expected_run_id: str | None = None
    attempt_ids: list[str] = Field(default_factory=list, max_length=20)
    event_ids: list[str] = Field(default_factory=list, max_length=100)


class ReviewAttemptSnapshot(RecipeModel):
    step: RecipeStepInstanceResponse
    attempt: RecipeAttemptResponse
    checksum: str


class ReviewArtifactSnapshot(RecipeModel):
    original_id: str
    retained: RecipeArtifactReceipt


class ReviewEventSnapshot(RecipeModel):
    event_id: str
    kind: str
    checksum: str
    content: dict[str, JsonValue]


class ReviewEvidenceSnapshot(RecipeModel):
    job_id: str
    job_version: int
    run_id: str | None
    run_version: int | None
    recipe_revision_id: str | None
    recipe_snapshot_checksum: str | None
    code: RecipeCodeReference
    code_head: str
    code_tree: str
    event_high_water: int
    attempts: list[ReviewAttemptSnapshot]
    events: list[ReviewEventSnapshot]
    artifacts: list[ReviewArtifactSnapshot]
    captured_at: datetime


class ReviewPreparation(RecipeModel):
    snapshot_artifact_id: str
    snapshot_checksum: str
    evidence: ReviewEvidenceSnapshot


class RecordReviewRequest(RecipeModel):
    request_key: str = Field(min_length=1, max_length=128)
    snapshot_artifact_id: str = Field(min_length=1, max_length=128)
    snapshot_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    rationale: str = Field(min_length=1, max_length=20_000)


class ReviewRecord(RecipeModel):
    event_id: str
    job_id: str
    request_key: str
    rationale: str
    snapshot_artifact_id: str
    snapshot_checksum: str
    evidence: ReviewEvidenceSnapshot
    recorded_at: datetime


class ReviewHistoryPage(CamelModel):
    items: list[ReviewRecord]
    next_cursor: int | None
    high_water: int
