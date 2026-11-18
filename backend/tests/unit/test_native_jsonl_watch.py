"""Standalone JSONL acquisition and real checkpoint/persistence regressions."""

import asyncio
import hashlib
import io
import json
import os
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from backend.models.domain import JobSource
from backend.models.events import new_event
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.events.tool_pairing import ToolPairing
from backend.services.ingest._base import TraceForgeIngestBase
from backend.services.ingest.jsonl_source import MAX_RECORD_BYTES, NativeJsonlSource
from backend.tests.unit.test_approval_service import session_factory as session_factory


@pytest.fixture(autouse=True)
def isolate_write_lock(monkeypatch):
    monkeypatch.setattr("backend.persistence.database._write_lock", asyncio.Lock())


async def _watcher(factory, framework):
    async with serialized_write(factory) as session:
        jobs = JobRepository(session)
        job = await jobs.get("job-1")
        # A separate owned Job avoids changing a managed Job's source in place.
        await jobs.create(replace(job, id="native", source=JobSource(f"{framework}_cli"), external_session_id="source"))
    return _new_watcher(factory, framework)


def _new_watcher(factory, framework):
    bus = EventBus()

    async def persist(event):
        async with serialized_write(factory) as session:
            await EventRepository(session).append(event)

    async def exists(event):
        async with factory() as session:
            return await EventRepository(session).contains(event)

    bus.subscribe(persist, required=True)
    watcher = TraceForgeIngestBase(
        EventProcessor(bus, enricher=ToolPairing(), persisted=exists),
        AsyncMock(),
        factory,
        framework,
    )
    watcher._prompt_captured.add("native")
    watcher._finalize_session = AsyncMock()
    return watcher


async def _checkpoint(factory, count):
    async with asyncio.timeout(5):
        while True:
            # The in-memory fixture shares one SQLite connection. Poll only
            # between writer transactions, never roll one back by closing a reader.
            async with serialized_write(factory) as session:
                checkpoint = await JobRepository(session).get_ingest_checkpoint("native")
            if checkpoint and checkpoint["record_count"] == count:
                return checkpoint
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("framework", ["copilot", "claude"])
async def test_fresh_tail_keeps_physical_decoded_emitted_and_storage_counts(framework, session_factory, tmp_path):
    watcher = await _watcher(session_factory, framework)
    records = (
        [
            {"type": "assistant.message", "data": {"content": "Inspect"}},
            {"type": "tool.execution_start", "data": {"toolCallId": "call", "toolName": "view"}},
            {"type": "tool.execution_complete", "data": {"toolCallId": "call", "result": {"content": "Body"}}},
        ]
        if framework == "copilot"
        else [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Inspect"},
                        {"type": "tool_use", "id": "call", "name": "view", "input": {}},
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "call", "content": "Body"}]},
            },
            {"type": "last-prompt"},
        ]
    )
    path = tmp_path / "events.jsonl"
    text = "\n" + "".join(json.dumps(raw) + "\n" for raw in records) + "\n"
    path.write_text(text)
    task = asyncio.create_task(watcher._tail_traceforge_events("source", "native", path))
    try:
        checkpoint = await _checkpoint(session_factory, 5)
        assert checkpoint["version"] == 2
        assert checkpoint["decoded_event_count"] == 3
        assert checkpoint["emitted_event_count"] == 2
        assert checkpoint["source"]["byte_count"] == len(text.encode())
        assert checkpoint["source"]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
        async with session_factory() as session:
            stored = await EventRepository(session).list_after(0, job_id="native")
            assert (await JobRepository(session).get("native")).tail_offset == 3
        assert len(stored) == 2
        first = stored[0].event
        digest = hashlib.sha256(
            json.dumps(records[0], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        expected = uuid.uuid5(
            uuid.NAMESPACE_URL,
            json.dumps(["codeplane-native-v1", framework, "source", 1, 0, digest], separators=(",", ":")),
        )
        assert first.id == str(expected)
        assert first.raw_event == records[0]
        assert first.payload["_codeplane_source"]["record_index"] == 1
        assert stored[1].event.payload["tool_name"] == "view"
        assert stored[0].storage_cursor != stored[1].storage_cursor
        watcher._runtime.register_external_session.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("change", ["rewrite", "truncate", "rotate"])
async def test_accepted_file_changes_stop_without_erasing_history(change, session_factory, tmp_path):
    watcher = await _watcher(session_factory, "copilot")
    path = tmp_path / "events.jsonl"
    text = '{"type":"assistant.message","data":{"content":"first"}}\n'
    path.write_text(text)
    task = asyncio.create_task(watcher._tail_traceforge_events("source", "native", path))
    await _checkpoint(session_factory, 1)
    if change == "rewrite":
        path.write_text(text.replace("first", "other"))
    elif change == "truncate":
        path.write_text("")
    else:
        path.rename(tmp_path / "rotated.jsonl")
        path.write_text(text)
    await asyncio.wait_for(task, 5)
    async with session_factory() as session:
        rows = await EventRepository(session).list_after(0, job_id="native")
        assert (await JobRepository(session).get_ingest_checkpoint("native"))["record_count"] == 1
    assert rows[0].event.payload["content"] == "first"
    assert change.removesuffix("e") in rows[-1].event.payload["message"].lower()
    assert rows[-1].event.payload["context"]["observation_status"] == "failed"
    watcher._finalize_session.assert_not_called()


async def test_incomplete_suffix_waits_for_newline_without_advancing(tmp_path):
    path = tmp_path / "source.jsonl"
    path.write_bytes(b'{"content":"\xce')
    async with NativeJsonlSource(path) as source:
        read = asyncio.create_task(anext(source))
        try:
            await asyncio.sleep(0.15)
            assert not read.done() and source.checkpoint()["byte_count"] == 0
            with path.open("ab") as stream:
                stream.write(b'\xbb"}\n\n')
            assert await asyncio.wait_for(read, 2) == '{"content":"\u03bb"}\n'.encode()
            assert await anext(source) == b"\n"
        finally:
            read.cancel()
            await asyncio.gather(read, return_exceptions=True)


@pytest.mark.parametrize("bad", ['{"broken":\n', "null\n", "[]\n", '{"value":NaN}\n', "\udcff\n"])
async def test_malformed_record_keeps_accepted_prefix(bad, session_factory, tmp_path):
    watcher = await _watcher(session_factory, "copilot")
    path = tmp_path / "events.jsonl"
    path.write_bytes(
        b'{"type":"assistant.message","data":{"content":"first"}}\n' + bad.encode(errors="surrogateescape")
    )
    await asyncio.wait_for(watcher._tail_traceforge_events("source", "native", path), 5)
    async with session_factory() as session:
        assert (await JobRepository(session).get_ingest_checkpoint("native"))["record_count"] == 1
        events = await EventRepository(session).list_after(0, job_id="native")
    assert events[0].event.payload["content"] == "first"
    assert events[-1].event.payload["context"]["observation_status"] == "failed"


async def test_oversized_physical_record_is_bounded(tmp_path):
    path = tmp_path / "source.jsonl"
    path.write_bytes(b"x" * (MAX_RECORD_BYTES + 1))
    async with NativeJsonlSource(path) as source:
        with pytest.raises(ValueError, match="exceeds 1 MiB"):
            await anext(source)
        assert source.checkpoint()["byte_count"] == 0


@pytest.mark.parametrize("complete", [True, False])
async def test_large_records_use_bounded_raw_reads_per_poll(tmp_path, monkeypatch, record_property, complete):
    path = tmp_path / "source.jsonl"
    prefix = b"x" * (MAX_RECORD_BYTES - 1)
    path.write_bytes(prefix + (b"\n" if complete else b""))

    class CountingFile(io.FileIO):
        read_calls = 0

        def read(self, size=-1):
            self.read_calls += 1
            return super().read(size)

    opened: list[CountingFile] = []
    original_open = type(path).open

    def instrumented_open(file_path, mode="r", buffering=-1, *args, **kwargs):
        if file_path == path and mode == "rb" and buffering == 0:
            stream = CountingFile(file_path, mode)
            opened.append(stream)
            return stream
        return original_open(file_path, mode, buffering, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", instrumented_open)
    async with NativeJsonlSource(path) as source:
        stream = opened[0]
        for poll in range(1 if complete else 2):
            before = stream.read_calls
            line = source._read_line()
            reads = stream.read_calls - before
            record_property(f"poll_{poll}_raw_reads", reads)
            assert reads <= 32, f"A near-1 MiB record required {reads} raw reads"
            if complete:
                assert line == prefix + b"\n"
                assert source.checkpoint()["byte_count"] == MAX_RECORD_BYTES
                assert source.checkpoint()["sha256"] == hashlib.sha256(line).hexdigest()
            else:
                assert line is None
                assert source.checkpoint()["byte_count"] == 0
                assert source.checkpoint()["sha256"] == hashlib.sha256(b"").hexdigest()
        if not complete:
            with path.open("ab") as writer:
                writer.write(b"\n")
            before = stream.read_calls
            assert source._read_line() == prefix + b"\n"
            assert stream.read_calls - before <= 32
            assert source.checkpoint()["byte_count"] == MAX_RECORD_BYTES


async def test_read_ahead_is_discarded_before_next_unaccepted_record(tmp_path):
    path = tmp_path / "source.jsonl"
    first, following = b'{"value":"first"}\n', b'{"value":"prior"}\n'
    path.write_bytes(first + following)
    async with NativeJsonlSource(path) as source:
        assert await anext(source) == first
        assert source.checkpoint()["byte_count"] == len(first)
        assert source.checkpoint()["sha256"] == hashlib.sha256(first).hexdigest()
        replacement = following.replace(b"prior", b"newer")
        with path.open("r+b", buffering=0) as writer:
            writer.seek(len(first))
            writer.write(replacement)
        assert await anext(source) == replacement
        assert source.checkpoint()["sha256"] == hashlib.sha256(first + replacement).hexdigest()


async def test_newline_that_exceeds_record_limit_is_rejected_without_progress(tmp_path):
    path = tmp_path / "source.jsonl"
    path.write_bytes(b"x" * MAX_RECORD_BYTES)
    async with NativeJsonlSource(path) as source:
        assert source._read_line() is None
        with path.open("ab") as writer:
            writer.write(b"\n")
        with pytest.raises(ValueError, match="exceeds 1 MiB"):
            await anext(source)
        assert source.checkpoint()["byte_count"] == 0
        assert source.checkpoint()["sha256"] == hashlib.sha256(b"").hexdigest()


async def test_restart_rejects_rewritten_bytes_even_if_json_semantics_match(session_factory, tmp_path):
    watcher = await _watcher(session_factory, "copilot")
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"assistant.message","data":{"content":"first"}}\n')
    task = asyncio.create_task(watcher._tail_traceforge_events("source", "native", path))
    await _checkpoint(session_factory, 1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    path.write_text('{ "type":"assistant.message","data":{"content":"first"}}\n')
    await _new_watcher(session_factory, "copilot")._tail_traceforge_events("source", "native", path)
    async with session_factory() as session:
        events = await EventRepository(session).list_after(0, job_id="native")
    assert "rewritten before its accepted checkpoint" in events[-1].event.payload["message"]
    assert events[0].event.payload["content"] == "first"


async def test_wrong_job_owner_rejected_before_decoding(session_factory, tmp_path):
    watcher = await _watcher(session_factory, "copilot")
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"assistant.message","data":{"content":"wrong owner"}}\n')
    await watcher._tail_traceforge_events("other-source", "native", path)
    async with session_factory() as session:
        assert await JobRepository(session).get_ingest_checkpoint("native") is None
        events = await EventRepository(session).list_after(0, job_id="native")
    assert len(events) == 1 and "does not belong" in events[0].event.payload["message"]


async def test_job_lifecycle_history_does_not_block_first_native_observation(session_factory, tmp_path):
    watcher = await _watcher(session_factory, "copilot")
    async with serialized_write(session_factory) as session:
        await EventRepository(session).append(new_event("native", "job.created", {"source": "copilot_cli"}))
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"session.shutdown","data":{}}\n')
    await asyncio.wait_for(watcher._tail_traceforge_events("source", "native", path), 5)
    checkpoint = await _checkpoint(session_factory, 1)
    assert checkpoint["version"] == 2
    watcher._finalize_session.assert_awaited_once()


async def test_legacy_checkpoint_without_extra_stays_readable_and_does_not_advance(
    session_factory, tmp_path, monkeypatch
):
    import builtins

    original_import = builtins.__import__

    def no_legacy(name, *args, **kwargs):
        if name == "traceforge" or name.startswith("traceforge."):
            raise ModuleNotFoundError("legacy deliberately unavailable", name="traceforge")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_legacy)
    watcher = await _watcher(session_factory, "copilot")
    checkpoint = {
        "version": 1,
        "record_count": 1,
        "external_session_id": "source",
        "mapping_sha256": "legacy-parser",
        "prefix_sha256": "legacy-prefix",
    }
    async with serialized_write(session_factory) as session:
        await JobRepository(session).update_ingest_checkpoint("native", checkpoint, 1)
        await EventRepository(session).append(new_event("native", "message.assistant", {"content": "Legacy retained"}))
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"session.shutdown","data":{}}\n')
    await watcher._tail_traceforge_events("source", "native", path)
    async with session_factory() as session:
        assert await JobRepository(session).get_ingest_checkpoint("native") == checkpoint
        events = await EventRepository(session).list_after(0, job_id="native")
    assert events[0].event.payload["content"] == "Legacy retained"
    assert "codeplane[legacy-traceforge]" in events[-1].event.payload["message"]
    watcher._finalize_session.assert_not_called()


@pytest.mark.parametrize("framework", ["copilot", "claude"])
@pytest.mark.parametrize("rewrite_after", ["first_replayed_event", "last_replayed_event"])
async def test_resume_rejects_late_accepted_rewrite_and_discards_reconstruction(
    framework, rewrite_after, session_factory, tmp_path, monkeypatch
):
    records = (
        [
            {"type": "assistant.message", "data": {"content": "Inspect"}},
            {"type": "session.info", "data": {"message": "padding" * 4096}},
            {
                "type": "tool.execution_start",
                "data": {"toolCallId": "call", "toolName": "view", "arguments": {"path": "before.py"}},
            },
        ]
        if framework == "copilot"
        else [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Inspect"}]}},
            {"type": "system", "subtype": "unmapped-padding", "padding": "padding" * 4096},
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "call", "name": "view", "input": {"path": "before.py"}}]
                },
            },
        ]
    )
    completion = (
        {"type": "tool.execution_complete", "data": {"toolCallId": "call", "result": {"content": "Body"}}}
        if framework == "copilot"
        else {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "call", "content": "Body"}]},
        }
    )
    path = tmp_path / "events.jsonl"
    prefix = b"".join((json.dumps(raw) + "\n").encode() for raw in records)
    assert prefix.index(b"before.py") > 16384
    path.write_bytes(prefix)
    first = await _watcher(session_factory, framework)
    task = asyncio.create_task(first._tail_traceforge_events("source", "native", path))
    try:
        accepted = await _checkpoint(session_factory, 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    async with session_factory() as session:
        history = await EventRepository(session).list_after(0, job_id="native")
    with path.open("ab") as stream:
        stream.write((json.dumps(completion) + "\n").encode())

    second = _new_watcher(session_factory, framework)
    process = second._event_processor.process_event
    rewritten = False

    async def rewrite_while_replaying(job_id, event, *args, **kwargs):
        nonlocal rewritten
        result = await process(job_id, event, *args, **kwargs)
        ordinal = event.payload.get("_codeplane_source", {}).get("record_index")
        target = 0 if rewrite_after == "first_replayed_event" else 2
        if kwargs.get("replay") and ordinal == target and not rewritten:
            before = path.stat()
            with path.open("r+b", buffering=0) as source:
                source.seek(prefix.index(b"before.py"))
                source.write(b"after_.py")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
            assert path.stat().st_size == before.st_size
            rewritten = True
        return result

    monkeypatch.setattr(second._event_processor, "process_event", rewrite_while_replaying)
    # A completion would terminate this bounded probe on the broken code.
    await asyncio.wait_for(
        second._tail_traceforge_events("source", "native", path, finalize_on_raw=lambda raw: (raw == completion, None)),
        5,
    )
    assert rewritten
    async with session_factory() as session:
        assert await JobRepository(session).get_ingest_checkpoint("native") == accepted
        rows = await EventRepository(session).list_after(0, job_id="native")
    assert rows[: len(history)] == history
    assert len(rows) == len(history) + 1
    assert "rewritten" in rows[-1].event.payload["message"].lower()
    second._finalize_session.assert_not_called()
    # Shutdown must not publish a start reconstructed from an invalid prefix.
    await second._event_processor.on_job_terminal("native", "source_failed")
    async with session_factory() as session:
        assert await EventRepository(session).list_after(0, job_id="native") == rows
