"""Tests for snapshot_helpers — build functions for snapshot assembly."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.models.domain import Approval, ApprovalResolution
from backend.models.events import EventKind
from backend.models.session_event import EventMetadata, ToolMotivation
from backend.services.artifacts.snapshot_helpers import (
    _apply_reassignments,
    _build_approvals,
    _build_logs,
    _build_timeline,
    _build_transcript,
    _build_turn_summaries,
)


@pytest.mark.parametrize("resolution", [None, ApprovalResolution.approved, ApprovalResolution.rejected])
@pytest.mark.parametrize("explicit", [True, False])
async def test_snapshot_preserves_explicit_approval_marker_and_notes(
    resolution: ApprovalResolution | None, explicit: bool
) -> None:
    now = datetime.now(UTC)
    approval = Approval(
        id="hard-gate",
        job_id="job-1",
        description="Fixture hard gate",
        proposed_action="fixture",
        requested_at=now,
        resolved_at=now if resolution else None,
        resolution=resolution,
        requires_explicit_approval=explicit,
        notes="Recorded restart supersession" if resolution else None,
    )
    repository = AsyncMock()
    repository.list_for_job.return_value = [approval]
    snapshot = await _build_approvals(repository, "job-1")
    wire = snapshot[0].model_dump(mode="json", by_alias=True)
    assert wire["requiresExplicitApproval"] is explicit
    assert wire["notes"] == approval.notes


def _event(
    job_id: str = "j1",
    event_id: str = "evt-1",
    timestamp: str = "2025-01-01T00:00:00Z",
    kind: EventKind = EventKind.log_line_emitted,
    metadata: EventMetadata | None = None,
    **payload: Any,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=event_id,
        session_id=job_id,
        timestamp=timestamp,
        kind=kind,
        payload=payload,
        metadata=metadata or EventMetadata(),
    )


# ---------------------------------------------------------------------------
# _build_logs
# ---------------------------------------------------------------------------


class TestBuildLogs:
    def test_empty(self) -> None:
        assert _build_logs([]) == []

    def test_extracts_fields(self) -> None:
        ev = _event(
            seq=1,
            timestamp="2025-01-01T00:00:01Z",
            level="error",
            message="something broke",
            context={"key": "val"},
        )
        logs = _build_logs([ev])
        assert len(logs) == 1
        assert logs[0].seq == 1
        assert logs[0].level == "error"
        assert logs[0].message == "something broke"
        assert logs[0].context == {"key": "val"}

    def test_defaults(self) -> None:
        ev = _event()
        logs = _build_logs([ev])
        assert logs[0].seq == 0
        assert logs[0].level == "info"
        assert logs[0].message == ""


# ---------------------------------------------------------------------------
# _build_transcript
# ---------------------------------------------------------------------------


class TestBuildTranscript:
    def test_failed_tool_keeps_scope_identity_and_error_even_when_normally_hidden(self) -> None:
        metadata = EventMetadata(
            integration_binding_id="binding", run_id="source", namespace=("observation-source", "events"),
        )
        failure = _event(
            kind=EventKind.tool_call_failed, tool_name="report_intent", tool_call_id="call",
            error={"message": "Denied"}, result={"detail": "No execution proof"}, metadata=metadata,
        )
        transcript = _build_transcript([failure], [], None, None, filter_deltas=True)
        assert len(transcript) == 1
        assert transcript[0].kind == "tool.call.failed"
        assert transcript[0].success is False
        assert transcript[0].tool_call_id == "call"
        assert transcript[0].metadata == metadata
        assert transcript[0].tool_issue == '{"message": "Denied"}'
        assert transcript[0].result == '{"detail": "No execution proof"}'

    def test_preserves_canonical_event_identity_and_optional_sequence(self) -> None:
        sequenced = _event(
            event_id="evt-sequenced",
            kind=EventKind.message_assistant,
            metadata=EventMetadata(sequence=10_000),
            seq=41,
            content="Sequenced",
        )
        unsequenced = _event(
            event_id="evt-unsequenced",
            kind=EventKind.message_assistant,
            metadata=EventMetadata(),
            seq=42,
            content="Unsequenced",
        )

        transcript = _build_transcript(
            [sequenced, unsequenced],
            [],
            None,
            None,
            filter_deltas=True,
        )

        assert [(item.event_id, item.sequence) for item in transcript] == [
            ("evt-sequenced", 10_000),
            ("evt-unsequenced", None),
        ]
        assert all("seq" not in item.model_fields_set for item in transcript)

    def test_orders_by_canonical_sequence_when_every_event_has_one(self) -> None:
        later_storage_event = _event(
            event_id="evt-first-row",
            kind=EventKind.message_assistant,
            metadata=EventMetadata(sequence=20),
            seq=1,
            content="Later producer event",
        )
        earlier_storage_event = _event(
            event_id="evt-second-row",
            kind=EventKind.message_assistant,
            metadata=EventMetadata(sequence=10),
            seq=2,
            content="Earlier producer event",
        )

        transcript = _build_transcript(
            [later_storage_event, earlier_storage_event],
            [],
            None,
            None,
            filter_deltas=True,
        )

        assert [item.event_id for item in transcript] == [
            "evt-second-row",
            "evt-first-row",
        ]

    def test_builds_tool_call_from_tf_fields_and_metadata(self) -> None:
        ev = _event(
            kind=EventKind.tool_call_completed,
            tool_name="Bash",
            arguments={"command": "echo hi"},
            result="hi",
            success=True,
            turn_id="t1",
            metadata=EventMetadata(
                tool_display="Run echo hi",
                duration_ms=12.5,
                motivation=ToolMotivation(intent="Verify shell"),
            ),
        )

        transcript = _build_transcript([ev], [], None, None, filter_deltas=True)

        assert len(transcript) == 1
        assert transcript[0].kind == EventKind.tool_call_completed
        assert transcript[0].arguments == '{"command": "echo hi"}'
        assert transcript[0].result == "hi"
        assert transcript[0].success is True
        assert transcript[0].tool_intent == "Verify shell"
        assert transcript[0].tool_display == "Run echo hi"
        assert transcript[0].tool_duration_ms == 12

    def test_filters_hidden_tool_events(self) -> None:
        ev = _event(
            kind=EventKind.tool_call_completed,
            tool_name="report_intent",
            arguments={"intent": "Internal bookkeeping"},
            result="ok",
            success=True,
        )

        assert _build_transcript([ev], [], None, None, filter_deltas=True) == []


# ---------------------------------------------------------------------------
# _build_timeline
# ---------------------------------------------------------------------------


class TestBuildTimeline:
    def test_empty(self) -> None:
        assert _build_timeline([]) == []

    def test_appends(self) -> None:
        events = [
            _event(headline="Step 1", headline_past="Did step 1", summary="s1"),
            _event(headline="Step 2", headline_past="Did step 2", summary="s2"),
        ]
        timeline = _build_timeline(events)
        assert len(timeline) == 2
        assert timeline[0].headline == "Step 1"
        assert timeline[1].headline == "Step 2"

    def test_replaces(self) -> None:
        events = [
            _event(headline="Step 1", headline_past="", summary=""),
            _event(headline="Step 2", headline_past="", summary=""),
            _event(headline="Step 3 (replaces 2)", headline_past="", summary="", replaces_count=1),
        ]
        timeline = _build_timeline(events)
        assert len(timeline) == 2
        assert timeline[0].headline == "Step 1"
        assert timeline[1].headline == "Step 3 (replaces 2)"

    def test_replaces_all(self) -> None:
        events = [
            _event(headline="A", headline_past="", summary=""),
            _event(headline="B", headline_past="", summary=""),
            _event(headline="C", headline_past="", summary="", replaces_count=99),
        ]
        timeline = _build_timeline(events)
        assert len(timeline) == 1
        assert timeline[0].headline == "C"


# ---------------------------------------------------------------------------
# _build_turn_summaries
# ---------------------------------------------------------------------------


class TestBuildTurnSummaries:
    def test_empty(self) -> None:
        assert _build_turn_summaries([], "j1", deduplicate=False) == []

    def test_basic(self) -> None:
        events = [
            _event(
                turn_id="t1",
                title="Fixed bug",
                activity_id="a1",
                activity_label="Debugging",
                activity_status="active",
                is_new_activity=True,
            ),
        ]
        result = _build_turn_summaries(events, "j1", deduplicate=False)
        assert len(result) == 1
        assert result[0].title == "Fixed bug"
        assert result[0].is_new_activity is True

    def test_deduplicate_keeps_latest(self) -> None:
        events = [
            _event(
                turn_id="t1",
                title="First",
                activity_id="a1",
                activity_label="L",
                activity_status="active",
                is_new_activity=True,
            ),
            _event(
                turn_id="t1",
                title="Updated",
                activity_id="a1",
                activity_label="L",
                activity_status="active",
                is_new_activity=False,
            ),
        ]
        result = _build_turn_summaries(events, "j1", deduplicate=True)
        assert len(result) == 1
        assert result[0].title == "Updated"
        # First event's is_new_activity is preserved
        assert result[0].is_new_activity is True

    def test_deduplicate_replaces_turn(self) -> None:
        events = [
            _event(
                turn_id="t1",
                title="Old",
                activity_id="a1",
                activity_label="L",
                activity_status="active",
                is_new_activity=True,
            ),
            _event(
                turn_id="t2",
                title="Merged",
                activity_id="a1",
                activity_label="L",
                activity_status="active",
                is_new_activity=False,
                replaces_turn_id="t1",
            ),
        ]
        result = _build_turn_summaries(events, "j1", deduplicate=True)
        # t1 is replaced; only t2 remains, but with inherited is_new_activity
        assert len(result) == 1
        assert result[0].turn_id == "t2"
        assert result[0].is_new_activity is True

    def test_skips_empty_title(self) -> None:
        events = [
            _event(
                turn_id="t1",
                title="",
                activity_id="a1",
                activity_label="L",
                activity_status="active",
                is_new_activity=False,
            ),
        ]
        result = _build_turn_summaries(events, "j1", deduplicate=False)
        assert len(result) == 0

    def test_skips_missing_turn_id(self) -> None:
        events = [
            _event(
                title="Orphan",
                activity_id="a1",
                activity_label="L",
                activity_status="active",
                is_new_activity=False,
            ),
        ]
        result = _build_turn_summaries(events, "j1", deduplicate=False)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# _apply_reassignments
# ---------------------------------------------------------------------------


class TestApplyReassignments:
    def test_empty(self) -> None:
        transcript: list[Any] = []
        _apply_reassignments(transcript, [])

    def test_reassigns_step_id(self) -> None:
        from backend.models.api_schemas import TranscriptPayload

        entry = TranscriptPayload(
            job_id="j1",
            event_id="evt-1",
            sequence=1,
            timestamp="2025-01-01T00:00:00Z",
            kind=EventKind.message_assistant,
            content="hi",
            turn_id="t1",
            step_id="old-step",
        )
        reassign_events = [
            _event(turn_id="t1", old_step_id="old-step", new_step_id="new-step"),
        ]
        _apply_reassignments([entry], reassign_events)
        assert entry.step_id == "new-step"

    def test_no_match(self) -> None:
        from backend.models.api_schemas import TranscriptPayload

        entry = TranscriptPayload(
            job_id="j1",
            event_id="evt-1",
            sequence=1,
            timestamp="2025-01-01T00:00:00Z",
            kind=EventKind.message_assistant,
            content="hi",
            turn_id="t2",
            step_id="old-step",
        )
        reassign_events = [
            _event(turn_id="t1", old_step_id="old-step", new_step_id="new-step"),
        ]
        _apply_reassignments([entry], reassign_events)
        assert entry.step_id == "old-step"
