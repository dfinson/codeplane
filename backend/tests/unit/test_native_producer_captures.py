"""Canonical expectations from actual inert writers/CLI output, independent of mapping definitions."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from backend.compat.traceformats.parsers import AiderPreParser
from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.record_service import ObservationRecords
from backend.tests.unit.test_approval_service import session_factory as session_factory
from backend.tests.unit.test_native_format_conformance import _decoder, _semantic

FIXTURES = Path(__file__).parents[1] / "fixtures" / "native_formats" / "produced"
CAPTURES = {
    "aider_markdown": ("aider-0.86.2.history.md", "aider-0.86.2.provenance.json"),
    "continue_dev": ("continue-cli-1.5.47.session.json", "continue-cli-1.5.47.provenance.json"),
}
EXPECTED = {
    "aider_markdown": [
        ["session.started", {"session_id": "aider-20261118T145736", "started_at": "2026-11-18T14:57:36+00:00"}],
        ["message.user", {"content": "Describe addition in this synthetic writer fixture."}],
        ["message.assistant", {"content": "Addition combines two numbers. No model was invoked for this fixture."}],
        # Aider's native blockquote hard-break spaces remain in tool text.
        ["tool.output", {"text": "Synthetic native writer check completed.  "}],
        ["message.user", {"content": "Use the synthetic example 2 plus 3."}],
        ["message.assistant", {"content": "The synthetic example gives 5."}],
    ],
    "continue_dev": [
        ["message.user", {"content": "Describe addition in this synthetic CLI fixture."}],
        ["message.assistant", {"content": "Addition combines two numbers in this inert fixture."}],
        ["message.user", {"content": "Use the synthetic example 2 plus 3."}],
        ["message.assistant", {"content": "The synthetic example gives 5."}],
    ],
}


def records(name: str):
    sample, receipt = CAPTURES[name]
    raw = (FIXTURES / sample).read_bytes()
    provenance = json.loads((FIXTURES / receipt).read_text())
    assert hashlib.sha256(raw).hexdigest() == provenance["sample_sha256"]
    assert provenance["format_id"] == name and provenance["model_inference"] is False
    if name == "aider_markdown":
        return list(AiderPreParser().parse_text(raw.decode()))
    return [json.loads(raw)]


@pytest.mark.parametrize("name", CAPTURES)
def test_actual_native_capture_has_independent_canonical_contents_and_replay_identity(name):
    source = records(name)
    original = deepcopy(source)
    decoder = _decoder(name)
    events = [event for index, record in enumerate(source) for event in decoder.decode(record, index)]
    assert _semantic(events) == EXPECTED[name]
    assert source == original
    replay = _decoder(name)
    assert [event for index, record in enumerate(source) for event in replay.decode(record, index)] == events
    other = _decoder(name, binding_id="other-capture")
    assert not {event.id for event in events}.intersection(
        event.id for index, record in enumerate(source) for event in other.decode(record, index)
    )
    assert not any(
        event.kind in {"telemetry.usage", "tool.call.completed", "execution.native.usage"} for event in events
    )
    assert all(not {"cost", "currency", "success", "tool_call_id"}.intersection(event.payload) for event in events)


def test_actual_aider_writer_markdown_survives_incremental_acquisition():
    raw = (FIXTURES / CAPTURES["aider_markdown"][0]).read_text()
    parser = AiderPreParser()
    incremental: list[dict[str, object]] = []
    for line in raw.splitlines(keepends=True):
        incremental.extend(parser.parse_chunk(line))
    incremental.extend(parser.flush())
    assert incremental == records("aider_markdown")
    assert parser.current_offset == len(raw.encode())


def test_continue_capture_records_real_cli_resume_and_separate_artifact_harness_pins():
    native = json.loads((FIXTURES / CAPTURES["continue_dev"][0]).read_text())
    provenance = json.loads((FIXTURES / CAPTURES["continue_dev"][1]).read_text())
    assert native["sessionId"] == "codeplane-native-capture"
    assert provenance["capture_kind"] == "actual-cli/local-inert-sse"
    assert provenance["producer"]["version"] == "1.5.47" and provenance["producer"]["git_head"] is None
    assert provenance["harness"]["source_commit"] == "abb1fc0439bd335efafd2dc4f39907e25348c572"
    assert [call["exit_code"] for call in provenance["cli_calls"]] == [0, 0]
    assert "--resume" in provenance["cli_calls"][1]["args"]
    assert provenance["local_http_requests"] == 2
    # These are native CLI initializers, not provider-reported zero usage.
    assert native["usage"]["promptTokens"] == native["usage"]["completionTokens"] == 0


@pytest.mark.parametrize("name", CAPTURES)
async def test_real_capture_import_is_durable_observation_only_and_replay_safe(name, session_factory):
    raw = records(name)
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="native-capture", project_id="proj-1", format_id=name, external_session_id="native-writer"
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, EventBus())
        batch = ObservationRecordBatch(source_key="actual-native-file", start_index=0, records=raw, end_session=True)
        accepted = await service.append("native-capture", batch)
        before = await EventRepository(session).list_after(0, job_id=job.id)
        replay = await service.append("native-capture", batch)
        assert accepted.accepted_records == len(raw) and not accepted.replay and replay.replay
        assert await EventRepository(session).list_after(0, job_id=job.id) == before
        assert job.repo is None and job.session_id is None
    async with session_factory() as session:
        durable = await EventRepository(session).list_after(0, job_id=job.id)
        assert durable == before
        derived = {"integration.batch.accepted", "step.started", "step.completed", "turn.summary"}
        canonical = [row.event for row in durable if row.event.kind not in derived]
        for (kind, payload), expected in zip(_semantic(canonical), EXPECTED[name], strict=True):
            assert [
                kind,
                {key: value for key, value in payload.items() if key not in {"step_id", "step_number"}},
            ] == expected
        assert all(event.metadata.integration_binding_id == "native-capture" for event in canonical)
        receipt = next(row.event for row in durable if row.event.kind == "integration.batch.accepted")
        assert receipt.raw_event["records"] == raw
