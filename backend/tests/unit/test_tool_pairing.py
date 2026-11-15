from datetime import UTC, datetime, timedelta

from backend.models.events import EventKind, new_event
from backend.models.session_event import EventMetadata, ToolMotivation
from backend.services.events.tool_pairing import ToolPairing


def test_pair_keeps_completion_identity_duration_and_start_motivation() -> None:
    pairing = ToolPairing()
    start_time = datetime.now(UTC)
    start = new_event(
        "job",
        EventKind.tool_call_started,
        {"tool_call_id": "call", "tool_name": "view", "arguments": {"path": "a"}},
        timestamp=start_time,
        metadata=EventMetadata(motivation=ToolMotivation(intent="Read implementation")),
    )
    assert pairing.process(start) is None
    complete = new_event(
        "job",
        EventKind.tool_call_completed,
        {"tool_call_id": "call", "result": "contents", "success": False},
        timestamp=start_time + timedelta(milliseconds=123),
    )
    result = pairing.process(complete)
    assert result.id == complete.id
    assert result.payload["success"] is False
    assert result.metadata.duration_ms == 123
    assert result.metadata.motivation.intent == "Read implementation"


def test_per_job_flush_and_checkpoint_do_not_consume_other_jobs() -> None:
    pairing = ToolPairing()
    for job in ("first", "second"):
        pairing.process(new_event(job, EventKind.tool_call_started, {"tool_call_id": "same", "tool_name": "view"}))
    checkpoint = pairing.checkpoint()
    restored = ToolPairing()
    restored.restore(checkpoint)
    assert [event.session_id for event in restored.flush_session("first")] == ["first"]
    complete = new_event("second", EventKind.tool_call_completed, {"tool_call_id": "same"})
    assert restored.process(complete).payload["tool_name"] == "view"


def test_missing_id_and_duplicate_call_identity_remain_visible_unresolved() -> None:
    pairing = ToolPairing(max_pending=1)
    missing = new_event("job", EventKind.tool_call_started, {"tool_call_id": ""})
    assert pairing.process(missing) is missing
    pairing.process(new_event("job", EventKind.tool_call_started, {"tool_call_id": "call"}))
    conflict = pairing.process(new_event("job", EventKind.tool_call_started, {"tool_call_id": "call"}))
    assert len(conflict) == 2
    assert all(event.payload["_codeplane_pairing"]["reason"] == "duplicate_start" for event in conflict)
