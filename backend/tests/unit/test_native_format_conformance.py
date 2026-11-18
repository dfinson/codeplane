"""Independent contract goldens and retained producer evidence with explicit fidelity limits."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from backend.models.domain import StateConflictError
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
from backend.tests.unit.test_native_mapping import _assert_standalone_native_imports

if TYPE_CHECKING:
    from backend.models.session_event import SessionEvent

FIXTURES = Path(__file__).parents[1] / "fixtures" / "native_formats"
MAPPINGS = Path(__file__).parents[2] / "compat" / "traceformats" / "mappings"
SHAPES = yaml.safe_load((FIXTURES / "shapes.yaml").read_text(encoding="utf-8"))
RETAINED = yaml.safe_load((FIXTURES / "retained_expected.yaml").read_text(encoding="utf-8"))
SDK_LOCAL = yaml.safe_load((FIXTURES / "sdk_local" / "expected.yaml").read_text(encoding="utf-8"))
PRODUCED = FIXTURES / "produced"
PRODUCED_EXPECTED = yaml.safe_load((PRODUCED / "expected.yaml").read_text(encoding="utf-8"))
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
    _assert_standalone_native_imports()


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


def _sdk_records(name):
    provenance = json.loads((FIXTURES / "sdk_local" / "provenance.json").read_text(encoding="utf-8"))
    assert (
        hashlib.sha256((FIXTURES / "sdk_local" / provenance["capture_script"]).read_bytes()).hexdigest()
        == provenance["capture_script_sha256"]
    )
    source = provenance["sources"][name]
    sample = (FIXTURES / "sdk_local" / f"{name}.jsonl").read_bytes()
    assert hashlib.sha256(sample).hexdigest() == source["sample_sha256"]
    assert len(sample.splitlines()) == source["physical_records"]
    return [json.loads(line) for line in sample.splitlines()]


@pytest.mark.parametrize("name", SDK_LOCAL)
def test_actual_local_sdk_bytes_payload_identity_and_absent_usage(name):
    case = SDK_LOCAL[name]
    records = _sdk_records(name)
    decoder = _decoder(name)
    assert decoder.mapping.framework_version == case["version"]
    assert decoder.mapping.ingestion_mode == case["transport"]
    groups = [decoder.decode(deepcopy(raw), index) for index, raw in enumerate(records)]
    # JSON comparison preserves false/zero distinctions in real tool results.
    assert json.dumps([_semantic(events) for events in groups], sort_keys=True) == json.dumps(
        case["expected"], sort_keys=True
    )
    events = [event for group in groups for event in group]
    assert len(events) == len(records)
    assert len({event.id for event in events}) == len(events)
    assert [event.model_dump_json() for event in events] == [
        event.model_dump_json() for group in _decode(name, records) for event in group
    ]
    assert not {event.id for event in events}.intersection(
        event.id for group in _decode(name, records, source_key="separate-source") for event in group
    )
    for index, event in enumerate(events):
        source = event.payload["_codeplane_source"]
        assert source["record_index"] == index and source["output_index"] == 0
        assert source["record_sha256"] == hashlib.sha256(_canonical_record(records[index])).hexdigest()
        assert source["vendor_event_id"] == records[index].get("id")
        assert source["timestamp_fallback"] is False
        assert event.timestamp == datetime.fromisoformat(records[index]["timestamp"].replace("Z", "+00:00"))
        assert event.metadata.source_framework == name
        assert event.metadata.ingestion_mode == "file_watch"
        assert not {"input_tokens", "output_tokens", "total_tokens", "cost", "usage", "success"} & event.payload.keys()
        assert not event.kind.startswith(("llm.", "telemetry."))
    if name == "maf_transcript":
        assert all("from_property" in raw and "from" not in raw for raw in records)
        assert events[2].metadata.motivation.intent == "Inspecting local input."
        assert events[2].metadata.motivation.source_event_ids == (events[1].id,)
        assert all("tool_call_id" not in event.payload for event in events)


@pytest.mark.parametrize("name", SDK_LOCAL)
def test_actual_local_sdk_partial_pairs_remain_explicit(name):
    case = SDK_LOCAL[name]
    records = _sdk_records(name)
    pairing = ToolPairing()
    partial = []
    for group in _decode(name, records[: case["partial_records"]]):
        for event in group:
            result = pairing.process(event)
            partial.extend(result if isinstance(result, list) else [result] if result is not None else [])
    partial.extend(pairing.flush())
    starts = [event for event in partial if event.kind == "tool.call.started"]
    assert len(starts) == 1
    assert starts[0].payload["_codeplane_pairing"] == {
        "status": "unresolved",
        "reason": "missing_identity" if name == "maf_transcript" else "missing_completion",
    }
    assert not any(event.kind in {"tool.call.completed", "tool.call.failed"} for event in partial)


@pytest.mark.parametrize("name", SDK_LOCAL)
async def test_actual_local_sdk_registered_history_retains_bytes_semantics_and_exact_retries(name, session_factory):
    records = _sdk_records(name)
    case = SDK_LOCAL[name]
    split = case["partial_records"]
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="sdk-capture", project_id="proj-1", format_id=name, external_session_id="inert-producer"
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, EventBus())
        first = ObservationRecordBatch(source_key="sdk-local", start_index=0, records=records[:split])
        receipt = await service.append("sdk-capture", first)
        assert receipt.accepted_records == receipt.next_index == split
        initial = await EventRepository(session).list_after(0, job_id=job.id)
        assert (await service.append("sdk-capture", first)).replay
        assert await EventRepository(session).list_after(0, job_id=job.id) == initial
        second = ObservationRecordBatch(
            source_key="sdk-local", start_index=split, prefix_sha256=receipt.prefix_sha256, records=records[split:]
        )
        receipt = await service.append("sdk-capture", second)
        assert receipt.accepted_records == len(records) - split and receipt.next_index == len(records)
        assert not receipt.session_closed
        before = await EventRepository(session).list_after(0, job_id=job.id)
    async with session_factory() as session:
        bindings = IntegrationRepository(session)
        service = ObservationRecords(bindings, JobRepository(session), EventBus())
        assert (await service.append("sdk-capture", first)).replay
        assert (await service.append("sdk-capture", second)).replay
        durable = await EventRepository(session).list_after(0, job_id=job.id)
        assert durable == before
        receipts = [row.event for row in durable if row.event.kind == "integration.batch.accepted"]
        assert [raw for event in receipts for raw in event.raw_event["records"]] == records
        assert all(event.metadata.source_framework is None for event in receipts)
        tools = [
            row.event
            for row in durable
            if row.event.kind in {"tool.call.started", "tool.call.completed", "tool.call.failed"}
        ]
        assert [event.kind for event in tools] == case["paired_kinds"]
        if name == "maf_transcript":
            assert tools[0].payload["activity_id"] == "activity-invoke"
            assert tools[0].payload["_codeplane_pairing"]["reason"] == "missing_identity"
            assert "tool_call_id" not in tools[0].payload
        else:
            for event, index in zip(tools, case["completed_record_indices"], strict=True):
                _, expected = case["expected"][index][0]
                for key, value in expected.items():
                    assert json.dumps(event.payload[key], sort_keys=True) == json.dumps(value, sort_keys=True)
                assert "_codeplane_pairing" not in event.payload
                assert event.payload["_codeplane_source"]["record_index"] == index
        assert not any(row.event.kind.startswith(("llm.", "telemetry.usage")) for row in durable)
        # A different physical input at an accepted position cannot be silently re-admitted.
        changed = deepcopy(records[0])
        changed["unexpected_rewrite"] = True
        with pytest.raises(StateConflictError, match="prefix|position"):
            await service.append("sdk-capture", first.model_copy(update={"records": [changed, *records[1:split]]}))
        assert await EventRepository(session).list_after(0, job_id=job.id) == durable


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
    chunked: list[dict[str, object]] = []
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
    ("markdown", "kind", "payload"),
    [
        ('```json\n{"value": false}\n```', "structured_output", {"format": "json", "data": {"value": False}}),
        ('```json\n{"value": false}\n```\n', "structured_output", {"format": "json", "data": {"value": False}}),
        ('```json\n{"value": false}', "structured_output", {"format": "json", "data": {"value": False}}),
        ('```json\n{"value":', "code_block", {"language": "json", "content": '{"value":'}),
        ('```json\n{"value": false}\n``', "code_block", {"language": "json", "content": '{"value": false}\n``'}),
    ],
)
def test_copilot_terminal_newline_preserves_closed_and_unclosed_fence_behavior(markdown, kind, payload):
    from backend.compat.traceformats.parsers import CopilotPreParser

    row = {
        "session_id": "native-sqlite",
        "turn_index": 0,
        "timestamp": ANCHOR.isoformat(),
        "assistant_response": markdown,
    }
    original = deepcopy(row)
    parser = CopilotPreParser()
    (record,) = parser.parse_turn(row)
    assert record == {
        "session_id": "native-sqlite",
        "turn_index": 0,
        "timestamp": ANCHOR.isoformat(),
        "type": kind,
        **payload,
    }
    assert row == original and parser.current_offset == 0


def _transferred_bytes(name):
    manifest = json.loads((PRODUCED / "transferred-provenance.json").read_text())
    source = manifest[name]
    raw = (PRODUCED / source["file"]).read_bytes()
    assert len(raw) == source["bytes"] and hashlib.sha256(raw).hexdigest() == source["sha256"]
    return raw


def test_actual_antigravity_connection_history_payload_identity_replay_and_partial_tool():
    records = [json.loads(line) for line in _transferred_bytes("antigravity").splitlines()]
    assert len(records) == 5
    groups = _decode("antigravity", records)
    events = [event for group in groups for event in group]
    assert _semantic(events) == PRODUCED_EXPECTED["antigravity"]
    assert groups == _decode("antigravity", records)
    assert len({event.id for event in events}) == 5
    assert not {event.id for event in events}.intersection(
        event.id for group in _decode("antigravity", records, source_key="other-history") for event in group
    )
    for index, event in enumerate(events):
        source = event.payload["_codeplane_source"]
        assert source["vendor_event_id"] == f"inert-native-capture:{index + 1}"
        assert source["record_index"] == index and source["output_index"] == 0
        assert source["record_sha256"] == hashlib.sha256(_canonical_record(records[index])).hexdigest()
        assert source["timestamp_fallback"] is True and event.timestamp == ANCHOR + timedelta(microseconds=index)
        assert not {"usage", "input_tokens", "output_tokens", "cost_usd", "success"} & event.payload.keys()
    resumed = _decoder("antigravity")
    for index, record in enumerate(records[:3]):
        resumed.decode(record, index)
    checkpoint = resumed.checkpoint()
    restored = _decoder("antigravity")
    restored.restore(checkpoint)
    assert [restored.decode(raw, index) for index, raw in enumerate(records[3:], 3)] == groups[3:]
    pairing = ToolPairing()
    for event in events[:3]:
        pairing.process(event)
    (unresolved,) = pairing.flush()
    assert unresolved.payload["tool_call_id"] == "inert-native-capture:3"
    assert unresolved.payload["_codeplane_pairing"] == {"status": "unresolved", "reason": "missing_completion"}
    assert not any(event.kind in {"tool.call.completed", "tool.call.failed"} for event in events)


async def test_actual_antigravity_history_acquisition_keeps_original_prefix_and_rewrites_fail(
    session_factory, tmp_path
):
    from backend.services.ingest.acquisition import AcquisitionChannel
    from backend.services.ingest.acquisition_sources import file_journal
    from backend.tests.unit.test_native_acquisition import _channel, _history

    raw = _transferred_bytes("antigravity")
    path = tmp_path / "native-history.jsonl"
    path.write_bytes(b"\n".join(raw.splitlines()[:3]) + b"\n")
    channel, job_id = await _channel(session_factory, "antigravity")
    first = await file_journal(path)
    assert first.physical_count == 3
    receipt = await channel.accept(first)
    assert receipt.accepted_records == receipt.next_index == 3
    path.write_bytes(raw)
    page = await file_journal(path)
    assert page.physical_count == 5
    receipt = await channel.accept(page)
    assert receipt.accepted_records == 2 and receipt.next_index == 5
    history = await _history(session_factory, job_id)
    retained = [
        record
        for row in history
        if row.event.kind == "integration.batch.accepted"
        for record in row.event.raw_event["records"]
    ]
    assert retained == [json.loads(line) for line in raw.splitlines()]
    restarted = AcquisitionChannel(session_factory, EventBus(), binding_id="binding", source_key="source")
    assert await restarted.accept(page) is None
    assert await _history(session_factory, job_id) == history
    path.write_bytes(raw.replace(b"synthetic fixture", b"rewritten fixture", 1))
    with pytest.raises(StateConflictError, match="rewritten"):
        await restarted.accept(await file_journal(path))
    assert await _history(session_factory, job_id) == history


def _sqlite_field_bytes(field):
    raw = base64.b64decode(field["sqlite_bytes_base64"], validate=True)
    assert len(raw) == field["bytes"] and hashlib.sha256(raw).hexdigest() == field["sha256"]
    return raw


@pytest.mark.parametrize("session_index", [0, 1])
async def test_actual_copilot_sqlite_bytes_acquisition_preparser_and_independent_golden(session_index, tmp_path):
    from backend.compat.traceformats.parsers import CopilotPreParser
    from backend.services.ingest.acquisition_sources import sqlite_snapshot

    artifact = json.loads(_transferred_bytes("copilot_markdown"))
    assert artifact["native_cli_version"] == "1.0.108" and not artifact["jsonl_used_to_generate_rows"]
    captured = artifact["sessions"][session_index]
    native_id = captured["native_session_id"]
    assert captured["native_session_start"]["sessionId"] == native_id
    path = tmp_path / "retained-field-reconstruction.db"
    tables = {
        "sessions": ("id", "cwd", "repository", "host_type", "branch", "summary", "created_at", "updated_at"),
        "turns": ("id", "session_id", "turn_index", "user_message", "assistant_response", "timestamp"),
    }
    rows = {}
    with sqlite3.connect(path) as database:
        for table, columns in tables.items():
            database.execute(_sqlite_field_bytes(artifact["schema_sql_bytes"][table]).decode())
            (source_row,) = captured["selected_native_sqlite_rows"][table]
            row = {
                name: _sqlite_field_bytes(value).decode("utf-8") if isinstance(value, dict) else value
                for name, value in source_row.items()
            }
            assert set(row) == set(columns)
            placeholders = ",".join("?" for _ in columns)
            database.execute(f"INSERT INTO {table} VALUES ({placeholders})", tuple(row[name] for name in columns))
            rows[table] = row
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    page = await sqlite_snapshot(
        path,
        table="turns",
        identity_column="id",
        sequence_column="turn_index",
        filters={"session_id": native_id},
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert page.physical_count == len(page.records) == 1
    assert page.records[0].identity == str((2, 5)[session_index])
    assert page.records[0].sequence == 0 and page.records[0].raw == rows["turns"]
    records = list(CopilotPreParser().parse_turn(page.records[0].raw))
    groups = _decode("copilot_markdown", records, external_session_id=native_id)
    events = [event for group in groups for event in group]
    expected = [
        ["message.user", {"content": rows["turns"]["user_message"], "turn_index": 0}],
        *PRODUCED_EXPECTED["copilot_markdown"][native_id],
    ]
    assert _semantic(events) == expected
    assert len(events) == len(records) == (8, 3)[session_index]
    assert len({event.id for event in events}) == len(events)
    assert groups == _decode(
        "copilot_markdown",
        list(CopilotPreParser().parse_turn(page.records[0].raw)),
        external_session_id=native_id,
    )
    assert _semantic([event for group in groups[:-1] for event in group]) == expected[:-1]
    timestamp = datetime.fromisoformat(rows["turns"]["timestamp"].replace("Z", "+00:00"))
    for event in events:
        assert event.timestamp == timestamp and not event.payload["_codeplane_source"]["timestamp_fallback"]
        assert event.metadata.ingestion_mode == "sqlite" and event.metadata.run_id == native_id
        assert (
            not {"tool_call_id", "success", "usage", "input_tokens", "output_tokens", "cost_usd"} & event.payload.keys()
        )
        assert not event.kind.startswith(("tool.call.", "telemetry.", "llm."))


def test_actual_cline_ui_minter_ids_are_not_timestamps_or_tool_correlation():
    records = [json.loads(line) for line in _transferred_bytes("cline").splitlines()]
    assert [raw["ts"] for raw in records] == [1, 1, 2, 2, 3, 3, 3]
    groups = _decode("cline", records)
    assert groups == _decode("cline", records)
    events = [event for group in groups for event in group]
    assert len({event.id for event in events}) == len(events)
    for event in events:
        source = event.payload["_codeplane_source"]
        index = source["record_index"]
        assert source["timestamp_fallback"] is True, "Native UI minter IDs are not Unix dates"
        assert event.timestamp == ANCHOR + timedelta(microseconds=index)
        assert source["vendor_event_id"] == records[index]["ts"], "Preserve the actual native UI identity"
        assert not {"tool_call_id", "input_tokens", "output_tokens", "cost_usd", "success"} & event.payload.keys()


def test_actual_cline_partial_ui_tool_update_does_not_claim_completion():
    records = [json.loads(line) for line in _transferred_bytes("cline").splitlines()]
    assert records[2]["say"] == "tool" and records[2]["partial"] is True
    before_final = [event for group in _decode("cline", records[:3]) for event in group]
    assert not any(event.kind in {"tool.call.completed", "tool.call.failed"} for event in before_final)
    events = [event for group in _decode("cline", records) for event in group]
    completed = [event for event in events if event.kind == "tool.call.completed"]
    assert len(completed) == 1
    assert completed[0].payload["tool_name"] == "readFile" and completed[0].payload["path"] == "fixture.txt"
    assert completed[0].payload["_codeplane_source"]["record_index"] == 3


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
    all_events: list[SessionEvent] = []
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
        receipts = [row.event for row in durable if row.event.kind == "integration.batch.accepted"]
        assert len(receipts) == 1
        assert receipts[0].raw_event == {
            "contract": "codeplane-source-batch-v1",
            "source_key": "source",
            "start_index": 0,
            "prefix_sha256": "0" * 64,
            "records": [raw],
        }


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
            assert len(retained) == 1
            assert retained[0].event.kind == "integration.batch.accepted"
            assert retained[0].event.raw_event == {
                "contract": "codeplane-source-batch-v1",
                "source_key": "source",
                "start_index": 0,
                "prefix_sha256": "0" * 64,
                "records": [raw],
            }


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
