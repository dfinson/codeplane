"""Bounded acquisition only: no decoding, credentials, registration or scheduling."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx

from backend.services.ingest.mapping import canonical_record
from backend.services.ingest.otel_decoder import decode_json_body

if TYPE_CHECKING:
    from pathlib import Path

MAX_BYTES = 1024 * 1024
MAX_RECORDS = 1000
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


@dataclass(frozen=True)
class SourceRecord:
    identity: str
    raw: dict[str, Any]
    sequence: int | str | None = None


@dataclass(frozen=True)
class AcquiredPage:
    source: dict[str, Any]
    records: tuple[SourceRecord, ...]
    mode: Literal["snapshot", "stream"] = "snapshot"
    incomplete_tail: bool = False
    physical_count: int | None = None

    @property
    def source_digest(self) -> str:
        return hashlib.sha256(
            canonical_record({"acquisition_version": 1, "mode": self.mode, "source": self.source})
        ).hexdigest()


def _identity(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
        raise ValueError("Acquisition requires a nonempty string or integer record identity")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _records(
    document: Any,
    *,
    records_path: tuple[str, ...],
    identity_field: str | None,
    sequence_field: str | None,
    envelope_fields: tuple[str, ...],
) -> tuple[SourceRecord, ...]:
    selected = document
    for field in records_path:
        if not isinstance(selected, dict) or field not in selected:
            raise ValueError("Snapshot record path is missing")
        selected = selected[field]
    if not records_path and isinstance(selected, dict):
        selected = [selected]
    if not isinstance(selected, list) or len(selected) > MAX_RECORDS:
        raise ValueError("Snapshot requires at most 1000 object records")
    if envelope_fields and (len(records_path) != 1 or not isinstance(document, dict)):
        raise ValueError("Envelope fields require a top-level snapshot record array")
    result = []
    seen = set()
    for index, raw in enumerate(selected):
        if not isinstance(raw, dict):
            raise ValueError("Acquired record must be a JSON object")
        identity = _identity(raw.get(identity_field)) if identity_field else f"position:{index}"
        if identity in seen:
            raise ValueError("Snapshot contains duplicate record identity")
        seen.add(identity)
        sequence = raw.get(sequence_field) if sequence_field else None
        if sequence_field and (isinstance(sequence, bool) or not isinstance(sequence, (int, str))):
            raise ValueError("Supplied producer sequence is invalid")
        if envelope_fields:
            if any(field not in document for field in envelope_fields):
                raise ValueError("Snapshot envelope field is missing")
            raw = {**{field: document[field] for field in envelope_fields}, records_path[0]: [raw]}
        result.append(SourceRecord(identity, deepcopy(raw), sequence))
    return tuple(result)


def _read_file(path: Path) -> bytes:
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        body = stream.read(MAX_BYTES + 1)
        current = path.stat()
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    # Windows stat and fstat can expose different ctime meanings. Compare mutation
    # on the same descriptor, and path identity while that descriptor is still open.
    if any(getattr(before, field) != getattr(after, field) for field in fields) or any(
        getattr(after, field) != getattr(current, field) for field in fields[:-1]
    ):
        raise ValueError("Complete-file snapshot changed during acquisition")
    if len(body) > MAX_BYTES:
        raise ValueError("Acquired file exceeds 1 MiB")
    return body


async def file_snapshot(
    path: Path,
    *,
    records_path: tuple[str, ...] = (),
    identity_field: str | None = None,
    sequence_field: str | None = None,
    envelope_fields: tuple[str, ...] = (),
) -> AcquiredPage:
    body = await asyncio.to_thread(_read_file, path)
    records = _records(
        decode_json_body(body),
        records_path=records_path,
        identity_field=identity_field,
        sequence_field=sequence_field,
        envelope_fields=envelope_fields,
    )
    return AcquiredPage(
        {
            "transport": "file_snapshot",
            "path": str(path.resolve()),
            "records_path": list(records_path),
            "identity_field": identity_field,
            "sequence_field": sequence_field,
            "envelope_fields": list(envelope_fields),
        },
        records,
        physical_count=len(records),
    )


async def file_journal(path: Path, *, final: bool = False) -> AcquiredPage:
    """Wait for delimiters while active; validate an unterminated final record only on explicit completion."""
    body = await asyncio.to_thread(_read_file, path)
    records = []
    complete_lines = body.split(b"\n")
    remainder = complete_lines.pop()
    incomplete = bool(remainder) and not final
    if final and remainder:
        complete_lines.append(remainder)
    if len(complete_lines) > MAX_RECORDS:
        raise ValueError("Acquired journal exceeds 1000 physical records")
    for index, line in enumerate(complete_lines):
        if not line.strip():
            continue
        raw = decode_json_body(line)
        if not isinstance(raw, dict):
            raise ValueError("Acquired journal record must be a JSON object")
        records.append(SourceRecord(f"physical:{index}", raw))
    return AcquiredPage(
        {"transport": "file_journal", "path": str(path.resolve())},
        tuple(records),
        incomplete_tail=incomplete,
        physical_count=len(complete_lines),
    )


def _sqlite_snapshot(
    path: Path,
    table: str,
    identity_column: str,
    sequence_column: str,
    filters: dict[str, str | int],
) -> tuple[SourceRecord, ...]:
    for name in (table, identity_column, sequence_column, *filters):
        if not _IDENTIFIER.fullmatch(name):
            raise ValueError("Invalid SQLite acquisition identifier")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_BYTES)
        deadline = time.monotonic() + 5
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10_000)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        where = " AND ".join(f'"{key}" = ?' for key in filters)
        query = f'SELECT * FROM "{table}"'
        if where:
            query += " WHERE " + where
        query += f' ORDER BY "{sequence_column}", "{identity_column}" LIMIT ?'
        rows = connection.execute(query, (*filters.values(), MAX_RECORDS + 1))
        result = []
        seen = set()
        total = 0

        for index, row in enumerate(rows):
            if index >= MAX_RECORDS:
                raise ValueError("SQLite snapshot exceeds 1000 records")
            raw = dict(row)
            identity = _identity(raw.get(identity_column))
            sequence = raw.get(sequence_column)
            if identity in seen or isinstance(sequence, bool) or not isinstance(sequence, int):
                raise ValueError("SQLite row identity is duplicate or sequence is not an integer")
            seen.add(identity)
            encoded = canonical_record(raw)
            total += len(encoded)
            if total > MAX_BYTES:
                raise ValueError("SQLite snapshot exceeds 1 MiB")
            decode_json_body(encoded)
            result.append(SourceRecord(identity, raw, sequence))
        return tuple(result)


async def sqlite_snapshot(
    path: Path,
    *,
    table: str,
    identity_column: str,
    sequence_column: str,
    filters: dict[str, str | int] | None = None,
) -> AcquiredPage:
    selected = dict(filters or {})
    records = await asyncio.to_thread(_sqlite_snapshot, path, table, identity_column, sequence_column, selected)
    return AcquiredPage(
        {
            "transport": "sqlite_snapshot",
            "path": str(path.resolve()),
            "table": table,
            "identity_column": identity_column,
            "sequence_column": sequence_column,
            "filters": selected,
        },
        records,
        physical_count=len(records),
    )


async def http_snapshot(
    client: httpx.AsyncClient,
    url: str,
    *,
    records_path: tuple[str, ...] = (),
    identity_field: str | None = None,
    sequence_field: str | None = None,
) -> AcquiredPage:
    async with client.stream(
        "GET", url, headers={"Accept-Encoding": "identity"}, follow_redirects=False, timeout=5
    ) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("Compressed acquisition responses are unsupported")
        body = bytearray()
        async for chunk in response.aiter_bytes(64 * 1024):
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("HTTP snapshot exceeds 1 MiB")
    records = _records(
        decode_json_body(bytes(body)),
        records_path=records_path,
        identity_field=identity_field,
        sequence_field=sequence_field,
        envelope_fields=(),
    )
    return AcquiredPage(
        {
            "transport": "http_snapshot",
            "url": url,
            "records_path": list(records_path),
            "identity_field": identity_field,
            "sequence_field": sequence_field,
        },
        records,
        physical_count=len(records),
    )


async def sse_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    last_event_id: str | None = None,
    limit: int = MAX_RECORDS,
) -> AcquiredPage:
    """Return a complete prefix at EOF, idle timeout, or a page boundary.

    Callers reconnect explicitly using only their durably committed event ID.
    """
    if not 1 <= limit <= MAX_RECORDS:
        raise ValueError("SSE record limit must be between 1 and 1000")
    headers = {"Accept": "text/event-stream", "Accept-Encoding": "identity"}
    if last_event_id is not None:
        if "\r" in last_event_id or "\n" in last_event_id:
            raise ValueError("Invalid SSE reconnect identity")
        headers["Last-Event-ID"] = last_event_id
    records: list[SourceRecord] = []
    buffer = bytearray()
    data: list[str] = []
    event_id = last_event_id
    total = 0
    stream_start = True
    skip_lf = False

    def consume_line(line: str) -> None:
        nonlocal event_id
        if not line:
            if data:
                if not event_id:
                    raise ValueError("SSE data requires a stable event id for durable reconnect")
                raw = decode_json_body("\n".join(data).encode("utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("SSE data must be a JSON object")
                records.append(SourceRecord(event_id, raw))
                data.clear()
            return
        if line.startswith(":"):
            return
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "id" and "\x00" not in value:
            event_id = value

    def page() -> AcquiredPage:
        return AcquiredPage(
            {"transport": "sse", "url": url},
            tuple(records),
            mode="stream",
            incomplete_tail=bool(buffer or data),
        )

    def consume_bytes(chunk: bytes) -> bool:
        nonlocal total, stream_start, skip_lf
        offset = 0
        while offset < len(chunk):
            available = MAX_BYTES - total
            if not available:
                if records:
                    return True
                raise ValueError("SSE record exceeds its 1 MiB acquisition bound")
            part = chunk[offset : offset + min(64 * 1024, available)]
            offset += len(part)
            total += len(part)
            buffer.extend(part)
            if stream_start:
                if len(buffer) < 3 and b"\xef\xbb\xbf".startswith(buffer):
                    continue
                if buffer.startswith(b"\xef\xbb\xbf"):
                    del buffer[:3]
                stream_start = False
            while buffer:
                if skip_lf:
                    if buffer[0] == 10:
                        del buffer[0]
                    skip_lf = False
                endings = [position for delimiter in (b"\r", b"\n") if (position := buffer.find(delimiter)) >= 0]
                if not endings:
                    break
                position = min(endings)
                delimiter = buffer[position]
                line = bytes(buffer[:position]).decode("utf-8", errors="strict")
                del buffer[: position + 1]
                skip_lf = delimiter == 13
                consume_line(line)
                if len(records) == limit:
                    return True
        return total == MAX_BYTES and bool(records)

    async with client.stream("GET", url, headers=headers, follow_redirects=False, timeout=5) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("Compressed acquisition responses are unsupported")
        if response.headers.get("content-type", "").split(";", 1)[0].strip() != "text/event-stream":
            raise ValueError("SSE source did not return text/event-stream")
        try:
            async for chunk in response.aiter_bytes():
                if consume_bytes(chunk):
                    return page()
        except httpx.ReadTimeout:
            if not records:
                raise
            return page()
    return page()


class QueueSource:
    """Keep dequeued data pending until the caller's durable receipt succeeds."""

    def __init__(self, queue: asyncio.Queue[SourceRecord], *, identity: str) -> None:
        if queue.maxsize <= 0 or not identity:
            raise ValueError("In-process acquisition requires a bounded queue and explicit source identity")
        self.queue = queue
        self.identity = identity
        self.delivery_lock = asyncio.Lock()
        self._pending: SourceRecord | None = None

    async def read(self) -> AcquiredPage:
        if self._pending is None:
            self._pending = deepcopy(await self.queue.get())
        return callback_page(self.identity, (self._pending,))

    def acknowledge(self) -> None:
        if self._pending is None:
            raise ValueError("No queue record is pending acknowledgment")
        self.queue.task_done()
        self._pending = None


def callback_page(identity: str, records: tuple[SourceRecord, ...]) -> AcquiredPage:
    if not identity:
        raise ValueError("Callback acquisition requires explicit source identity")
    return AcquiredPage({"transport": "in_process", "identity": identity}, deepcopy(records), mode="stream")
