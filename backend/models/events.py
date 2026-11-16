"""Canonical internal event model."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict

from backend.models.session_event import EventMetadata, SessionEvent

if TYPE_CHECKING:
    from backend.models.api_schemas import ExecutionPhase
    from backend.models.domain import (
        ApprovalResolution,
        GitMergeOutcome,
        JobState,
        Resolution,
    )

__all__ = [
    "TRANSCRIPT_KINDS",
    "TRANSCRIPT_STREAMING_KINDS",
    "EventKind",
    "EventMetadata",
    "EventPayload",
    "SessionEvent",
    "new_event",
]


class EventKind(StrEnum):
    """CodePlane control-plane event kinds as open dotted TraceForge kinds.

    These are CodePlane's own kinds expressed in TraceForge's open dotted-string
    ``kind`` grammar. ``traceforge.SessionEvent.kind`` is a free-form string; this
    enum is just symbol-safe named constants for the kinds CodePlane emits — not a
    translation layer. The wire/persistence carry these dotted values verbatim.
    """

    # --- Job lifecycle ---
    job_created = "job.created"
    job_setup_progress = "job.setup_progress"
    workspace_prepared = "workspace.prepared"
    agent_session_started = "agent.session_started"
    # --- Transcript family (dotted vendor kinds, emitted natively by both
    #     producers: managed adapters + imported TF mappings) ---
    message_user = "message.user"
    message_assistant = "message.assistant"
    message_system = "message.system"
    message_delta = "message.delta"
    # Unified TF reasoning lifecycle (traceforge-toolkit >=0.1.2): both managed
    # producers and the bundled claude/copilot mappings emit ``llm.reasoning.chunk``
    # for model thinking/reasoning. The started/completed/failed boundaries are
    # registered for forward-compat with other frameworks CP may later ingest;
    # CP's live path only emits ``llm.reasoning.chunk``.
    llm_reasoning_started = "llm.reasoning.started"
    llm_reasoning_chunk = "llm.reasoning.chunk"
    llm_reasoning_completed = "llm.reasoning.completed"
    llm_reasoning_failed = "llm.reasoning.failed"
    planning_started = "planning.started"
    tool_call_started = "tool.call.started"
    tool_call_completed = "tool.call.completed"
    tool_call_failed = "tool.call.failed"
    tool_result_chunk = "tool.result.chunk"
    # --- Imported/native session lifecycle. ``session.ended``/``session.error``
    #     are consumed by the ingest sources (services/ingest/_base.py) for
    #     finalization; the remaining kinds are forward-compat/pass-through TF
    #     lifecycle vocabulary, not consumed by CP's live path. ---
    session_started = "session.started"
    session_ended = "session.ended"
    session_error = "session.error"
    session_idle = "session.idle"
    session_abort = "session.abort"
    turn_started = "turn.started"
    turn_ended = "turn.ended"
    permission_granted = "permission.granted"
    # --- Telemetry (native TF usage record) ---
    telemetry_usage = "telemetry.usage"
    # --- File edits (native TF file kind; drives diff recalculation) ---
    file_edited = "file.edited"
    # --- Logs / diffs ---
    log_line_emitted = "log"
    diff_updated = "diff.updated"
    # --- Approvals (permissions) ---
    approval_requested = "permission.requested"
    approval_resolved = "permission.resolved"
    batch_approval_requested = "permission.batch.requested"
    batch_approval_resolved = "permission.batch.resolved"
    # --- Job state / outcomes ---
    job_review = "job.review"
    job_completed = "job.completed"
    job_failed = "job.failed"
    job_canceled = "job.canceled"
    job_state_changed = "job.state_changed"
    session_heartbeat = "session.heartbeat"
    merge_completed = "merge.completed"
    merge_conflict = "merge.conflict"
    session_resumed = "session.resumed"
    job_resolved = "job.resolved"
    job_archived = "job.archived"
    job_title_updated = "job.title_updated"
    progress_headline = "progress.headline"
    model_downgraded = "model.downgraded"
    model_observed = "model.observed"
    tool_group_summary = "tool.group_summary"
    agent_plan_updated = "plan.updated"
    execution_phase_changed = "execution.phase_changed"
    telemetry_updated = "telemetry.updated"
    artifacts_updated = "artifacts.updated"
    tracker_operation_updated = "tracker.operation_updated"
    # --- Steps / plan ---
    step_started = "step.started"
    step_completed = "step.completed"
    step_title_generated = "step.title_generated"
    step_group_updated = "step.group_updated"
    plan_step_updated = "plan.step_updated"
    step_entries_reassigned = "step.entries_reassigned"
    turn_summary = "turn.summary"
    verification_started = "verification.started"
    verification_result = "verification.result"
    action_classified = "action.classified"
    policy_settings_changed = "policy.settings_changed"
    # --- Repo index ---
    # --- Structural health ---
    stall_detected = "stall.detected"
    # --- Monitors ---
    monitor_approved = "monitor.approved"
    monitor_rejected = "monitor.rejected"
    monitor_escalated = "monitor.escalated"
    # --- Sidecars ---
    sidecar_transcript = "sidecar.transcript"
    sidecar_agent_message = "sidecar.agent_message"
    sidecar_gate_verdict = "sidecar.gate_verdict"
    sidecar_metadata_update = "sidecar.metadata_update"
    # --- Unified secondary session events (preflight, sidecars, monitors) ---
    secondary_session_started = "secondary_session.started"
    secondary_session_entry = "secondary_session.entry"
    secondary_session_completed = "secondary_session.completed"
    # --- Context handoff — emitted when context crosses a session boundary ---
    context_handoff = "context.handoff"
    job_mode_changed = "job.mode_changed"
    # --- Internal housekeeping (not user-visible) ---
    reenrich_complete = "internal.reenrich_complete"


# The transcript family: dotted vendor kinds that represent conversation/tool
# activity to display and step-track. Both producers (managed adapters, imported
# TF mappings) emit these dotted kinds natively — consumers branch on ``kind``,
# never on a ``role`` field. ``message.delta`` and ``tool.result.chunk`` are
# streaming partials (skipped by step tracking); the rest are complete blocks.
TRANSCRIPT_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.message_user,
        EventKind.message_assistant,
        EventKind.message_system,
        EventKind.message_delta,
        EventKind.llm_reasoning_started,
        EventKind.llm_reasoning_chunk,
        EventKind.llm_reasoning_completed,
        EventKind.llm_reasoning_failed,
        EventKind.tool_call_started,
        EventKind.tool_call_completed,
        EventKind.tool_call_failed,
        EventKind.tool_result_chunk,
    }
)

# Streaming partials within the transcript family — display them, but do not
# advance step/turn tracking on them (they are token-by-token fragments).
TRANSCRIPT_STREAMING_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.message_delta,
        EventKind.llm_reasoning_chunk,
        EventKind.tool_result_chunk,
    }
)


def is_admission_receipt(event: SessionEvent) -> bool:
    return bool(event.payload.get("admission_status") or event.payload.get("execution_status") == "unconfirmed")


# ---------------------------------------------------------------------------
# Typed event payloads
#
# These TypedDicts describe the *most common* payload shapes emitted by the
# service layer and consumed by SSE builders / API endpoints.  They are not
# enforced at publish time (payloads are still plain dicts internally) but
# give type-checkers enough information to validate consumer code.
# ---------------------------------------------------------------------------


class _BasePayload(TypedDict, total=False):
    """Fields injected at runtime by RuntimeService (step tracking, session tagging)."""

    step_id: str | None
    step_number: int | None
    session_number: int
    turn_id: str | None


class JobSetupProgressPayloadDict(_BasePayload, total=False):
    step: str


class JobCanceledPayloadDict(_BasePayload, total=False):
    reason: str


class StepEntriesReassignedPayloadDict(_BasePayload, total=False):
    old_step_id: str
    new_step_id: str


class EmptyPayloadDict(_BasePayload):
    """Payload for events that carry no data (e.g. job_archived)."""


class LogLinePayloadDict(_BasePayload, total=False):
    seq: int
    timestamp: str
    level: str
    message: str
    context: dict[str, Any] | None


class TranscriptPayloadDict(_BasePayload, total=False):
    """TF-native transcript/tool payload.

    Dotted ``kind`` (on the event) replaces the old ``role`` field. Tool fields
    use TF names (``arguments``/``result``/``success``). Presentation/motivation
    enrichment (``tool_display``/intent/``duration_ms``) lives on
    ``event.metadata`` (``tool_display``/``motivation``/``duration_ms``), not here.
    """

    seq: int
    timestamp: str
    content: str
    title: str | None
    tool_name: str | None
    tool_call_id: str | None
    arguments: Any
    result: str | None
    success: bool | None


class DiffFilePayloadDict(TypedDict, total=False):
    path: str
    status: str
    additions: int
    deletions: int
    hunks: list[dict[str, Any]]
    write_count: int | None
    retry_count: int | None


class DiffPayloadDict(_BasePayload, total=False):
    changed_files: list[DiffFilePayloadDict]


class ApprovalRequestedPayloadDict(_BasePayload, total=False):
    approval_id: str
    description: str
    proposed_action: str | None
    timestamp: str


class ApprovalResolvedPayloadDict(_BasePayload, total=False):
    approval_id: str
    resolution: ApprovalResolution
    timestamp: str


class JobStatePayloadDict(_BasePayload, total=False):
    state: JobState
    new_state: JobState
    previous_state: JobState | None


class JobReviewPayloadDict(_BasePayload, total=False):
    pr_url: str | None
    merge_status: GitMergeOutcome | None
    resolution: Resolution | None
    model_downgraded: bool
    requested_model: str | None
    actual_model: str | None


class JobCompletedPayloadDict(_BasePayload, total=False):
    resolution: Resolution | None
    merge_status: GitMergeOutcome | None
    pr_url: str | None


class JobFailedPayloadDict(_BasePayload, total=False):
    reason: str


class SessionHeartbeatPayloadDict(_BasePayload, total=False):
    session_id: str
    timestamp: str
    last_activity_at: str
    active_tool_name: str
    active_tool_since: str


class MergeCompletedPayloadDict(_BasePayload, total=False):
    branch: str
    base_ref: str
    strategy: str
    timestamp: str


class MergeConflictPayloadDict(_BasePayload, total=False):
    branch: str
    base_ref: str
    conflict_files: list[str]
    fallback: str
    pr_url: str | None
    timestamp: str


class SessionResumedPayloadDict(_BasePayload, total=False):
    timestamp: str


class JobResolvedPayloadDict(_BasePayload, total=False):
    resolution: Resolution
    pr_url: str | None
    conflict_files: list[str] | None
    error: str | None


class JobTitleUpdatedPayloadDict(_BasePayload, total=False):
    title: str | None
    branch: str | None
    description: str | None


class ProgressHeadlinePayloadDict(_BasePayload, total=False):
    headline: str
    headline_past: str
    summary: str
    replaces_count: int


class ModelDowngradedPayloadDict(_BasePayload, total=False):
    requested_model: str
    actual_model: str


class ToolGroupSummaryPayloadDict(_BasePayload, total=False):
    summary: str


class AgentPlanStepDict(TypedDict, total=False):
    label: str
    status: str


class AgentPlanUpdatedPayloadDict(_BasePayload, total=False):
    steps: list[AgentPlanStepDict]


class ExecutionPhasePayloadDict(_BasePayload, total=False):
    phase: ExecutionPhase


class TelemetryUpdatedPayloadDict(_BasePayload, total=False):
    job_id: str
    total_cost_usd: float
    total_tokens: int
    input_tokens: int
    output_tokens: int


class StepStartedPayloadDict(_BasePayload, total=False):
    intent: str
    trigger: str
    timing_source: str
    source_started_at: str | None


class StepCompletedPayloadDict(_BasePayload, total=False):
    status: str
    tool_count: int
    duration_ms: int | None
    timing_source: str
    source_started_at: str | None
    source_completed_at: str | None
    has_summary: bool
    agent_message: str | None
    files_read: list[str]
    files_written: list[str]
    tool_names: list[str]
    start_sha: str | None
    end_sha: str | None
    preceding_context: str | None
    diff_additions: int
    diff_deletions: int
    worktree_path: str | None


class StepTitlePayloadDict(_BasePayload, total=False):
    title: str


class PlanStepUpdatedPayloadDict(_BasePayload, total=False):
    plan_step_id: str
    label: str
    summary: str | None
    status: str
    tool_count: int
    files_written: list[str]
    started_at: str | None
    completed_at: str | None
    duration_ms: int | None
    start_sha: str | None
    end_sha: str | None


class TurnSummaryPayloadDict(_BasePayload, total=False):
    title: str
    activity_id: str
    activity_label: str
    activity_status: str  # active | done
    is_new_activity: bool
    plan_item_id: str | None


# Union of all known payload shapes.  Used as the DomainEvent.payload type so
# consumers get useful type information.
EventPayload = (
    LogLinePayloadDict
    | TranscriptPayloadDict
    | DiffPayloadDict
    | ApprovalRequestedPayloadDict
    | ApprovalResolvedPayloadDict
    | JobSetupProgressPayloadDict
    | JobStatePayloadDict
    | JobReviewPayloadDict
    | JobCompletedPayloadDict
    | JobFailedPayloadDict
    | JobCanceledPayloadDict
    | SessionHeartbeatPayloadDict
    | MergeCompletedPayloadDict
    | MergeConflictPayloadDict
    | SessionResumedPayloadDict
    | JobResolvedPayloadDict
    | JobTitleUpdatedPayloadDict
    | ProgressHeadlinePayloadDict
    | ModelDowngradedPayloadDict
    | ToolGroupSummaryPayloadDict
    | AgentPlanUpdatedPayloadDict
    | ExecutionPhasePayloadDict
    | TelemetryUpdatedPayloadDict
    | StepStartedPayloadDict
    | StepCompletedPayloadDict
    | StepTitlePayloadDict
    | PlanStepUpdatedPayloadDict
    | TurnSummaryPayloadDict
    | StepEntriesReassignedPayloadDict
    | EmptyPayloadDict
    | dict[str, Any]
)


def new_event(
    session_id: str | None,
    kind: EventKind | str,
    payload: EventPayload | dict[str, Any],
    *,
    timestamp: datetime | None = None,
    metadata: EventMetadata | None = None,
    sequence: int | None = None,
    event_id: str | None = None,
    raw_event: dict[str, Any] | None = None,
) -> SessionEvent:
    """Construct a canonical ``traceforge.SessionEvent`` for a CodePlane job.

    Single construction point that replaces the retired ``DomainEvent`` dataclass.
    ``session_id`` carries the CodePlane job id (``""`` for job-less/global events).
    ``sequence`` is optional producer-stream order and is never a persistence cursor.
    ``timestamp`` and event ``id`` are auto-filled when omitted, matching the old
    ``DomainEvent.for_job`` convenience.
    """
    if metadata is None:
        metadata = EventMetadata(sequence=sequence)
    elif sequence is not None:
        metadata = metadata.model_copy(update={"sequence": sequence})
    fields: dict[str, Any] = {
        "kind": str(kind),
        "session_id": session_id or "",
        "timestamp": timestamp if timestamp is not None else datetime.now(UTC),
        "payload": dict(payload),
        "metadata": metadata,
        "raw_event": raw_event,
    }
    if event_id is not None:
        fields["id"] = event_id
    return SessionEvent(**fields)
