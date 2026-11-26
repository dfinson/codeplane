from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.models.events import EventKind, new_event
from backend.models.session_event import EventMetadata, ToolMotivation
from backend.services.events.tool_pairing import ToolPairing
from backend.services.ingest.mapping import NativeMappingDecoder


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
    assert pairing.process(missing).payload["_codeplane_pairing"]["reason"] == "missing_identity"
    pairing.process(new_event("job", EventKind.tool_call_started, {"tool_call_id": "call"}))
    conflict = pairing.process(new_event("job", EventKind.tool_call_started, {"tool_call_id": "call"}))
    assert len(conflict) == 2
    assert all(event.payload["_codeplane_pairing"]["reason"] == "duplicate_start" for event in conflict)


@pytest.mark.parametrize("duration", [None, 0])
def test_failed_tool_pairs_without_fabricating_duration_from_fallback_timestamps(duration) -> None:
    pairing = ToolPairing()
    source = {"timestamp_fallback": True}
    pairing.process(
        new_event(
            "job",
            EventKind.tool_call_started,
            {
                "tool_call_id": "call",
                "tool_name": "write",
                "arguments": {"path": "file"},
                "_codeplane_source": source,
            },
        )
    )
    failure = new_event(
        "job",
        EventKind.tool_call_failed,
        {"tool_call_id": "call", "error": "Denied", "_codeplane_source": source},
        metadata=EventMetadata(duration_ms=duration),
    )
    result = pairing.process(failure)
    assert result.id == failure.id and result.kind == "tool.call.failed"
    assert result.payload["tool_name"] == "write"
    assert result.payload["error"] == "Denied"
    assert result.metadata.duration_ms == duration
    assert pairing.checkpoint().starts == ()


def test_reused_native_session_and_call_ids_remain_scoped_by_registered_execution_token() -> None:
    pairing = ToolPairing()
    metadata = EventMetadata(run_id="reused-session", attempt_id="attempt", execution_epoch="epoch")
    for token in ("first", "second"):
        assert (
            pairing.process(
                new_event(
                    "job",
                    EventKind.tool_call_started,
                    {"tool_call_id": "same", "tool_name": token},
                    metadata=metadata.model_copy(update={"execution_token": token}),
                )
            )
            is None
        )
    result = pairing.process(
        new_event(
            "job",
            EventKind.tool_call_failed,
            {"tool_call_id": "same"},
            metadata=metadata.model_copy(update={"execution_token": "first"}),
        )
    )
    assert result.payload["tool_name"] == "first"
    assert len(pairing.checkpoint().starts) == 1
    assert pairing.checkpoint().starts[0].metadata.execution_token == "second"


@pytest.mark.parametrize("terminal", ["tool_completed", "tool_failed"])
def test_llama_app_operation_mapping_pairs_with_distinct_identity_and_source_domains(terminal):
    pairing = ToolPairing()
    path = Path(__file__).parents[2] / "services/adapters/profile_bridges/llamaindex.yaml"
    scopes = (
        ("binding-a", "epoch", "token"),
        ("binding-b", "epoch", "token"),
        ("binding-a", "other-epoch", "token"),
        ("binding-a", "epoch", "other-token"),
    )
    sources = []
    for index, (binding, epoch, token) in enumerate(scopes):
        decoder = NativeMappingDecoder(
            path,
            job_id="job",
            external_session_id="app-source",
            binding_id=binding,
            anchor=datetime.now(UTC),
            source_key="native-callbacks",
        )
        metadata = EventMetadata(
            source_framework="llamaindex",
            integration_binding_id=binding,
            execution_epoch=epoch,
            execution_token=token,
            namespace=("observation-source", "native-callbacks"),
        )
        (start,) = decoder.decode(
            {
                "type": "tool_started",
                "tool_name": "body",
                "operation_id": "same-operation",
                "arguments": {"value": index},
            },
            0,
        )
        assert pairing.process(start.model_copy(update={"metadata": metadata})) is None
        sources.append((decoder, metadata, index))
    native_start = new_event(
        "job",
        EventKind.tool_call_started,
        {"tool_call_id": "same-operation", "tool_name": "native"},
        metadata=sources[0][1],
    )
    assert pairing.process(native_start) is None
    restored = ToolPairing()
    restored.restore(pairing.checkpoint())
    for decoder, metadata, index in reversed(sources):
        (end,) = decoder.decode(
            {
                "type": terminal,
                "tool_name": "body",
                "operation_id": "same-operation",
                "result": str(index),
            },
            1,
        )
        result = restored.process(end.model_copy(update={"metadata": metadata}))
        assert result.kind == ("tool.call.completed" if terminal == "tool_completed" else "tool.call.failed")
        assert result.payload["arguments"] == {"value": index}
        assert result.payload["app_operation_id"] == "same-operation"
        assert "tool_call_id" not in result.payload and "_codeplane_pairing" not in result.payload
    native_end = new_event(
        "job", EventKind.tool_call_completed, {"tool_call_id": "same-operation"}, metadata=sources[0][1]
    )
    assert restored.process(native_end).payload["tool_name"] == "native"
    assert restored.checkpoint().starts == ()


def test_observed_operation_keeps_priority_over_llama_app_fallback():
    pairing = ToolPairing()
    metadata = EventMetadata(
        source_framework="llamaindex",
        integration_binding_id="binding",
        namespace=("observation-source", "source"),
    )
    payload = {
        "_codeplane_grouping": {"version": "cp-otel-groups-v1"},
        "_codeplane_observation": {"operation_id": "source-span"},
    }
    start = new_event(
        "job",
        EventKind.tool_call_started,
        {**payload, "app_operation_id": "observed-operation:source-span", "arguments": "original"},
        metadata=metadata,
    )
    end = new_event(
        "job",
        EventKind.tool_call_completed,
        {**payload, "app_operation_id": "different-app-id"},
        metadata=metadata,
    )
    assert pairing.process(start) is None
    result = pairing.process(end)
    assert result.payload["arguments"] == "original"
    assert "_codeplane_pairing" not in result.payload
    assert pairing.checkpoint().starts == ()
