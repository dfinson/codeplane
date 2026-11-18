"""Populated legacy/native conversion on isolated producer files and SQLite."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from backend import config as backend_config
from backend.models.db import Base
from backend.models.domain import Job, JobSource, JobState, StateConflictError
from backend.persistence.database import create_engine, create_session_factory, serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.events.tool_pairing import ToolPairing
from backend.services.ingest._base import TraceForgeIngestBase, _LegacyNativeIdentity


@pytest.fixture
async def conversion_store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEPLANE_HOME", str(tmp_path))
    monkeypatch.setattr(backend_config, "_codeplane_dir", None)
    monkeypatch.setattr("backend.persistence.database._write_lock", asyncio.Lock())
    engine = create_engine(tmp_path / "data.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    async with serialized_write(factory) as session:
        await ProjectRepository(session).create("project", "Conversion fixture", [])
    yield factory
    await engine.dispose()


def _records(framework):
    if framework == "copilot":
        return [
            None,
            {"type": "assistant.message", "timestamp": "2026-01-02T03:04:05.123456Z", "data": {"content": "Inspect"}},
            {"type": "tool.execution_start", "data": {"toolCallId": "call", "toolName": "view", "arguments": {}}},
        ]
    return [
        {
            "type": "assistant",
            "timestamp": "2026-01-02T03:04:05.123456Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Inspect"},
                    {"type": "tool_use", "id": "call", "name": "Read", "input": {}},
                ]
            },
        },
        None,
    ]


def _completion(framework):
    return (
        {"type": "tool.execution_complete", "data": {"toolCallId": "call", "result": {"content": "Body"}}}
        if framework == "copilot"
        else {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "call", "content": "Body"}]},
        }
    )


def _watcher(factory, framework):
    bus = EventBus()

    async def persist(event):
        async with serialized_write(factory) as session:
            await EventRepository(session).append(event)

    async def exists(event):
        async with factory() as session:
            return await EventRepository(session).contains(event)

    bus.subscribe(persist, required=True)
    watcher = TraceForgeIngestBase(
        EventProcessor(bus, enricher=ToolPairing(), persisted=exists), AsyncMock(), factory, framework
    )
    watcher._prompt_captured.add("native")
    return watcher


async def _wait_checkpoint(factory, count):
    async with asyncio.timeout(5):
        while True:
            async with factory() as session:
                checkpoint = await JobRepository(session).get_ingest_checkpoint("native")
            if checkpoint and checkpoint["record_count"] == count:
                return checkpoint
            await asyncio.sleep(0.01)


async def _populate(factory, path, framework="copilot", records=None):
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    legacy = _LegacyNativeIdentity(framework, "source", "native", anchor)
    async with serialized_write(factory) as session:
        await JobRepository(session).create(
            Job(
                id="native",
                project_id="project",
                repo=str(path.parent),
                prompt="",
                state=JobState.running,
                source=JobSource(f"{framework}_cli"),
                external_session_id="source",
                created_at=anchor,
                updated_at=anchor,
                branch=None,
                base_ref="main",
                worktree_path=None,
                session_id=None,
            )
        )
        await JobRepository(session).update_ingest_checkpoint(
            "native",
            {
                "version": 1,
                "record_count": 0,
                "external_session_id": "source",
                "mapping_sha256": legacy.mapping_digest,
                "prefix_sha256": "0" * 64,
            },
            0,
        )
    raw = _records(framework) if records is None else records
    path.write_bytes(b"".join((json.dumps(item).encode() if item is not None else b"") + b"\n" for item in raw))
    watcher = _watcher(factory, framework)
    task = asyncio.create_task(watcher._tail_traceforge_events("source", "native", path))
    try:
        checkpoint = await _wait_checkpoint(factory, len(raw))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return checkpoint


async def _history(factory):
    async with factory() as session:
        return [
            (row.storage_cursor, row.event.model_dump(mode="json"))
            for row in await EventRepository(session).list_after(0, job_id="native")
        ]


@pytest.mark.parametrize("framework", ["copilot", "claude"])
async def test_populated_conversion_retains_history_buffers_and_evidence_after_new_progress(
    conversion_store, tmp_path, framework
):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    factory = conversion_store
    path = tmp_path / "producer.jsonl"
    original = await _populate(factory, path, framework)
    before = await _history(factory)
    assert before[0][1]["timestamp"] == (
        "2026-01-02T03:04:05.123456Z" if framework == "copilot" else "2026-01-01T00:00:00Z"
    )
    result = await convert_native_checkpoint(factory, job_id="native", source_path=path)
    assert result["status"] == "converted"
    converted = await _wait_checkpoint(factory, original["record_count"])
    assert converted["version"] == 2 and converted["decoded_event_count"] == 2
    assert converted["source"]["byte_count"] == path.stat().st_size
    evidence = converted["conversion"]
    assert evidence["evidence"]["original_checkpoint"] == original
    assert evidence["evidence"]["pending_starts"] == 1
    assert await _history(factory) == before
    await factory.kw["bind"].dispose()
    assert (await convert_native_checkpoint(factory, job_id="native", source_path=path))[
        "status"
    ] == "already_converted"
    with path.open("ab") as stream:
        stream.write(json.dumps(_completion(framework)).encode() + b"\n")
    watcher = _watcher(factory, framework)
    task = asyncio.create_task(watcher._tail_traceforge_events("source", "native", path))
    try:
        progressed = await _wait_checkpoint(factory, original["record_count"] + 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert progressed["conversion"] == evidence
    history = await _history(factory)
    assert history[: len(before)] == before
    completed = next(event for _, event in history if event["kind"] == "tool.call.completed")
    assert completed["payload"]["tool_name"] == ("view" if framework == "copilot" else "Read")
    assert completed["payload"]["result"] == "Body"
    assert completed["metadata"]["motivation"]["intent"] == "Inspect"
    assert completed["raw_event"] == _completion(framework)
    with pytest.raises(StateConflictError, match="progress"):
        await convert_native_checkpoint(factory, job_id="native", source_path=path, rollback=True)
    assert await _history(factory) == history
    assert await _wait_checkpoint(factory, progressed["record_count"]) == progressed


async def test_conversion_rollback_and_retry_preserve_one_original_boundary(conversion_store, tmp_path):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path)
    before = await _history(conversion_store)
    first = await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    rolled_back = await convert_native_checkpoint(conversion_store, job_id="native", source_path=path, rollback=True)
    assert rolled_back["status"] == "rolled_back"
    checkpoint = await _wait_checkpoint(conversion_store, original["record_count"])
    assert {key: value for key, value in checkpoint.items() if key != "conversion"} == original
    assert checkpoint["conversion"]["status"] == "rolled_back"
    assert (await convert_native_checkpoint(conversion_store, job_id="native", source_path=path, rollback=True))[
        "status"
    ] == "already_rolled_back"
    again = await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    assert again["conversion_sha256"] == first["conversion_sha256"]
    assert await _history(conversion_store) == before


async def test_conversion_rejects_mid_compound_cursor_without_mutation(conversion_store, tmp_path):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path, "claude")
    async with serialized_write(conversion_store) as session:
        await JobRepository(session).update_ingest_checkpoint("native", original, 1)
    before = await _history(conversion_store)
    with pytest.raises(StateConflictError, match="decoded|compound"):
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    assert await _wait_checkpoint(conversion_store, original["record_count"]) == original
    assert await _history(conversion_store) == before


async def test_missing_legacy_reader_preserves_original_checkpoint(conversion_store, tmp_path, monkeypatch):
    import builtins

    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path)
    before = await _history(conversion_store)
    actual_import = builtins.__import__

    def missing_legacy(name, *args, **kwargs):
        if name == "traceforge" or name.startswith("traceforge."):
            raise ModuleNotFoundError("legacy deliberately unavailable", name="traceforge")
        return actual_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_legacy)
    with pytest.raises(StateConflictError, match="legacy-traceforge"):
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    assert await _wait_checkpoint(conversion_store, original["record_count"]) == original
    assert await _history(conversion_store) == before


@pytest.mark.parametrize("difference", ["payload", "continuation"])
async def test_real_parser_difference_refuses_promotion(conversion_store, tmp_path, difference):
    import yaml
    from traceforge.config.mappings import resolve_mapping_path

    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    legacy_mapping = yaml.safe_load(resolve_mapping_path("copilot").read_text())
    native_mapping = json.loads(json.dumps(legacy_mapping))
    if difference == "payload":
        legacy_mapping["events"]["assistant.message"]["payload"]["content"] = "data.legacy"
        records = [{"type": "assistant.message", "data": {"content": "New", "legacy": "Old"}}]
    else:
        legacy_mapping["motivation"] = {"sources": [{"events": ["session.info"], "field": "message"}]}
        records = [{"type": "session.info", "data": {"message": "Pending intent"}}]
    legacy_dir = tmp_path / "home" / ".traceforge" / "mappings"
    native_dir = tmp_path / "mappings"
    legacy_dir.mkdir(parents=True)
    native_dir.mkdir(parents=True)
    (legacy_dir / "copilot.yaml").write_text(yaml.safe_dump(legacy_mapping))
    (native_dir / "copilot.yaml").write_text(yaml.safe_dump(native_mapping))
    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path, records=records)
    before = await _history(conversion_store)
    with pytest.raises(StateConflictError, match="canonical output|motivation or turn"):
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    assert await _wait_checkpoint(conversion_store, original["record_count"]) == original
    assert await _history(conversion_store) == before


@pytest.mark.parametrize("change", ["rewrite", "truncate", "rotate", "mapping"])
async def test_source_change_after_staged_promotion_rolls_back(conversion_store, tmp_path, monkeypatch, change):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path)
    before = await _history(conversion_store)
    compare_and_set = JobRepository.compare_and_set_ingest_checkpoint

    async def change_after_stage(repo, expected, checkpoint, decoded):
        state = await compare_and_set(repo, expected, checkpoint, decoded)
        if change == "rewrite":
            path.write_bytes(path.read_bytes().replace(b"Inspect", b"Changed"))
        elif change == "truncate":
            path.write_bytes(b"")
        elif change == "rotate":
            path.rename(tmp_path / "rotated.jsonl")
            path.write_bytes(b"\n")
        else:
            from backend.services.ingest.mapping import resolve_mapping_path

            definition = resolve_mapping_path("copilot").read_bytes()
            directory = tmp_path / "mappings"
            directory.mkdir(parents=True)
            (directory / "copilot.yaml").write_bytes(definition + b"\n# changed during conversion\n")
        return state

    monkeypatch.setattr(JobRepository, "compare_and_set_ingest_checkpoint", change_after_stage)
    with pytest.raises(StateConflictError, match="rewritten|truncated|rotated|mapping implementation"):
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    assert await _wait_checkpoint(conversion_store, original["record_count"]) == original
    assert await _history(conversion_store) == before


async def test_interruption_after_staging_preserves_old_reader_and_retry(conversion_store, tmp_path, monkeypatch):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path)
    before = await _history(conversion_store)
    staged = asyncio.Event()
    compare_and_set = JobRepository.compare_and_set_ingest_checkpoint

    async def pause_after_stage(repo, expected, checkpoint, decoded):
        await compare_and_set(repo, expected, checkpoint, decoded)
        staged.set()
        await asyncio.Event().wait()

    with monkeypatch.context() as scoped:
        scoped.setattr(JobRepository, "compare_and_set_ingest_checkpoint", pause_after_stage)
        task = asyncio.create_task(convert_native_checkpoint(conversion_store, job_id="native", source_path=path))
        try:
            await asyncio.wait_for(staged.wait(), 3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert await _wait_checkpoint(conversion_store, original["record_count"]) == original
    assert await _history(conversion_store) == before
    assert (await convert_native_checkpoint(conversion_store, job_id="native", source_path=path))[
        "status"
    ] == "converted"


async def test_concurrent_file_append_is_left_for_new_reader(conversion_store, tmp_path, monkeypatch):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path)
    accepted_bytes = path.stat().st_size
    compare_and_set = JobRepository.compare_and_set_ingest_checkpoint

    async def append_after_stage(repo, expected, checkpoint, decoded):
        state = await compare_and_set(repo, expected, checkpoint, decoded)
        with path.open("ab") as producer:
            producer.write(json.dumps(_completion("copilot")).encode() + b"\n")
        return state

    with monkeypatch.context() as scoped:
        scoped.setattr(JobRepository, "compare_and_set_ingest_checkpoint", append_after_stage)
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    checkpoint = await _wait_checkpoint(conversion_store, original["record_count"])
    assert checkpoint["source"]["byte_count"] == accepted_bytes < path.stat().st_size
    task = asyncio.create_task(_watcher(conversion_store, "copilot")._tail_traceforge_events("source", "native", path))
    try:
        await _wait_checkpoint(conversion_store, original["record_count"] + 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert any(event["kind"] == "tool.call.completed" for _, event in await _history(conversion_store))


async def test_active_watcher_refuses_conversion_and_repository_rejects_stale_cursor(conversion_store, tmp_path):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path)
    async with conversion_store() as session:
        stale = await JobRepository(session).get_ingest_state("native")
    task = asyncio.create_task(_watcher(conversion_store, "copilot")._tail_traceforge_events("source", "native", path))
    try:
        with path.open("ab") as producer:
            producer.write(json.dumps(_completion("copilot")).encode() + b"\n")
        current = await _wait_checkpoint(conversion_store, original["record_count"] + 1)
        with pytest.raises(StateConflictError, match="already owned"):
            await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
        other_watcher = _watcher(conversion_store, "copilot")
        await other_watcher._tail_traceforge_events("source", "native", path)
        assert not task.done()
        async with serialized_write(conversion_store) as session:
            with pytest.raises(StateConflictError, match="changed concurrently"):
                await JobRepository(session).compare_and_set_ingest_checkpoint(stale, {"version": 2}, 2)
        assert await _wait_checkpoint(conversion_store, current["record_count"]) == current
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_source_ownership_is_enforced_across_processes(conversion_store, tmp_path):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path)
    script = """
import sys
from pathlib import Path
from backend.persistence.database import create_engine, create_session_factory
from backend.services.ingest.source_ownership import hold_native_source
engine = create_engine(Path(sys.argv[1]))
with hold_native_source(create_session_factory(engine), "native"):
    print("owned", flush=True)
    sys.stdin.buffer.read(1)
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(tmp_path / "data.db"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"owned\n"
        with pytest.raises(StateConflictError, match="already owned"):
            await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    finally:
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(b"x"), 5)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise
        assert process.returncode == 0, (stdout, stderr)
    assert await _wait_checkpoint(conversion_store, original["record_count"]) == original
    assert (await convert_native_checkpoint(conversion_store, job_id="native", source_path=path))[
        "status"
    ] == "converted"


@pytest.mark.parametrize("framework", ["copilot", "claude"])
async def test_operator_cli_converts_and_rolls_back_populated_sqlite(conversion_store, tmp_path, framework):
    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path, framework)
    before = await _history(conversion_store)
    arguments = [
        sys.executable,
        "-m",
        "backend.main",
        "convert-native-checkpoint",
        "--job-id",
        "native",
        "--source",
        str(path),
    ]
    for flags, status in (([], "converted"), (["--rollback"], "rolled_back")):
        process = await asyncio.create_subprocess_exec(
            *arguments, *flags, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), 15)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise
        assert process.returncode == 0, (output, error)
        assert json.loads(output)["status"] == status
    assert (await _wait_checkpoint(conversion_store, original["record_count"]))["version"] == 1
    assert await _history(conversion_store) == before


@pytest.mark.parametrize("framework", ["copilot", "claude"])
async def test_rolled_back_legacy_reader_can_advance_without_losing_evidence(conversion_store, tmp_path, framework):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path, framework)
    await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    await convert_native_checkpoint(conversion_store, job_id="native", source_path=path, rollback=True)
    rollback = await _wait_checkpoint(conversion_store, original["record_count"])
    with path.open("ab") as producer:
        producer.write(json.dumps(_completion(framework)).encode() + b"\n")
    task = asyncio.create_task(_watcher(conversion_store, framework)._tail_traceforge_events("source", "native", path))
    try:
        progressed = await _wait_checkpoint(conversion_store, original["record_count"] + 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert progressed["version"] == 1 and progressed["conversion"] == rollback["conversion"]
    before = await _history(conversion_store)
    completed = next(event for _, event in before if event["kind"] == "tool.call.completed")
    assert completed["payload"]["result"] == "Body" and completed["raw_event"] is None
    with pytest.raises(StateConflictError, match="progress"):
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    assert await _history(conversion_store) == before


@pytest.mark.parametrize("change", ["mapping", "incomplete-prefix"])
async def test_changed_legacy_contract_or_incomplete_accepted_line_is_not_promoted(conversion_store, tmp_path, change):
    from traceforge.config.mappings import resolve_mapping_path

    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path)
    before = await _history(conversion_store)
    if change == "mapping":
        source = resolve_mapping_path("copilot").read_bytes()
        directory = tmp_path / "home" / ".traceforge" / "mappings"
        directory.mkdir(parents=True)
        (directory / "copilot.yaml").write_bytes(source + b"\n# changed legacy mapping\n")
    else:
        path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(StateConflictError, match="mapping digest|incomplete"):
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    assert await _wait_checkpoint(conversion_store, original["record_count"]) == original
    assert await _history(conversion_store) == before


async def test_partially_committed_record_requires_legacy_recovery_before_conversion(conversion_store, tmp_path):
    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    path = tmp_path / "producer.jsonl"
    original = await _populate(conversion_store, path)
    async with conversion_store() as session:
        job = await JobRepository(session).get("native")
    old = _LegacyNativeIdentity("copilot", "source", "native", job.created_at)
    for ordinal, raw in enumerate(_records("copilot")):
        if raw is not None:
            tuple(old.events(raw, ordinal))
    interrupted_raw = {"type": "assistant.message", "data": {"content": "Persisted before checkpoint"}}
    interrupted = tuple(old.events(interrupted_raw, original["record_count"]))
    with path.open("ab") as producer:
        producer.write(json.dumps(interrupted_raw).encode() + b"\n")
    async with serialized_write(conversion_store) as session:
        for event in interrupted:
            await EventRepository(session).append(event)
    before = await _history(conversion_store)
    with pytest.raises(StateConflictError, match="outside the accepted physical checkpoint"):
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    assert await _wait_checkpoint(conversion_store, original["record_count"]) == original
    task = asyncio.create_task(_watcher(conversion_store, "copilot")._tail_traceforge_events("source", "native", path))
    try:
        recovered = await _wait_checkpoint(conversion_store, original["record_count"] + 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert recovered["version"] == 1 and "conversion" not in recovered
    assert await _history(conversion_store) == before
    assert (await convert_native_checkpoint(conversion_store, job_id="native", source_path=path))[
        "status"
    ] == "converted"


def test_existing_raw_payload_is_not_excluded_from_equivalence():
    from backend.services.ingest._base import _NativeIdentity
    from backend.services.ingest.checkpoint_conversion import _compare_event

    event = next(
        _NativeIdentity("copilot", "source", "job", datetime(2026, 1, 1, tzinfo=UTC)).events(
            {"type": "assistant.message", "data": {"content": "Input"}}, 0
        )
    )
    changed = event.model_copy(update={"raw_event": {"different": "retained producer payload"}})
    with pytest.raises(StateConflictError, match="raw event differs"):
        _compare_event(changed, event)


@pytest.mark.parametrize(("boolean", "number"), [(True, 1), (False, 0)], ids=["true-one", "false-zero"])
async def test_real_legacy_boolean_number_difference_preserves_original_history(
    conversion_store, tmp_path, boolean, number
):
    import yaml
    from traceforge.config.mappings import resolve_mapping_path

    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    legacy = yaml.safe_load(resolve_mapping_path("copilot").read_text())
    replacement = json.loads(json.dumps(legacy))
    legacy["events"]["session.info"]["payload"] = {"value": "data.legacy"}
    replacement["events"]["session.info"]["payload"] = {"value": "data.replacement"}
    for directory, definition in (
        (tmp_path / "home" / ".traceforge" / "mappings", legacy),
        (tmp_path / "home" / ".codeplane" / "mappings", replacement),
        (tmp_path / "mappings", replacement),
    ):
        directory.mkdir(parents=True)
        (directory / "copilot.yaml").write_text(yaml.safe_dump(definition))
    raw = {
        "type": "session.info",
        "data": {
            "legacy": {"nested": [boolean]},
            "replacement": {"nested": [number]},
        },
    }
    path = tmp_path / "producer.jsonl"
    await _populate(conversion_store, path, records=[raw])
    before = json.dumps(await _history(conversion_store), sort_keys=True)
    async with conversion_store() as session:
        original = (await JobRepository(session).get_ingest_state("native")).encoded_checkpoint
    with pytest.raises(StateConflictError, match="canonical output"):
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    async with conversion_store() as session:
        assert (await JobRepository(session).get_ingest_state("native")).encoded_checkpoint == original
    assert json.dumps(await _history(conversion_store), sort_keys=True) == before


@pytest.mark.parametrize(("boolean", "number"), [(True, 1), (False, 0)], ids=["true-one", "false-zero"])
def test_retained_raw_boolean_number_difference_is_not_equivalent(tmp_path, monkeypatch, boolean, number):
    from backend.services.ingest._base import _NativeIdentity
    from backend.services.ingest.checkpoint_conversion import _compare_event

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CODEPLANE_HOME", str(tmp_path / "active"))
    monkeypatch.setattr(backend_config, "_codeplane_dir", None)
    raw = {"type": "assistant.message", "data": {"content": "Input"}, "nested": [boolean]}
    event = next(_NativeIdentity("copilot", "source", "job", datetime(2026, 1, 1, tzinfo=UTC)).events(raw, 0))
    changed = event.model_copy(update={"raw_event": {**raw, "nested": [number]}})
    with pytest.raises(StateConflictError, match="raw event differs"):
        _compare_event(event, changed)


async def test_active_home_mapping_mismatch_cannot_be_hidden_by_default_home(conversion_store, tmp_path):
    import yaml
    from traceforge.config.mappings import resolve_mapping_path

    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    legacy = yaml.safe_load(resolve_mapping_path("copilot").read_text())
    replacement = json.loads(json.dumps(legacy))
    replacement["events"]["assistant.message"]["payload"]["content"] = "data.replacement"
    for directory, definition in (
        (tmp_path / "home" / ".codeplane" / "mappings", legacy),
        (tmp_path / "mappings", replacement),
    ):
        directory.mkdir(parents=True)
        (directory / "copilot.yaml").write_text(yaml.safe_dump(definition))
    path = tmp_path / "producer.jsonl"
    original = await _populate(
        conversion_store,
        path,
        records=[{"type": "assistant.message", "data": {"content": "Accepted", "replacement": "Different"}}],
    )
    before = await _history(conversion_store)
    with pytest.raises(StateConflictError, match="canonical output"):
        await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    assert await _wait_checkpoint(conversion_store, original["record_count"]) == original
    assert await _history(conversion_store) == before


@pytest.mark.parametrize("selection", ["active", "legacy-fallback"])
async def test_active_home_replacement_selection_resumes_without_migration(conversion_store, tmp_path, selection):
    import yaml
    from traceforge.config.mappings import resolve_mapping_path

    from backend.services.ingest.checkpoint_conversion import convert_native_checkpoint

    original_mapping = yaml.safe_load(resolve_mapping_path("copilot").read_text())
    wrong = json.loads(json.dumps(original_mapping))
    wrong["events"]["assistant.message"]["payload"]["content"] = "data.replacement"
    definitions = [
        (tmp_path / "home" / ".traceforge" / "mappings", original_mapping),
        (tmp_path / "home" / ".codeplane" / "mappings", wrong),
    ]
    if selection == "active":
        definitions.append((tmp_path / "mappings", original_mapping))
    for directory, definition in definitions:
        directory.mkdir(parents=True)
        (directory / "copilot.yaml").write_text(yaml.safe_dump(definition))
    mapping_bytes = {
        directory / "copilot.yaml": (directory / "copilot.yaml").read_bytes() for directory, _ in definitions
    }
    path = tmp_path / "producer.jsonl"
    await _populate(
        conversion_store,
        path,
        records=[{"type": "assistant.message", "data": {"content": "Accepted", "replacement": "Wrong"}}],
    )
    await convert_native_checkpoint(conversion_store, job_id="native", source_path=path)
    converted = await _wait_checkpoint(conversion_store, 1)
    with path.open("ab") as producer:
        producer.write(
            json.dumps(
                {"type": "assistant.message", "data": {"content": "Continued", "replacement": "Wrong tail"}}
            ).encode()
            + b"\n"
        )
    task = asyncio.create_task(_watcher(conversion_store, "copilot")._tail_traceforge_events("source", "native", path))
    try:
        progressed = await _wait_checkpoint(conversion_store, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert progressed["mapping_sha256"] == converted["mapping_sha256"]
    assert [event["payload"]["content"] for _, event in await _history(conversion_store)] == ["Accepted", "Continued"]
    assert {path: path.read_bytes() for path in mapping_bytes} == mapping_bytes
    assert (tmp_path / "mappings").exists() == (selection == "active")
