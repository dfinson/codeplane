import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.persistence.event_repo import EventRepository
from backend.services.ingest.mapping import NATIVE_FORMATS, NativeMappingDecoder, resolve_mapping_path
from backend.tests.unit.test_approval_service import session_factory as session_factory

ANCHOR = datetime(2026, 1, 2, tzinfo=UTC)


def _decoder(name, **kwargs):
    path = Path(__file__).parents[2] / "compat" / "traceformats" / "mappings" / f"{name}.yaml"
    return NativeMappingDecoder(
        path, job_id="job", external_session_id="external", anchor=ANCHOR, framework_id=name, **kwargs
    )


def test_all_released_mapping_definitions_load_without_tf_or_model_imports() -> None:
    import sys

    for name in NATIVE_FORMATS:
        decoder = _decoder(name)
        assert decoder.mapping.events or decoder.mapping.spans
    assert not any(name == "traceforge" or name.startswith("traceforge.") for name in sys.modules)
    assert "onnxruntime" not in sys.modules


def test_copilot_native_identity_and_source_motivation_are_stable() -> None:
    raw = {"type": "assistant.message", "data": {"content": "Read implementation"}}
    first = _decoder("copilot")
    second = _decoder("copilot")
    event = first.decode(raw, 3)[0]
    assert event.model_dump(mode="json") == second.decode(raw, 3)[0].model_dump(mode="json")
    assert event.session_id == "job"
    assert event.metadata.run_id == "external"
    assert event.timestamp.year == 2026
    assert event.id != first.decode(raw, 4)[0].id


def test_antigravity_partial_history_never_fabricates_completion_or_usage() -> None:
    decoder = _decoder("antigravity")
    output = decoder.decode(
        {
            "type": "TOOL_CALL",
            "id": "step-1",
            "tool_calls": [
                {"id": "call-1", "name": "read_file", "args": {"path": "readme"}},
                {"id": "call-2", "name": "search", "args": {"pattern": "code"}},
            ],
        },
        0,
    )
    assert [event.kind for event in output] == ["tool.call.started", "tool.call.started"]
    assert all("success" not in event.payload and "result" not in event.payload for event in output)
    assert output[0].id != output[1].id


def test_sweagent_missing_timestamp_uses_anchor_not_wall_clock_and_no_model_stats() -> None:
    event = _decoder("sweagent").decode({"role": "assistant", "content": "Inspect repository"}, 0)[0]
    assert event.timestamp == ANCHOR
    assert event.kind == "message.assistant"
    assert not {"cost_usd", "input_tokens", "output_tokens"} & event.payload.keys()


def test_assistant_and_metadata_records_without_native_identity_share_one_source_turn() -> None:
    decoder = _decoder("copilot", binding_id="binding")
    events = [
        decoder.decode({"type": kind, "data": {"content": content}}, index)[0]
        for index, (kind, content) in enumerate((
            ("assistant.message", "First"), ("session.info", "Metadata"), ("assistant.message", "Second"),
        ))
    ]
    assert len({event.payload["turn_id"] for event in events}) == 1
    assert all(event.payload["_codeplane_grouping"] == {"derived": True, "basis": "source_session"} for event in events)
    operator = decoder.decode({"type": "user.message", "data": {"content": "Continue"}}, 3)[0]
    following = decoder.decode({"type": "assistant.message", "data": {"content": "Third"}}, 4)[0]
    assert operator.payload["turn_id"] == following.payload["turn_id"] != events[0].payload["turn_id"]


def test_explicit_turn_markers_precede_source_fallback() -> None:
    decoder = _decoder("copilot", binding_id="binding")
    marker = decoder.decode({"type": "assistant.turn_start", "data": {"turnId": "native-turn"}}, 0)[0]
    restored = _decoder("copilot", binding_id="binding")
    restored.restore(decoder.checkpoint())
    message = restored.decode({"type": "assistant.message", "data": {"content": "Same producer turn"}}, 1)[0]
    assert marker.payload["turn_id"] == message.payload["turn_id"] == "native-turn"
    assert marker.payload["_codeplane_grouping"]["derived"] is False
    assert message.payload["_codeplane_grouping"]["basis"] == "producer_turn"


def test_native_request_groups_are_source_scoped_and_explicit_turns_take_precedence(tmp_path) -> None:
    path = tmp_path / "requests.yaml"
    path.write_text(
        "framework: request-source\nframework_version: '1'\ningestion_mode: replay\n"
        "events:\n  message:\n    kind: message.assistant\n    payload:\n"
        "      content: content\n      request_id: request\n      turn_id: turn\n",
        encoding="utf-8",
    )

    def decoder(binding, source):
        return NativeMappingDecoder(
            path, job_id="job", external_session_id="session", binding_id=binding, source_key=source, anchor=ANCHOR,
        )

    first = decoder("binding", "first")
    event = first.decode({"type": "message", "request": "request", "content": "First"}, 0)[0]
    following = first.decode({"type": "message", "request": "request", "content": "Second"}, 1)[0]
    sibling_channel = decoder("binding", "second").decode({"type": "message", "request": "request"}, 0)[0]
    different_binding = decoder("other-binding", "first").decode({"type": "message", "request": "request"}, 0)[0]
    assert event.payload["turn_id"] == following.payload["turn_id"] == sibling_channel.payload["turn_id"]
    assert different_binding.payload["turn_id"] != event.payload["turn_id"]
    assert event.payload["_codeplane_grouping"] == {"derived": True, "basis": "request_id"}
    explicit = first.decode({"type": "message", "request": "request", "turn": "producer-turn"}, 2)[0]
    assert explicit.payload["turn_id"] == "producer-turn"


def test_legacy_assistant_rotation_remains_explicit_and_unchanged() -> None:
    decoder = _decoder("copilot", legacy_identity=True)
    raw = {"type": "assistant.message", "data": {"content": "Legacy"}}
    first, second = decoder.decode(raw, 0)[0], decoder.decode(raw, 1)[0]
    assert first.payload["turn_id"] != second.payload["turn_id"]
    assert "_codeplane_grouping" not in first.payload


def test_maf_native_span_mapping_preserves_zero_and_missing_external_ids() -> None:
    event = _decoder("maf").decode(
        {
            "name": "agents.adapter.process",
            "start_time_unix_nano": 0,
            "end_time_unix_nano": 0,
            "attributes": {"activity.type": "message", "activity.is_agentic_request": False},
        },
        0,
    )[0]
    assert event.kind == "message.user"
    assert event.payload["is_agentic"] is False
    assert event.metadata.span_id is None
    assert event.metadata.integration_binding_id is None
    assert event.raw_event["name"] == "agents.adapter.process"
    assert event.timestamp.year == 1970


def test_custom_mapping_precedence_and_invalid_timestamp_state_rollback(tmp_path) -> None:
    override = tmp_path / "sweagent.yaml"
    override.write_text(
        "framework: sweagent\nframework_version: custom\ningestion_mode: replay\n"
        "timestamp_field: timestamp\n"
        "motivation:\n  sources:\n    - events: [message]\n      field: content\n"
        "events:\n  message:\n    kind: message.assistant\n    payload:\n      content: content\n",
        encoding="utf-8",
    )
    assert resolve_mapping_path("sweagent", [tmp_path]) == override
    decoder = NativeMappingDecoder(override, job_id="job", external_session_id="external", anchor=ANCHOR)
    state = decoder.checkpoint()
    with pytest.raises(ValueError, match="timestamp"):
        decoder.decode({"type": "message", "content": "Incomplete accepted prefix", "timestamp": "not-a-date"}, 0)
    assert decoder.checkpoint() == state
    first = decoder.decode({"type": "message", "content": "accepted"}, 0)
    decoder.restore(state)
    assert decoder.decode({"type": "message", "content": "accepted"}, 0) == first


async def test_binding_scoped_ids_and_turns_persist_independently_and_replay_once(session_factory) -> None:
    path = Path(__file__).parents[2] / "compat" / "traceformats" / "mappings" / "copilot.yaml"
    raw = {"type": "assistant.message", "data": {"content": "Same source bytes"}}
    identities = set()
    turns = set()
    for job, binding in (("job-1", "first"), ("job-1", "second"), ("job-2", "third")):
        decoder = NativeMappingDecoder(
            path,
            job_id=job,
            binding_id=binding,
            external_session_id="shared-label",
            anchor=ANCHOR,
        )
        event = decoder.decode(raw, 0)[0]
        identities.add(event.id)
        turns.add(event.payload["turn_id"])
        async with session_factory() as session:
            repo = EventRepository(session)
            cursor = await repo.append(event)
            await session.commit()
        replay = NativeMappingDecoder(
            path,
            job_id=job,
            binding_id=binding,
            external_session_id="shared-label",
            anchor=ANCHOR,
        ).decode(raw, 0)[0]
        async with session_factory() as session:
            assert await EventRepository(session).append(replay) == cursor
            await session.commit()
    assert len(identities) == len(turns) == 3
    async with session_factory() as session:
        assert len(await EventRepository(session).list_after(0)) == 3


def test_explicit_legacy_identity_retains_old_seed_without_rewriting_history() -> None:
    raw = {"type": "assistant.message", "data": {"content": "Legacy"}}
    event = _decoder("copilot", legacy_identity=True).decode(raw, 4)[0]
    digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    expected = uuid.uuid5(
        uuid.NAMESPACE_URL,
        json.dumps(["codeplane-native-v1", "copilot", "external", 4, 0, digest], separators=(",", ":")),
    )
    assert event.id == str(expected)
    assert event.payload["turn_id"] == str(uuid.uuid5(uuid.NAMESPACE_URL, "job:native-turn:0"))
    assert "binding_id" not in event.payload["_codeplane_source"]
