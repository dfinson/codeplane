"""Real local acquisition through registration, canonical receipts and replay."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.models.db import Base
from backend.models.domain import StateConflictError
from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.ingest.acquisition import AcquisitionChannel
from backend.services.ingest.acquisition_checkpoint import AcquisitionCheckpoint
from backend.services.ingest.acquisition_sources import (
    MAX_BYTES,
    AcquiredPage,
    QueueSource,
    SourceRecord,
    callback_page,
    file_journal,
    file_snapshot,
    http_snapshot,
    sqlite_snapshot,
    sse_page,
)
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.mapping import PARSER_REVISION, canonical_record
from backend.services.ingest.record_service import ObservationRecords
from backend.tests.unit.test_approval_service import session_factory as session_factory


async def _channel(factory, format_id="copilot", binding="binding", source="source"):
    bus = EventBus()
    async with factory() as session:
        job = await HistoryRegistration(
            IntegrationRepository(session), JobRepository(session), ProjectRepository(session)
        ).register(binding_id=binding, project_id="proj-1", format_id=format_id, external_session_id="producer")
        await session.commit()
    return AcquisitionChannel(factory, bus, binding_id=binding, source_key=source), job.id


async def _history(factory, job_id):
    async with factory() as session:
        return await EventRepository(session).list_after(0, job_id=job_id)


async def _checkpoint(factory, binding="binding", source="source"):
    async with factory() as session:
        return await IntegrationRepository(session).checkpoint(binding, source, PARSER_REVISION)


def _message(identity, text):
    return {"id": identity, "type": "assistant.message", "data": {"content": text}}


@asynccontextmanager
async def _http_server(respond):
    tasks = set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            await respond(request, writer)
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        for task in tuple(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


async def _reply(writer, body, content_type="application/json"):
    writer.write(
        f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def _reply_sse_chunks(writer, chunks):
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    )
    for chunk in chunks:
        writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
        await writer.drain()
        await asyncio.sleep(0)
    writer.write(b"0\r\n\r\n")
    await writer.drain()


async def test_file_snapshots_diff_stable_records_across_replacement_and_restart(session_factory, tmp_path):
    channel, job = await _channel(session_factory)
    path = tmp_path / "snapshot.json"
    first = _message("first", "First")
    second = _message("second", "Second")
    path.write_text(json.dumps([first]))
    receipt = await channel.accept(await file_snapshot(path, identity_field="id"))
    assert receipt.accepted_records == receipt.next_index == 1
    retained = await _history(session_factory, job)
    assert await channel.accept(await file_snapshot(path, identity_field="id")) is None
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps([second, first]))
    replacement.replace(path)
    restarted = AcquisitionChannel(session_factory, EventBus(), binding_id="binding", source_key="source")
    receipt = await restarted.accept(await file_snapshot(path, identity_field="id"))
    assert receipt.accepted_records == 1 and receipt.next_index == 2
    history = await _history(session_factory, job)
    assert history[: len(retained)] == retained
    assert [row.event.payload["content"] for row in history if row.event.kind == "message.assistant"] == [
        "First",
        "Second",
    ]
    receipts = [row.event for row in history if row.event.kind == "integration.batch.accepted"]
    assert [event.raw_event["records"] for event in receipts] == [[first], [second]]
    state = (await _checkpoint(session_factory)).buffer["acquisition"]["state"]
    assert state["contract"] == "native-acquisition-records-v2"
    assert len(state["records"]) == 1
    assert [event.raw_event["acquisition"]["state"]["records"][0]["identity"] for event in receipts] == [
        '"first"',
        '"second"',
    ]


@pytest.mark.parametrize("defect", ["rewrite", "truncate", "sequence"])
async def test_accepted_snapshot_changes_fence_progress_without_losing_history(defect, session_factory, tmp_path):
    channel, job = await _channel(session_factory)
    path = tmp_path / "snapshot.json"
    records = [{**_message("first", "First"), "seq": 41}, {**_message("second", "Second"), "seq": 42}]
    path.write_text(json.dumps(records))
    await channel.accept(await file_snapshot(path, identity_field="id", sequence_field="seq"))
    before = await _history(session_factory, job)
    if defect == "rewrite":
        records[0]["data"]["content"] = "Changed"
    elif defect == "truncate":
        records.pop()
    else:
        records[0]["seq"] = 99
    path.write_text(json.dumps(records))
    with pytest.raises(StateConflictError, match="rewritten|truncated"):
        await channel.accept(await file_snapshot(path, identity_field="id", sequence_field="seq"))
    assert await _history(session_factory, job) == before
    assert (await _checkpoint(session_factory)).state == "reconciliation_required"


async def test_native_snapshot_envelope_and_complete_journal_resume(session_factory, tmp_path):
    channel, job = await _channel(session_factory, "continue_dev")
    path = tmp_path / "continue.json"
    document = {"sessionId": "s1", "history": [{"message": {"role": "assistant", "content": "Before tool"}}]}
    path.write_text(json.dumps(document))
    first = await file_snapshot(path, records_path=("history",), envelope_fields=("sessionId",))
    await channel.accept(first)
    document["history"].append(
        {
            "message": {
                "role": "assistant",
                "toolCalls": [{"id": "call", "function": {"name": "view", "arguments": '{"path":"a.py"}'}}],
            }
        }
    )
    path.write_text(json.dumps(document))
    await channel.accept(await file_snapshot(path, records_path=("history",), envelope_fields=("sessionId",)))
    assert len([row for row in await _history(session_factory, job) if row.event.kind == "message.assistant"]) == 1
    channel, job = await _channel(session_factory, "copilot_vscode", binding="journal")
    journal = tmp_path / "journal.jsonl"
    start = {"kind": 0, "v": {"version": 3, "requests": [{"requestId": "request-1", "modelId": "model-a"}]}}
    journal.write_text(json.dumps(start) + "\n\n" + '{"kind":')
    page = await file_journal(journal)
    assert page.incomplete_tail and page.physical_count == 2
    await channel.accept(page)
    appended = {"kind": 2, "k": ["requests", 0, "response"], "v": [{"value": "Answer"}]}
    journal.write_text(json.dumps(start) + "\n\n" + json.dumps(appended) + "\n")
    receipt = await channel.accept(await file_journal(journal))
    assert receipt.next_index == 2
    answer = next(row.event for row in await _history(session_factory, job) if row.event.kind == "message.assistant")
    assert answer.payload["request_id"] == "request-1"
    assert answer.payload["model"] == "model-a"
    assert await channel.accept(await file_journal(journal)) is None


async def test_sqlite_rows_are_read_only_snapshot_bound_and_keep_sequence(session_factory, tmp_path):
    path = tmp_path / "producer.db"
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, seq INTEGER, role TEXT, content_json TEXT, session_id TEXT)"
        )
        database.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?)",
            (
                "row-a",
                41,
                "assistant",
                '[{"type":"toolRequest","id":"call","toolCall":{"status":"success",'
                '"value":{"name":"view","arguments":{"path":"a.py"}}}}]',
                "producer",
            ),
        )
    channel, job = await _channel(session_factory, "goose")
    options = dict(table="messages", identity_column="id", sequence_column="seq", filters={"session_id": "producer"})
    page = await sqlite_snapshot(path, **options)
    assert page.records[0].sequence == 41 and page.records[0].raw["id"] == "row-a"
    await channel.accept(page)
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?)",
            (
                "row-b",
                42,
                "user",
                '[{"type":"toolResponse","id":"call","toolResult":{"status":"success","value":{"content":"Body"}}}]',
                "producer",
            ),
        )
        database.execute("INSERT INTO messages VALUES (?,?,?,?,?)", ("other", 1, "user", "[]", "other"))
    receipt = await channel.accept(await sqlite_snapshot(path, **options))
    assert receipt.accepted_records == 1 and receipt.next_index == 2
    events = await _history(session_factory, job)
    completion = next(row.event for row in events if row.event.kind == "tool.call.completed")
    assert completion.payload["tool_call_id"] == "call"
    assert completion.payload["tool_name"] == "view" and completion.payload["result"] == "Body"
    state = (await _checkpoint(session_factory)).buffer["acquisition"]["state"]
    assert [record["sequence"] for record in state["records"]] == [42]
    assert [
        record["sequence"]
        for row in events
        if row.event.kind == "integration.batch.accepted"
        for record in row.event.raw_event["acquisition"]["state"]["records"]
    ] == [41, 42]
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


async def test_sqlite_snapshot_does_not_mix_a_concurrent_writer_transaction(tmp_path, monkeypatch):
    path = tmp_path / "producer.db"
    connect = sqlite3.connect
    with connect(path) as database:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, seq INTEGER, content TEXT)")
        database.executemany("INSERT INTO messages VALUES (?,?,?)", [("a", 41, "before-a"), ("b", 42, "before-b")])
    changed = False

    class ConcurrentConnection(sqlite3.Connection):
        def execute(self, query, parameters=()):
            if query.startswith("SELECT *"):

                def row_factory(cursor, values):
                    nonlocal changed
                    if not changed:
                        changed = True
                        with connect(path, timeout=1) as writer:
                            writer.execute("UPDATE messages SET content='after-b' WHERE id='b'")
                            writer.execute("INSERT INTO messages VALUES ('c',43,'after-c')")
                    return sqlite3.Row(cursor, values)

                self.row_factory = row_factory
            return super().execute(query, parameters)

    def snapshot_connection(*args, **kwargs):
        return connect(*args, **kwargs, factory=ConcurrentConnection)

    monkeypatch.setattr(sqlite3, "connect", snapshot_connection)
    page = await sqlite_snapshot(path, table="messages", identity_column="id", sequence_column="seq")
    assert changed
    assert [record.raw["content"] for record in page.records] == ["before-a", "before-b"]
    with connect(path) as database:
        assert database.execute("SELECT content FROM messages ORDER BY seq").fetchall() == [
            ("before-a",),
            ("after-b",),
            ("after-c",),
        ]


async def test_loopback_http_polling_is_deduplicated_and_bounded(session_factory):
    channel, job = await _channel(session_factory)
    records = [_message("first", "First")]

    async def respond(request, writer):
        await _reply(writer, json.dumps(records).encode())

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        url = base + "/snapshot"
        await channel.accept(await http_snapshot(client, url, identity_field="id"))
        before = await _history(session_factory, job)
        assert await channel.accept(await http_snapshot(client, url, identity_field="id")) is None
        assert await _history(session_factory, job) == before
        records.append(_message("second", "Second"))
        assert (await channel.accept(await http_snapshot(client, url, identity_field="id"))).accepted_records == 1
        records.append(_message("large", "x" * MAX_BYTES))
        with pytest.raises(ValueError, match="exceeds 1 MiB"):
            await http_snapshot(client, url, identity_field="id")
    assert (await _checkpoint(session_factory)).record_count == 2


async def test_loopback_sse_disconnect_reconnect_duplicate_and_partial_frames(session_factory):
    channel, job = await _channel(session_factory)
    requests = []
    first, second = _message("first", "First"), _message("second", "Second")

    async def respond(request, writer):
        requests.append(request)
        body = f": heartbeat\n\nid: 41\ndata: {json.dumps(first)}\n\n".encode()
        if len(requests) == 1:
            body += b'id: 42\ndata: {"incomplete":'
        else:
            body += f"id: 42\r\ndata: {json.dumps(second)}\r\n\r\n".encode()
        await _reply(writer, body, "text/event-stream")

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        page = await sse_page(client, base + "/events")
        assert page.incomplete_tail
        await channel.accept(page)
        assert await channel.last_event_id() == "41"
        restored = AcquisitionChannel(session_factory, EventBus(), binding_id="binding", source_key="source")
        page = await sse_page(client, base + "/events", last_event_id=await restored.last_event_id())
        assert (await restored.accept(page)).accepted_records == 1
        assert await restored.last_event_id() == "42"
    assert b"Last-Event-ID: 41" in requests[1]
    messages = [row.event for row in await _history(session_factory, job) if row.event.kind == "message.assistant"]
    assert [event.payload["content"] for event in messages] == ["First", "Second"]


async def test_sse_cr_only_framing_and_missing_identity_are_explicit():
    body = b'id: one\rdata: {"type":"assistant.message","data":{"content":"CR"}}\r\r'

    async def respond(request, writer):
        await _reply(writer, body, "text/event-stream")

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        page = await sse_page(client, base)
        assert not page.incomplete_tail and len(page.records) == 1
        assert page.records[0].identity == "one"
        body = b'data: {"type":"assistant.message"}\n\n'
        with pytest.raises(ValueError, match="stable event id"):
            await sse_page(client, base)


async def test_queue_pending_survives_atomic_failure_and_cancel(session_factory, monkeypatch):
    channel, job = await _channel(session_factory)
    queue: asyncio.Queue[SourceRecord] = asyncio.Queue(maxsize=2)
    source = QueueSource(queue, identity="callback-source")
    item = SourceRecord("callback-1", _message("first", "First"), 17)
    original_item = deepcopy(item)
    await queue.put(item)
    append = EventRepository.append

    async def fail_receipt(repo, event):
        if event.kind == "integration.batch.accepted":
            raise OSError("receipt unavailable")
        return await append(repo, event)

    with monkeypatch.context() as scoped:
        scoped.setattr(EventRepository, "append", fail_receipt)
        with pytest.raises(OSError, match="receipt unavailable"):
            await channel.accept_queue(source)
    assert await _history(session_factory, job) == []
    assert await _checkpoint(session_factory) is None
    item.raw["data"]["content"] = "Caller mutated after failed delivery"
    assert (await source.read()).records == (original_item,)
    assert (await channel.accept_queue(source)).accepted_records == 1
    await asyncio.wait_for(queue.join(), 1)
    waiting = asyncio.create_task(channel.accept_queue(source))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await queue.put(original_item)
    assert await channel.accept_queue(source) is None
    await asyncio.wait_for(queue.join(), 1)
    assert (await _checkpoint(session_factory)).record_count == 1
    usage = {"type": "assistant.usage", "data": {"model": "model-a", "inputTokens": 0, "outputTokens": 3}}
    await channel.accept(callback_page("callback-source", (SourceRecord("usage", usage, 18),)))
    events = await _history(session_factory, job)
    measured = next(row.event for row in events if row.event.kind == "telemetry.usage")
    assert measured.payload["input_tokens"] == 0 and measured.payload["output_tokens"] == 3
    assert "cost_usd" not in measured.payload


async def test_concurrent_queue_consumers_do_not_replace_unacknowledged_delivery(session_factory):
    channel, job = await _channel(session_factory)
    queue: asyncio.Queue[SourceRecord] = asyncio.Queue(maxsize=2)
    source = QueueSource(queue, identity="callbacks")
    await queue.put(SourceRecord("a", _message("first", "First")))
    await queue.put(SourceRecord("b", _message("second", "Second")))
    receipts = await asyncio.gather(channel.accept_queue(source), channel.accept_queue(source))
    assert [receipt.next_index for receipt in receipts] == [1, 2]
    await asyncio.wait_for(queue.join(), 1)
    messages = [row.event for row in await _history(session_factory, job) if row.event.kind == "message.assistant"]
    assert [event.payload["content"] for event in messages] == ["First", "Second"]


async def test_cancellation_inside_receipt_transaction_keeps_queue_item_pending(session_factory, monkeypatch):
    channel, job = await _channel(session_factory)
    queue: asyncio.Queue[SourceRecord] = asyncio.Queue(maxsize=1)
    item = SourceRecord("first", _message("first", "First"))
    await queue.put(item)
    source = QueueSource(queue, identity="callback")
    entered = asyncio.Event()
    append = EventRepository.append

    async def hold_receipt(repo, event):
        if event.kind == "integration.batch.accepted":
            entered.set()
            await asyncio.Event().wait()
        return await append(repo, event)

    with monkeypatch.context() as scoped:
        scoped.setattr(EventRepository, "append", hold_receipt)
        task = asyncio.create_task(channel.accept_queue(source))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await _checkpoint(session_factory) is None
    assert await _history(session_factory, job) == []
    assert (await source.read()).records == (item,)
    assert (await channel.accept_queue(source)).accepted_records == 1
    await asyncio.wait_for(queue.join(), 1)


async def test_decoder_change_cannot_advance_acquired_source(session_factory, tmp_path, monkeypatch):
    from backend.services.ingest import record_service

    channel, job = await _channel(session_factory)
    path = tmp_path / "source.json"
    records = [_message("first", "First")]
    path.write_text(json.dumps(records))
    await channel.accept(await file_snapshot(path, identity_field="id"))
    history = await _history(session_factory, job)
    original = record_service.resolve_mapping_path("copilot")
    mapping = tmp_path / "copilot.yaml"
    mapping.write_bytes(original.read_bytes() + b"\n# changed producer-contract implementation\n")
    monkeypatch.setattr(record_service, "resolve_mapping_path", lambda name: mapping)
    path.write_text(json.dumps([*records, _message("second", "Second")]))
    with pytest.raises(StateConflictError, match="implementation changed"):
        await channel.accept(await file_snapshot(path, identity_field="id"))
    assert await _history(session_factory, job) == history
    assert (await _checkpoint(session_factory)).record_count == 1


async def test_http_cancellation_does_not_advance_acquisition(session_factory):
    channel, job = await _channel(session_factory)
    requested = asyncio.Event()

    async def respond(request, writer):
        requested.set()
        await asyncio.Event().wait()

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        waiting = asyncio.create_task(http_snapshot(client, base))
        await requested.wait()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    assert await _checkpoint(session_factory) is None
    assert await _history(session_factory, job) == []


async def test_checkpoint_source_identity_cannot_change_and_plain_import_cannot_erase_state(session_factory, tmp_path):
    channel, job = await _channel(session_factory)
    original = callback_page("original", (SourceRecord("id", _message("first", "First")),))
    receipt = await channel.accept(original)
    history = await _history(session_factory, job)
    async with session_factory() as session:
        with pytest.raises(StateConflictError):
            await ObservationRecords(IntegrationRepository(session), JobRepository(session), EventBus()).append(
                "binding",
                ObservationRecordBatch(
                    source_key="source",
                    start_index=1,
                    prefix_sha256=receipt.prefix_sha256,
                    records=[_message("next", "Next")],
                ),
            )
    changed = AcquiredPage({"transport": "in_process", "identity": "different"}, original.records, mode="stream")
    with pytest.raises(StateConflictError, match="identity"):
        await channel.accept(changed)
    assert await _history(session_factory, job) == history


async def test_incomplete_or_duplicate_records_never_invent_progress(session_factory, tmp_path):
    channel, job = await _channel(session_factory)
    path = tmp_path / "snapshot.json"
    path.write_text('{"unfinished":')
    with pytest.raises(ValueError, match="Malformed"):
        await file_snapshot(path)
    row = _message("same", "Same")
    path.write_text(json.dumps([row, deepcopy(row)]))
    with pytest.raises(ValueError, match="duplicate"):
        await file_snapshot(path, identity_field="id")
    assert await channel.accept(callback_page("empty", ())) is None
    assert await _checkpoint(session_factory) is None
    assert await _history(session_factory, job) == []


async def test_explicit_finish_preserves_partial_tool_evidence_without_managed_outcome(session_factory):
    channel, job = await _channel(session_factory)
    record = SourceRecord(
        "call",
        {
            "type": "tool.execution_start",
            "data": {"toolCallId": "call", "toolName": "view", "arguments": {"path": "a.py"}},
        },
    )
    await channel.accept(callback_page("callbacks", (record,)))
    receipt = await channel.finish()
    assert receipt.session_closed and receipt.accepted_records == 0 and receipt.next_index == 1
    before = await _history(session_factory, job)
    assert await channel.finish() is None
    assert await _history(session_factory, job) == before
    tools = [row.event for row in before if row.event.kind.startswith("tool.call.")]
    assert len(tools) == 1 and tools[0].kind == "tool.call.started"
    assert tools[0].payload["_codeplane_pairing"]["reason"] == "missing_completion"
    async with session_factory() as session:
        assert (await JobRepository(session).get(job)).state.value == "running"


async def _history_fingerprint(factory, job_id):
    digest = hashlib.sha256()
    messages = 0
    cursor = 0
    async with factory() as session:
        repository = EventRepository(session)
        while rows := await repository.list_after(cursor, job_id=job_id, limit=200):
            for row in rows:
                digest.update(row.event.model_dump_json().encode())
                messages += int(row.event.kind == "message.assistant")
            cursor = rows[-1].storage_cursor
    return messages, cursor, digest.hexdigest()


@pytest.mark.parametrize("defect", ["rewrite", "resequence"])
async def test_long_lived_source_keeps_old_identity_after_disk_restart_and_retry(tmp_path, defect):
    database = tmp_path / "long-lived.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await ProjectRepository(session).create("proj-1", "Long-lived acquisition", [])
            await session.commit()
        channel, job = await _channel(factory)
        first_page = None
        for start, count in ((0, 400), (400, 400), (800, 205)):
            page = callback_page(
                "long-lived",
                tuple(
                    SourceRecord(str(index), _message(str(index), f"Record {index}"), index)
                    for index in range(start, start + count)
                ),
            )
            page = AcquiredPage(page.source, page.records, mode="stream", physical_count=count)
            if first_page is None:
                first_page = page
            receipt = await channel.accept(page)
            assert receipt.accepted_records == count and receipt.next_index == start + count
        checkpoint = await _checkpoint(factory)
        assert len(checkpoint.buffer["acquisition"]["state"]["records"]) <= 400
        before = await _history_fingerprint(factory, job)
        assert before[0] == 1005
    finally:
        await engine.dispose()
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        restarted = AcquisitionChannel(factory, EventBus(), binding_id="binding", source_key="source")
        assert first_page is not None
        assert await restarted.accept(first_page) is None
        assert await _history_fingerprint(factory, job) == before
        queue: asyncio.Queue[SourceRecord] = asyncio.Queue(maxsize=1)
        await queue.put(SourceRecord("1005", _message("1005", "Record 1005"), 1005))
        assert (await restarted.accept_queue(QueueSource(queue, identity="long-lived"))).next_index == 1006
        await asyncio.wait_for(queue.join(), 1)
        after = await _history_fingerprint(factory, job)
        assert after[0] == 1006
        old = first_page.records[0]
        changed = (
            SourceRecord(old.identity, _message("0", "Rewritten"), old.sequence)
            if defect == "rewrite"
            else SourceRecord(old.identity, old.raw, 99999)
        )
        with pytest.raises(StateConflictError, match="rewritten or resequenced"):
            await restarted.accept(callback_page("long-lived", (changed,)))
        assert await _history_fingerprint(factory, job) == after
        assert (await _checkpoint(factory)).state == "reconciliation_required"
    finally:
        await engine.dispose()


async def test_old_cumulative_receipts_remain_authoritative_after_checkpoint_compaction(session_factory):
    channel, job = await _channel(session_factory)
    first = SourceRecord("a", _message("a", "A"), 41)
    second = SourceRecord("b", _message("b", "B"), 42)
    source = callback_page("legacy", (first,))
    inventory = []
    prefix = "0" * 64
    async with session_factory() as session:
        service = ObservationRecords(IntegrationRepository(session), JobRepository(session), EventBus())
        for index, record in enumerate((first, second)):
            inventory.append(
                {
                    "identity": record.identity,
                    "sequence": record.sequence,
                    "sha256": hashlib.sha256(canonical_record(record.raw)).hexdigest(),
                }
            )
            receipt = await service.append(
                "binding",
                ObservationRecordBatch(
                    source_key="source",
                    start_index=index,
                    prefix_sha256=prefix,
                    records=[record.raw],
                ),
                acquisition=AcquisitionCheckpoint(
                    source_identity_sha256=source.source_digest,
                    expected_checkpoint_version=index,
                    state={"contract": "native-acquisition-records-v1", "records": deepcopy(inventory)},
                ),
            )
            prefix = receipt.prefix_sha256
    third = SourceRecord("c", _message("c", "C"), 43)
    assert (await channel.accept(callback_page("legacy", (first, second, third)))).accepted_records == 1
    state = (await _checkpoint(session_factory)).buffer["acquisition"]["state"]
    assert state["contract"] == "native-acquisition-records-v2"
    assert [record["identity"] for record in state["records"]] == ["c"]
    before = await _history_fingerprint(session_factory, job)
    assert before[0] == 3
    assert await channel.accept(callback_page("legacy", (first, second))) is None
    assert await _history_fingerprint(session_factory, job) == before


async def test_snapshot_truncation_checks_all_receipts_not_only_last_checkpoint_page(session_factory, tmp_path):
    channel, job = await _channel(session_factory)
    path = tmp_path / "snapshot.json"
    first, second = _message("a", "A"), _message("b", "B")
    path.write_text(json.dumps([first]))
    await channel.accept(await file_snapshot(path, identity_field="id"))
    path.write_text(json.dumps([first, second]))
    await channel.accept(await file_snapshot(path, identity_field="id"))
    before = await _history_fingerprint(session_factory, job)
    path.write_text(json.dumps([second]))
    with pytest.raises(StateConflictError, match="truncated"):
        await channel.accept(await file_snapshot(path, identity_field="id"))
    assert await _history_fingerprint(session_factory, job) == before


async def test_missing_receipt_history_is_not_treated_as_fresh_identity(session_factory):
    channel, job = await _channel(session_factory)
    first = callback_page("callbacks", (SourceRecord("first", _message("first", "First")),))
    await channel.accept(first)
    async with session_factory() as session:
        repository = EventRepository(session)
        receipt = next(
            row for row in await repository.list_after(0, job_id=job) if row.event.kind == "integration.batch.accepted"
        )
        await repository.delete_event(receipt.event.id)
        await session.commit()
    before = await _history_fingerprint(session_factory, job)
    with pytest.raises(StateConflictError, match="receipt history"):
        await channel.accept(first)
    assert await _history_fingerprint(session_factory, job) == before


@pytest.mark.parametrize("limit", ["records", "bytes"])
async def test_per_page_bounds_remain_independent_of_source_lifetime(session_factory, limit):
    channel, job = await _channel(session_factory)
    records = (
        tuple(SourceRecord(str(index), _message(str(index), "Small")) for index in range(1001))
        if limit == "records"
        else (SourceRecord("large", _message("large", "x" * MAX_BYTES)),)
    )
    with pytest.raises(ValueError, match="1000 records|1 MiB"):
        await channel.accept(callback_page("bounded", records))
    assert await _checkpoint(session_factory) is None
    assert await _history(session_factory, job) == []


async def test_receipt_identity_lookup_is_scoped_to_the_acquisition_channel(session_factory):
    first, job = await _channel(session_factory, source="first-channel")
    second, same_job = await _channel(session_factory, source="second-channel")
    assert same_job == job
    await first.accept(callback_page("shared-origin", (SourceRecord("same-id", _message("same-id", "First"), 1),)))
    await second.accept(callback_page("shared-origin", (SourceRecord("same-id", _message("same-id", "Second"), 2),)))
    before = await _history_fingerprint(session_factory, job)
    assert before[0] == 2
    assert (
        await first.accept(callback_page("shared-origin", (SourceRecord("same-id", _message("same-id", "First"), 1),)))
        is None
    )
    assert await _history_fingerprint(session_factory, job) == before


async def test_sse_idle_timeout_returns_and_commits_complete_prefix(session_factory):
    channel, job = await _channel(session_factory)
    first, second = _message("first", "First"), _message("second", "Second")
    requests = []

    async def respond(request, writer):
        requests.append(request)
        if len(requests) == 1:
            frame = f"id: first\ndata: {json.dumps(first)}\n\n".encode() + b'id: second\ndata: {"partial":'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                + f"{len(frame):x}\r\n".encode()
                + frame
                + b"\r\n"
            )
            await writer.drain()
            await asyncio.Event().wait()
        else:
            await _reply(writer, f"id: second\ndata: {json.dumps(second)}\n\n".encode(), "text/event-stream")

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        page = await asyncio.wait_for(sse_page(client, base), 7)
        assert [record.identity for record in page.records] == ["first"]
        assert page.incomplete_tail
        receipt = await channel.accept(page)
        assert receipt.accepted_records == 1 and not receipt.session_closed
        page = await sse_page(client, base, last_event_id=await channel.last_event_id())
        assert (await channel.accept(page)).accepted_records == 1
    assert b"Last-Event-ID: first" in requests[1]
    assert (await _history_fingerprint(session_factory, job))[0] == 2


@pytest.mark.parametrize("accepted_prefix", [False, True])
async def test_sse_individually_oversized_record_remains_an_explicit_failure(session_factory, accepted_prefix):
    channel, job = await _channel(session_factory)

    async def respond(request, writer):
        body = b"id: too-large\ndata: " + json.dumps(_message("large", "x" * MAX_BYTES)).encode() + b"\n\n"
        if accepted_prefix and b"Last-Event-ID: first" not in request:
            body = f"id: first\ndata: {json.dumps(_message('first', 'First'))}\n\n".encode() + body
        await _reply(writer, body, "text/event-stream")

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        if accepted_prefix:
            assert (await channel.accept(await sse_page(client, base))).accepted_records == 1
        before = await _history_fingerprint(session_factory, job)
        with pytest.raises(ValueError, match="1 MiB"):
            await sse_page(client, base, last_event_id=await channel.last_event_id())
    assert await _history_fingerprint(session_factory, job) == before
    assert before[0] == int(accepted_prefix)


async def test_sse_cancellation_after_complete_frame_is_not_mistaken_for_idle_delivery(session_factory, monkeypatch):
    from backend.services.ingest import acquisition_sources

    channel, job = await _channel(session_factory)
    parsed = asyncio.Event()
    decode = acquisition_sources.decode_json_body

    def observe_decode(body):
        raw = decode(body)
        parsed.set()
        return raw

    monkeypatch.setattr(acquisition_sources, "decode_json_body", observe_decode)

    async def respond(request, writer):
        frame = f"id: first\ndata: {json.dumps(_message('first', 'First'))}\n\n".encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            + f"{len(frame):x}\r\n".encode()
            + frame
            + b"\r\n"
        )
        await writer.drain()
        await asyncio.Event().wait()

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        task = asyncio.create_task(sse_page(client, base))
        await asyncio.wait_for(parsed.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await _checkpoint(session_factory) is None
    assert await _history(session_factory, job) == []


async def test_sse_page_byte_boundary_does_not_discard_a_complete_large_record(session_factory):
    channel, job = await _channel(session_factory)
    first, second = _message("first", "x" * 600_000), _message("second", "y" * 600_000)
    requests = []

    async def respond(request, writer):
        requests.append(request)
        records = [second] if b"Last-Event-ID: first" in request else [first, second]
        body = b"".join(f"id: {raw['id']}\ndata: {json.dumps(raw)}\n\n".encode() for raw in records)
        await _reply(writer, body, "text/event-stream")

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        page = await sse_page(client, base)
        assert [record.identity for record in page.records] == ["first"]
        assert (await channel.accept(page)).accepted_records == 1
        page = await sse_page(client, base, last_event_id=await channel.last_event_id())
        assert [record.identity for record in page.records] == ["second"]
        assert (await channel.accept(page)).accepted_records == 1
    assert (await _history_fingerprint(session_factory, job))[0] == 2


@pytest.mark.parametrize("sequence", [9223372036854775809, -9223372036854775809])
async def test_big_native_sequence_round_trips_losslessly_and_keeps_integer_type(session_factory, sequence):
    channel, job = await _channel(session_factory)
    raw = _message("event", "Exact sequence")
    page = callback_page("big-sequence", (SourceRecord("event", raw, sequence),))
    await channel.accept(page)
    before = await _history_fingerprint(session_factory, job)
    assert await channel.accept(page) is None
    assert await _history_fingerprint(session_factory, job) == before
    with pytest.raises(StateConflictError, match="resequenced"):
        await channel.accept(callback_page("big-sequence", (SourceRecord("event", raw, str(sequence)),)))
    assert await _history_fingerprint(session_factory, job) == before


@pytest.mark.parametrize("split", [False, True])
async def test_sse_reconnect_strips_only_the_stream_start_bom(session_factory, split):
    channel, job = await _channel(session_factory)
    requests = []
    first, second = _message("old", "Old"), _message("new", "Inside \ufeff payload")

    async def respond(request, writer):
        requests.append(request)
        if len(requests) == 1:
            await _reply(writer, f"id: old\ndata: {json.dumps(first)}\n\n".encode(), "text/event-stream")
        else:
            frame = f"id: new\ndata: {json.dumps(second, ensure_ascii=False)}\n\n".encode()
            chunks = [b"\xef", b"\xbb", b"\xbf" + frame] if split else [b"\xef\xbb\xbf" + frame]
            await _reply_sse_chunks(writer, chunks)

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        await channel.accept(await sse_page(client, base))
        page = await sse_page(client, base, last_event_id=await channel.last_event_id())
        assert (await channel.accept(page)).accepted_records == 1
        assert await channel.last_event_id() == "new"
    messages = [row.event for row in await _history(session_factory, job) if row.event.kind == "message.assistant"]
    assert [event.payload["content"] for event in messages] == ["Old", "Inside \ufeff payload"]


async def test_sse_does_not_strip_a_second_initial_bom():
    async def respond(request, writer):
        body = b"\xef\xbb\xbf\xef\xbb\xbfid: event\ndata: " + json.dumps(_message("event", "Value")).encode() + b"\n\n"
        await _reply_sse_chunks(writer, [body])

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(ValueError, match="stable event id"):
            await sse_page(client, base)


async def test_explicit_empty_sse_final_page_keeps_last_committed_identity(session_factory):
    channel, job = await _channel(session_factory)

    async def respond(request, writer):
        body = (
            b": heartbeat\n\n"
            if b"Last-Event-ID: first" in request
            else f"id: first\ndata: {json.dumps(_message('first', 'First'))}\n\n".encode()
        )
        await _reply(writer, body, "text/event-stream")

    async with _http_server(respond) as base, httpx.AsyncClient(trust_env=False) as client:
        await channel.accept(await sse_page(client, base))
        page = await sse_page(client, base, last_event_id=await channel.last_event_id())
        receipt = await channel.finish(final_page=page)
        assert receipt.session_closed and receipt.accepted_records == 0 and receipt.next_index == 1
        assert await channel.last_event_id() == "first"
        before = await _history_fingerprint(session_factory, job)
        assert await channel.finish(final_page=page) is None
        assert await _history_fingerprint(session_factory, job) == before


@pytest.mark.parametrize("terminal", [False, True])
async def test_completed_journal_consumes_valid_unterminated_object_before_finish(session_factory, tmp_path, terminal):
    channel, job = await _channel(session_factory)
    first = _message("first", "First")
    last = {"type": "session.shutdown", "data": {}} if terminal else _message("last", "Last")
    path = tmp_path / "journal.jsonl"
    path.write_text(json.dumps(first) + "\n" + json.dumps(last))
    active = await file_journal(path)
    assert active.incomplete_tail and len(active.records) == 1
    await channel.accept(active)
    with pytest.raises(ValueError, match="incomplete tail"):
        await channel.finish(final_page=active)
    receipt = await channel.finish(final_page=await file_journal(path, final=True))
    assert receipt.session_closed and receipt.next_index == 2 and receipt.accepted_records == 1
    before = await _history_fingerprint(session_factory, job)
    assert await channel.finish(final_page=await file_journal(path, final=True)) is None
    assert await _history_fingerprint(session_factory, job) == before
    receipts = [
        row.event for row in await _history(session_factory, job) if row.event.kind == "integration.batch.accepted"
    ]
    assert receipts[-1].raw_event["records"] == [last]


async def test_completed_journal_rejects_malformed_final_remainder_before_closure(session_factory, tmp_path):
    channel, job = await _channel(session_factory)
    path = tmp_path / "journal.jsonl"
    path.write_text(json.dumps(_message("first", "First")) + '\n{"broken":')
    await channel.accept(await file_journal(path))
    before = await _history_fingerprint(session_factory, job)
    with pytest.raises(ValueError, match="Malformed"):
        await channel.finish(final_page=await file_journal(path, final=True))
    assert await _history_fingerprint(session_factory, job) == before
    async with session_factory() as session:
        assert not (await IntegrationRepository(session).projection("binding")).buffer["closed"]
