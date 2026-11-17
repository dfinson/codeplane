"""Independent TF 0.1.5 contract goldens, not a claim of live producer qualification."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.tool_pairing import ToolPairing
from backend.services.ingest._base import _advance_prefix, _canonical_record, _verify_source_prefix
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.mapping import NATIVE_FORMATS, NativeMappingDecoder, resolve_mapping_path
from backend.services.ingest.record_service import ObservationRecords
from backend.tests.unit.test_approval_service import session_factory as session_factory

FIXTURES = Path(__file__).parents[1] / "fixtures" / "native_formats"
MAPPINGS = Path(__file__).parents[2] / "compat" / "traceformats" / "mappings"
SHAPES = yaml.safe_load((FIXTURES / "shapes.yaml").read_text(encoding="utf-8"))
RETAINED = yaml.safe_load((FIXTURES / "retained_expected.yaml").read_text(encoding="utf-8"))
ANCHOR = datetime(2026, 1, 2, tzinfo=UTC)


def _decoder(name, **overrides):
    options = {
        "job_id": "qualification-job",
        "external_session_id": "producer-session",
        "binding_id": "qualification-binding",
        "source_key": "source-A",
        "anchor": ANCHOR,
        "framework_id": name,
    }
    options.update(overrides)
    path = resolve_mapping_path(name, [MAPPINGS])
    assert path is not None
    return NativeMappingDecoder(path, **options)


def _semantic(events):
    # Transport-owned provenance/grouping is checked independently below.
    return [
        [
            event.kind,
            {
                key: value
                for key, value in event.payload.items()
                if key not in {"_codeplane_source", "_codeplane_grouping", "turn_id"}
            },
        ]
        for event in events
    ]


def _decode(name, records, **options):
    decoder = _decoder(name, **options)
    return [decoder.decode(deepcopy(raw), index) for index, raw in enumerate(records)]


def test_fixture_matrix_is_exact_and_core_remains_tf_nlp_free():
    assert set(SHAPES) == NATIVE_FORMATS
    for package in ("traceforge", "onnxruntime", "transformers", "torch"):
        assert not any(module == package or module.startswith(f"{package}.") for module in sys.modules)


@pytest.mark.parametrize("name", SHAPES)
def test_documented_shape_golden_payload_usage_and_transport(name):
    case = SHAPES[name]
    decoder = _decoder(name)
    assert decoder.mapping.framework_version == case["version"]
    assert decoder.mapping.ingestion_mode == case["transport"]
    for index, (raw, expected) in enumerate(zip(case["records"], case["expected"], strict=True)):
        before = deepcopy(raw)
        events = decoder.decode(raw, index)
        # Exact payload equality includes zero/false, missing usage, missing tool
        # IDs, failure evidence, and one-to-many output rather than just kinds.
        assert _semantic(events) == expected
        assert raw == before
        for output_index, event in enumerate(events):
            assert event.metadata.ingestion_mode == case["transport"]
            assert event.metadata.source_framework == ("continue" if name == "continue_dev" else name)
            assert event.metadata.integration_binding_id == "qualification-binding"
            assert event.metadata.run_id == "producer-session"
            assert event.session_id == "qualification-job"
            source = event.payload["_codeplane_source"]
            assert source["record_index"] == index
            assert source["output_index"] == output_index
            assert source["vendor_event_id"] == raw.get("id")
            assert source["source_key"] == "source-A"
            assert source["record_sha256"] == hashlib.sha256(_canonical_record(raw)).hexdigest()
            if source["timestamp_fallback"]:
                assert event.timestamp == ANCHOR + timedelta(microseconds=index)


@pytest.mark.parametrize("name", SHAPES)
def test_replay_duplicate_identity_and_binding_source_isolation(name):
    records = SHAPES[name]["records"]
    first = _decode(name, records)
    assert first == _decode(name, records)
    identities = {event.id for events in first for event in events}
    assert len(identities) == sum(map(len, first))
    for options in ({"binding_id": "other-binding"}, {"source_key": "source-B"}, {"job_id": "other-job"}):
        separate = _decode(name, records, **options)
        assert not identities.intersection(event.id for events in separate for event in events)
    repeated = _decode(name, [*records, *records])
    assert not identities.intersection(event.id for events in repeated[len(records) :] for event in events)


@pytest.mark.parametrize("name", SHAPES)
def test_partial_history_keeps_only_the_available_evidence(name):
    case = SHAPES[name]
    count = case["partial_records"]
    assert case["evidence_limit"]
    actual = _decode(name, case["records"][:count])
    assert [_semantic(events) for events in actual] == case["expected"][:count]
    assert not any(event.kind in {"tool.call.completed", "tool.call.failed"} for events in actual for event in events)


@pytest.mark.parametrize("name", SHAPES)
def test_additive_producer_version_drift_respects_declared_shape(name):
    case = SHAPES[name]
    extended = [
        {**raw, "future_producer_extension": {"version": "next", "unknown": [0, False]}} for raw in case["records"]
    ]
    expected = deepcopy(case["expected"])
    for index, replacement in case.get("extension_changed_records", {}).items():
        expected[index] = replacement
    assert [_semantic(events) for events in _decode(name, extended)] == expected
    # Added bytes change provenance, but do not change the declared mapping range.
    baseline = {event.id for events in _decode(name, case["records"]) for event in events}
    assert not baseline.intersection(event.id for events in _decode(name, extended) for event in events)


@pytest.mark.parametrize("name", SHAPES)
def test_invalid_record_does_not_advance_decoder_state(name):
    decoder = _decoder(name)
    raw = deepcopy(SHAPES[name]["records"][0])
    before = decoder.checkpoint()
    with pytest.raises(ValueError):
        decoder.decode({**raw, "invalid_nonfinite_number": float("nan")}, 0)
    assert decoder.checkpoint() == before
    with pytest.raises(ValueError, match="Physical source record index cannot be negative"):
        decoder.decode(raw, -1)
    assert decoder.checkpoint() == before
    assert _semantic(decoder.decode(raw, 0)) == SHAPES[name]["expected"][0]


@pytest.mark.parametrize("name", SHAPES)
def test_physical_prefix_includes_blanks_and_rejects_rewrite_or_truncation(name, tmp_path):
    records = SHAPES[name]["records"]
    lines = ["", *(json.dumps(raw) for raw in records), ""]
    path = tmp_path / "source.jsonl"
    accepted = "\n".join(lines) + "\n"
    path.write_text(accepted, encoding="utf-8")
    prefix = bytes(32)
    for line in lines:
        prefix = _advance_prefix(prefix, _canonical_record(json.loads(line)) if line else b"")
    _verify_source_prefix(path, len(lines), prefix.hex())
    # Unaccepted tail is not part of the accepted checkpoint.
    path.write_text(accepted + '{"unfinished":', encoding="utf-8")
    _verify_source_prefix(path, len(lines), prefix.hex())
    # A shorter or rewritten accepted prefix cannot silently resume.
    for replacement in ("\n", accepted.replace('"', '"changed-', 1)):
        path.write_text(replacement, encoding="utf-8")
        with pytest.raises(RuntimeError, match="truncated or rewritten before its accepted checkpoint"):
            _verify_source_prefix(path, len(lines), prefix.hex())


@pytest.mark.parametrize("name", RETAINED)
def test_retained_upstream_recording_or_explicit_shape(name):
    provenance = json.loads((FIXTURES / "provenance.json").read_text())
    entry = provenance["sources"][name]
    sample = (FIXTURES / "retained" / f"{name}.jsonl").read_bytes()
    assert hashlib.sha256(sample).hexdigest() == entry["sample_sha256"]
    records = [json.loads(line) for line in sample.splitlines()]
    decoder = _decoder(name)
    for raw, physical_index, expected in zip(records, entry["selected_zero_based_lines"], RETAINED[name], strict=True):
        events = decoder.decode(raw, physical_index)
        assert [event.kind for event in events] == [kind for kind, _ in expected]
        for event, (_, fields) in zip(events, expected, strict=True):
            for key, value in fields.items():
                assert event.payload.get(key) == value, (name, physical_index, key)
            assert event.payload["_codeplane_source"]["record_index"] == physical_index
    replay = _decoder(name)
    assert [
        replay.decode(raw, index) for raw, index in zip(records, entry["selected_zero_based_lines"], strict=True)
    ] == [
        _decoder(name).decode(raw, index)
        for raw, index in zip(records, entry["selected_zero_based_lines"], strict=True)
    ]


def test_aider_markdown_preparser_full_and_chunked_source():
    from backend.compat.traceformats.parsers import AiderPreParser

    text = (FIXTURES / "aider_history.md").read_text()
    expected = [
        ["session.started", {"session_id": "aider-20260102T030405", "started_at": "2026-01-02T03:04:05+00:00"}],
        ["message.user", {"content": "Read a.py"}],
        ["message.assistant", {"content": "Reading."}],
        ["telemetry.usage", {"tokens_sent": "0", "tokens_received": "3"}],
        ["tool.call.completed", {"tool_name": "git", "result": "abc123", "message": "Fix route"}],
    ]
    full = list(AiderPreParser().parse_text(text))
    parser = AiderPreParser()
    chunked = []
    for line in text.splitlines(keepends=True):
        chunked.extend(parser.parse_chunk(line))
    chunked.extend(parser.flush())
    assert chunked == full
    assert parser.current_offset == len(text.encode())
    assert [item for events in _decode("aider_markdown", full) for item in _semantic(events)] == expected


def test_copilot_sqlite_markdown_turn_preserves_medium_fidelity():
    from backend.compat.traceformats.parsers import CopilotPreParser

    row = json.loads((FIXTURES / "copilot_turn.json").read_text())
    records = list(CopilotPreParser().parse_turn(row))
    events = [event for group in _decode("copilot_markdown", records) for event in group]
    assert _semantic(events) == [
        ["message.user", {"content": "Read a.py", "turn_index": 7}],
        ["message.assistant", {"content": "Reading."}],
        ["tool.call.started", {"tool_name": "bash", "input": "cat a.py"}],
        ["tool.output", {"language": "text", "content": "file contents"}],
    ]
    assert all(event.timestamp.isoformat() == "2026-01-02T03:04:05+00:00" for event in events)
    assert all("tool_call_id" not in event.payload and "success" not in event.payload for event in events)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("aider", []),
        ("aider_markdown", [("tool.call.completed", None, "missing_identity")]),
        ("amazonq", [("tool.call.completed", "call-1", None)]),
        (
            "antigravity",
            [
                ("tool.call.started", "call-1", "missing_completion"),
                ("tool.call.started", "call-2", "missing_completion"),
            ],
        ),
        ("claude", [("tool.call.completed", "call-1", None)]),
        ("cline", [("tool.call.completed", None, "missing_identity")]),
        ("codex", [("tool.call.completed", "call-1", None)]),
        ("continue_dev", [("tool.call.completed", "call-1", None)]),
        ("copilot", [("tool.call.completed", "call-1", None)]),
        ("copilot_markdown", [("tool.call.started", None, "missing_identity")]),
        ("copilot_vscode", [("tool.call.completed", "call-1", "missing_start")]),
        (
            "crewai",
            [("tool.call.completed", None, "missing_identity"), ("tool.call.started", "call-1", "missing_completion")],
        ),
        ("goose", [("tool.call.completed", "call-1", None)]),
        ("langchain", [("tool.call.failed", "call-1", None)]),
        ("langgraph", [("tool.call.completed", "call-1", None)]),
        ("maf", []),
        ("maf_transcript", [("tool.call.started", None, "missing_identity")]),
        ("openai_agents", [("tool.call.completed", "call-1", None)]),
        ("opencode", [("tool.call.failed", "call-1", None)]),
        ("openhands", []),
        ("pydantic_ai", [("tool.call.failed", "call-1", None)]),
        ("semantic_kernel", [("tool.call.failed", "call-1", None)]),
        ("smolagents", [("tool.call.started", "call-1", "missing_completion")]),
        ("sweagent", []),
    ],
)
def test_released_tool_pairing_preserves_unresolved_evidence(name, expected):
    pairing = ToolPairing()
    emitted = []
    for group in _decode(name, SHAPES[name]["records"]):
        for event in group:
            result = pairing.process(event)
            emitted.extend(result if isinstance(result, list) else [result] if result is not None else [])
    emitted.extend(pairing.flush())
    assert [
        (
            event.kind,
            event.payload.get("tool_call_id", event.payload.get("call_id")),
            event.payload.get("_codeplane_pairing", {}).get("reason"),
        )
        for event in emitted
        if event.kind in {"tool.call.started", "tool.call.completed", "tool.call.failed"}
    ] == expected


async def test_all_formats_durable_replay_keeps_storage_cursor_separate(session_factory, tmp_path):
    all_events = []
    for name, case in SHAPES.items():
        groups = _decode(name, case["records"], job_id="job-1")
        all_events.extend(event for group in groups for event in group)
    async with session_factory() as session:
        repo = EventRepository(session)
        cursors = [await repo.append(event) for event in all_events]
        await session.commit()
    async with session_factory() as session:
        repo = EventRepository(session)
        assert [await repo.append(event) for event in all_events] == cursors
        await session.commit()
        stored = await repo.list_after(0, job_id="job-1")
        assert [row.event for row in stored] == all_events
        assert [row.storage_cursor for row in stored] == cursors
        assert len(set(cursors)) == len(all_events)
        batched = [row for row in stored if row.event.metadata.source_framework == "antigravity"]
        calls = [row for row in batched if row.event.kind == "tool.call.started"]
        assert len(calls) == 2
        assert [row.event.payload["step_index"] for row in calls] == [42, 42]
        assert [row.event.payload["_codeplane_source"]["record_index"] for row in calls] == [1, 1]
        assert [row.event.payload["_codeplane_source"]["output_index"] for row in calls] == [0, 1]
        assert calls[0].storage_cursor != calls[1].storage_cursor
        # A refused source rewrite cannot erase already persisted history.
        path = tmp_path / "source.jsonl"
        path.write_text('{"rewritten":true}\n', encoding="utf-8")
        with pytest.raises(RuntimeError, match="truncated or rewritten"):
            _verify_source_prefix(path, 1, "0" * 64)
        assert [row.event for row in await repo.list_after(0, job_id="job-1")] == all_events


@pytest.mark.parametrize("name", SHAPES)
async def test_admitted_source_record_survives_canonical_storage_and_replay(name, session_factory):
    raw = deepcopy(SHAPES[name]["records"][0])
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="qualification", project_id="proj-1", format_id=name, external_session_id="producer"
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, EventBus())
        batch = ObservationRecordBatch(source_key="source", start_index=0, records=[raw], end_session=True)
        first = await service.append("qualification", batch)
        before = await EventRepository(session).list_after(0, job_id=job.id)
        replay = await service.append("qualification", batch)
        after = await EventRepository(session).list_after(0, job_id=job.id)
        assert first.accepted_records == first.next_index == 1
        assert not first.replay and replay.replay
        assert before == after
    async with session_factory() as session:
        durable = await EventRepository(session).list_after(0, job_id=job.id)
        assert durable == before
        # Assert at the admitted-history boundary, not on an intermediate
        # decoder object that a consumer is entitled to enrich before storage.
        assert any(row.event.raw_event == raw for row in durable)


def test_openai_unfinished_function_span_does_not_invent_completion():
    raw = {
        "object": "trace.span",
        "id": "call-1",
        "trace_id": "trace-1",
        "started_at": "2026-01-02T03:04:05Z",
        "ended_at": None,
        "span_data": {"type": "function", "name": "read_file", "input": '{"path":"a.py"}'},
    }
    events = _decoder("openai_agents").decode(raw, 0)
    assert _semantic(events) == [
        [
            "tool.call.started",
            {
                "tool_name": "read_file",
                "tool_call_id": "call-1",
                "arguments": '{"path":"a.py"}',
                "span_id": "call-1",
                "trace_id": "trace-1",
            },
        ]
    ]


def test_vscode_checkpoint_is_independent_of_another_binding_snapshot():
    first = _decoder("copilot_vscode")
    first.decode({"kind": 0, "v": {"version": 3, "requests": [{"requestId": "first", "modelId": "model-a"}]}}, 0)
    saved = first.checkpoint()
    _decoder("copilot_vscode", binding_id="other").decode(
        {"kind": 0, "v": {"version": 3, "requests": [{"requestId": "second", "modelId": "model-b"}]}}, 0
    )
    restored = _decoder("copilot_vscode")
    restored.restore(saved)
    event = restored.decode({"kind": 2, "k": ["requests", 0, "response"], "v": [{"value": "First answer"}]}, 1)[0]
    assert event.payload["request_id"] == "first"
    assert event.payload["model"] == "model-a"


def test_opencode_checkpoint_does_not_adopt_another_bindings_message_role():
    first = _decoder("opencode")
    first.decode(
        {"type": "message.updated.1", "data": {"sessionID": "shared", "info": {"id": "m1", "role": "assistant"}}}, 0
    )
    saved = first.checkpoint()
    _decoder("opencode", binding_id="other").decode(
        {"type": "message.updated.1", "data": {"sessionID": "shared", "info": {"id": "m1", "role": "user"}}}, 0
    )
    restored = _decoder("opencode")
    restored.restore(saved)
    event = restored.decode(
        {
            "type": "message.part.updated.1",
            "data": {"sessionID": "shared", "part": {"id": "p1", "messageID": "m1", "type": "text", "text": "Answer"}},
        },
        1,
    )[0]
    assert event.kind == "message.assistant"


def test_vscode_failed_record_rolls_back_preprocessor_state_too():
    decoder = _decoder("copilot_vscode")
    decoder.decode({"kind": 0, "v": {"version": 3, "requests": [{"requestId": "accepted"}]}}, 0)
    saved = decoder.checkpoint()
    with pytest.raises(ValueError, match="Malformed source timestamp"):
        decoder.decode(
            {"kind": 0, "v": {"version": 3, "requests": [{"requestId": "rejected", "timestamp": "not-a-date"}]}}, 1
        )
    assert decoder.checkpoint() == saved
    following = decoder.decode({"kind": 2, "k": ["requests", 0, "response"], "v": [{"value": "Answer"}]}, 1)
    assert following[0].payload["request_id"] == "accepted"


def test_vscode_unsupported_journal_version_stops_before_acceptance():
    decoder = _decoder("copilot_vscode")
    with pytest.raises(ValueError, match="[Vv]ersion"):
        decoder.decode({"kind": 0, "v": {"sessionId": "future", "version": 4, "requests": []}}, 0)


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("antigravity", {"type": "FUTURE_STEP", "source": "MODEL", "content": "Retain unknown evidence"}),
        ("openai_agents", {"object": "trace.future", "id": "future-1"}),
    ],
)
async def test_unknown_native_shape_is_not_silently_lost_after_admission(name, raw, session_factory):
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="qualification", project_id="proj-1", format_id=name, external_session_id="producer"
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, EventBus())
        batch = ObservationRecordBatch(source_key="source", start_index=0, records=[raw], end_session=True)
        # A preprocessor may intentionally emit zero events. The accepted
        # source-history contract still forbids silently discarding its record.
        try:
            receipt = await service.append("qualification", batch)
        except ValueError as exc:
            assert any(reason in str(exc).lower() for reason in ("unsupported", "unknown", "version"))
            assert await EventRepository(session).list_after(0, job_id=job.id) == []
        else:
            assert receipt.accepted_records == 1
            retained = await EventRepository(session).list_after(0, job_id=job.id)
            assert any(row.event.raw_event == raw for row in retained)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {"action": "run", "args": {"command": "pwd", "thought": "Inspect"}, "source": "agent"},
            [["command.started", {"command": "pwd", "thought": "Inspect", "source": "agent"}]],
        ),
        (
            {"observation": "run", "content": "/repo", "extras": {"metadata": {"exit_code": 0}}},
            [["command.completed", {"content": "/repo", "exit_code": 0}]],
        ),
        (
            {"kind": "ActionEvent", "tool_name": "apply_patch", "action": {"patch": "*** patch"}, "source": "agent"},
            [["file.edited", {"patch": "*** patch", "source": "agent"}]],
        ),
        (
            {"kind": "ActionEvent", "tool_name": "finish", "action": {"message": "Done."}},
            [["session.ended", {"output": "Done."}]],
        ),
    ],
)
def test_openhands_native_actions_and_legacy_discriminators_keep_payloads(raw, expected):
    before = deepcopy(raw)
    assert _semantic(_decoder("openhands").decode(raw, 0)) == expected
    assert raw == before


@pytest.mark.parametrize("explicit_step_type", [False, True])
@pytest.mark.parametrize("nested_compatibility", [False, True])
def test_smolagents_flat_native_and_nested_compatibility_tool_calls(explicit_step_type, nested_compatibility):
    tool = {"name": "read_file", "arguments": {"path": "a.py"}}
    call = {"id": "call-1", "function": tool} if nested_compatibility else {"id": "call-1", **tool}
    raw = {"step_number": 1, "tool_calls": [call]}
    if explicit_step_type:
        raw["step_type"] = "ActionStep"
    before = deepcopy(raw)
    assert _semantic(_decoder("smolagents").decode(raw, 0)) == [
        ["message.assistant", {"step_number": 1}],
        ["tool.call.started", {"tool_name": "read_file", "tool_call_id": "call-1", "arguments": {"path": "a.py"}}],
    ]
    assert raw == before
