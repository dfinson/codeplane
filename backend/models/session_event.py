"""Application-owned event contract, compatible with TraceForge 0.1.5 history.

Legacy field definitions derive from dfinson/traceforge at 46e0340.
See backend/compat/traceforge.LICENSE. No vendor runtime is imported here.
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003 - Pydantic resolves the event schema at runtime.
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class ToolMotivation(_FrozenModel):
    intent: str | None = None
    reasoning: str | None = None
    source_event_ids: tuple[str, ...] = Field(default_factory=tuple)


class FileTarget(_FrozenModel):
    raw_path: str
    path: str
    inside_root: bool | None = None


class PhaseSegment(_FrozenModel):
    phase: str
    actions: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()


class EventClassification(_FrozenModel):
    """Read-compatible historical labels, not permission authority."""

    mechanism: str
    effect: str | None = None
    scope: frozenset[str] = frozenset()
    role: frozenset[str] = frozenset()
    action: frozenset[str] = frozenset()
    capability: frozenset[str] = frozenset()
    structure: frozenset[str] = frozenset()
    source_labels: frozenset[str] = frozenset()
    shell_dialect: str | None = None
    binaries: tuple[str, ...] = ()
    phase_map: tuple[PhaseSegment, ...] = ()


class EventMetadata(_FrozenModel):
    source_framework: str | None = None
    ingestion_mode: Literal["stream", "file_watch", "poll", "replay", "sqlite"] | None = None
    raw_kind: str | None = None
    span_id: str | None = None
    parent_id: str | None = None
    correlation_id: str | None = None
    run_id: str | None = None
    sequence: int | None = Field(default=None, ge=0)
    namespace: tuple[str, ...] | None = None
    partial: bool = False
    repo: str | None = None
    turn_id: str | None = None
    visibility: Literal["visible", "system", "collapsed"] = "visible"
    phases: frozenset[str] | None = None
    phase: str | None = None
    boundary: str | None = None
    activity_id: str | None = None
    step_id: str | None = None
    activity_title: str | None = None
    step_title: str | None = None
    classification: EventClassification | None = None
    tool_display: str | None = None
    motivation: ToolMotivation | None = None
    duration_ms: float | None = None
    file_targets: tuple[FileTarget, ...] = Field(default_factory=tuple)
    governance: dict[str, Any] | None = None
    integration_binding_id: str | None = None
    execution_epoch: str | None = None
    execution_token: str | None = None
    recipe_run_id: str | None = None
    recipe_revision: int | None = None
    step_instance_id: str | None = None
    attempt_id: str | None = None

    @field_validator("duration_ms")
    @classmethod
    def _duration_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("duration_ms must be non-negative")
        return value


class CanonicalSessionEvent(_FrozenModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: str
    session_id: str
    timestamp: datetime
    payload: dict[str, Any]
    raw_event: dict[str, Any] | None = None
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_from_legacy_model(cls, value: Any) -> Any:
        if isinstance(value, BaseModel) and not isinstance(value, EventMetadata):
            return value.model_dump(mode="json")
        return value

    @property
    def sequence(self) -> int | None:
        return self.metadata.sequence


SessionEvent = CanonicalSessionEvent
