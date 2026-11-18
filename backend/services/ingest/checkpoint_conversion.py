"""Explicit, quiescent v1/v2 conversion; never rewrites or re-emits source history.

Rollback restores only an unchanged conversion boundary, not a provider or its
new outcomes. Once either reader advances, retained evidence remains readable
but a new transition requires reconciliation rather than offset reset.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from backend.models.domain import StateConflictError
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.services.events.tool_pairing import ToolPairing
from backend.services.ingest._base import (
    _advance_prefix,
    _canonical_record,
    _LegacyNativeIdentity,
    _NativeIdentity,
    _verify_source_prefix,
)
from backend.services.ingest.jsonl_source import NativeJsonlSource
from backend.services.ingest.otel_decoder import decode_json_body
from backend.services.ingest.source_ownership import hold_native_source

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.session_event import SessionEvent

_CONTRACT = "codeplane-native-conversion-v1"


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise StateConflictError(f"Native checkpoint conversion requires reconciliation: {reason}")


def _canonical_event(event: SessionEvent) -> dict[str, Any]:
    # Physical source bytes bind additive raw retention independently.
    return event.model_dump(mode="json", exclude={"raw_event"})


def _compare_event(old: SessionEvent, new: SessionEvent) -> bytes:
    before, after = _canonical_event(old), _canonical_event(new)
    _require(before == after, "canonical output differs (identity, time, payload or metadata)")
    _require(old.raw_event is None or old.raw_event == new.raw_event, "retained original raw event differs")
    return _canonical_record(before)


async def _history_digest(session: AsyncSession, job_id: str, framework: str, external_id: str, accepted: int) -> str:
    digest = bytes(32)
    cursor = 0
    while True:
        rows = await EventRepository(session).list_after(cursor, job_id=job_id, limit=64)
        if not rows:
            return digest.hex()
        for row in rows:
            source = row.event.payload.get("_codeplane_source")
            if (
                isinstance(source, dict)
                and source.get("framework") == framework
                and source.get("session_id") == external_id
            ):
                ordinal = source.get("record_index")
                _require(
                    type(ordinal) is int and 0 <= ordinal < accepted,
                    "history contains output outside the accepted physical checkpoint",
                )
            digest = _advance_prefix(
                digest, _canonical_record({"cursor": row.storage_cursor, "event": row.event.model_dump(mode="json")})
            )
        cursor = rows[-1].storage_cursor


def _conversion(checkpoint: dict[str, Any]) -> dict[str, Any] | None:
    record = checkpoint.get("conversion")
    if record is None:
        return None
    _require(isinstance(record, dict), "malformed conversion evidence")
    assert isinstance(record, dict)
    evidence = record.get("evidence")
    _require(isinstance(evidence, dict) and evidence.get("contract") == _CONTRACT, "unknown conversion evidence")
    assert isinstance(evidence, dict)
    _require(
        record.get("sha256") == hashlib.sha256(_canonical_record(evidence)).hexdigest(),
        "conversion evidence digest changed",
    )
    _require(record.get("status") in ("converted", "rolled_back"), "unknown conversion status")
    _require(
        isinstance(evidence.get("original_checkpoint"), dict)
        and "conversion" not in evidence["original_checkpoint"]
        and isinstance(evidence.get("converted_checkpoint"), dict)
        and "conversion" not in evidence["converted_checkpoint"],
        "conversion evidence is incomplete or nested",
    )
    return record


async def convert_native_checkpoint(
    factory: async_sessionmaker[AsyncSession], *, job_id: str, source_path: Path, rollback: bool = False
) -> dict[str, Any]:
    """Compare actual retained parsers and atomically promote one accepted prefix."""
    try:
        with hold_native_source(factory, job_id):
            return await _convert(factory, job_id, source_path, rollback)
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        raise StateConflictError(f"Native checkpoint conversion requires reconciliation: {exc}") from exc


async def _convert(
    factory: async_sessionmaker[AsyncSession], job_id: str, source_path: Path, rollback: bool
) -> dict[str, Any]:
    async with factory() as session:
        saved = await JobRepository(session).get_ingest_state(job_id)
        checkpoint = saved.checkpoint
        job = saved.job
        framework = {"copilot_cli": "copilot", "claude_cli": "claude"}.get(job.source)
        _require(framework is not None and bool(job.external_session_id), "Job does not own a native CLI source")
        _require(isinstance(checkpoint, dict), "no provable native checkpoint")
        assert framework is not None and job.external_session_id is not None and checkpoint is not None
        current_count = checkpoint.get("record_count")
        _require(type(current_count) is int and current_count >= 0, "invalid physical record count")
        assert isinstance(current_count, int)
        history_digest = await _history_digest(session, job_id, framework, job.external_session_id, current_count)
        retained = _conversion(checkpoint)
        original = retained["evidence"]["original_checkpoint"] if retained else checkpoint
        offset = retained["evidence"]["original_tail_offset"] if retained else job.tail_offset
        emitted = await EventRepository(session).count_native_events(job_id, framework, job.external_session_id)
    base = {key: value for key, value in checkpoint.items() if key != "conversion"}
    if retained is not None:
        evidence = retained["evidence"]
        _require(
            evidence["converted_checkpoint"]["source"]["path"] == str(source_path.resolve()),
            "conversion source path changed",
        )
        if rollback and retained["status"] == "rolled_back" and checkpoint.get("version") == 1:
            return _result("already_rolled_back", job_id, retained)
        if not rollback and retained["status"] == "converted" and checkpoint.get("version") == 2:
            return _result("already_converted", job_id, retained)
        expected = evidence["converted_checkpoint"] if rollback else original
        _require(
            base == expected and job.tail_offset == offset and history_digest == evidence["history_sha256"],
            "progress or history changed after the retained conversion boundary",
        )
    else:
        _require(not rollback, "no retained conversion to roll back")
    _require(original.get("version") == 1, "only an explicit version-1 boundary may be converted")
    count = original.get("record_count")
    _require(type(count) is int and count >= 0, "invalid physical record count")
    assert isinstance(count, int)
    _require(original.get("external_session_id") == job.external_session_id, "checkpoint source ownership differs")
    old = _LegacyNativeIdentity(framework, job.external_session_id, job_id, job.created_at)
    new = _NativeIdentity(framework, job.external_session_id, job_id, job.created_at)
    _require(original.get("mapping_sha256") == old.mapping_digest, "retained legacy mapping digest changed")
    for field, value in (
        ("framework", framework),
        ("timestamp_anchor", old.anchor.isoformat()),
        ("output_contract", "codeplane-native-v1"),
    ):
        _require(field not in original or original[field] == value, f"retained {field} changed")
    _verify_source_prefix(source_path, count, original["prefix_sha256"])
    old_pairs, new_pairs = ToolPairing(), ToolPairing()
    canonical_digest, prefix = bytes(32), bytes(32)
    decoded = 0
    previous_source = retained["evidence"]["converted_checkpoint"]["source"] if retained else None
    async with NativeJsonlSource(source_path, checkpoint=previous_source) as source:
        for ordinal in range(count):
            line = await source.read_available()
            _require(line is not None, "accepted physical prefix is truncated or incomplete")
            assert line is not None
            raw = decode_json_body(line) if line.strip() else None
            _require(not line.strip() or isinstance(raw, dict), "accepted record is not a JSON object")
            if isinstance(raw, dict):
                old_events = tuple(old.events(deepcopy(raw), ordinal))
                new_events = tuple(new.events(deepcopy(raw), ordinal))
                _require(len(old_events) == len(new_events), "compound record output count differs")
                for before, after in zip(old_events, new_events, strict=True):
                    canonical_digest = _advance_prefix(canonical_digest, _compare_event(before, after))
                    left, right = old_pairs.process(before), new_pairs.process(after)
                    left_events = left if isinstance(left, list) else [left] if left is not None else []
                    right_events = right if isinstance(right, list) else [right] if right is not None else []
                    _require(len(left_events) == len(right_events), "paired canonical output count differs")
                    for left_event, right_event in zip(left_events, right_events, strict=True):
                        _compare_event(left_event, right_event)
                    decoded += 1
            prefix = _advance_prefix(prefix, _canonical_record(raw) if isinstance(raw, dict) else b"")
        _require(prefix.hex() == original["prefix_sha256"], "accepted physical prefix changed")
        _require(decoded == offset, "decoded cursor ends inside a compound record or differs from accepted prefix")
        _require(old.checkpoint() == new.decoder.checkpoint(), "buffered motivation or turn state differs")
        continuation = _advance_prefix(bytes(32), _canonical_record(asdict(old.checkpoint())))
        old_starts, new_starts = old_pairs.checkpoint().starts, new_pairs.checkpoint().starts
        _require(len(old_starts) == len(new_starts), "buffered tool-start count differs")
        for left_start, right_start in zip(old_starts, new_starts, strict=True):
            continuation = _advance_prefix(continuation, _compare_event(left_start, right_start))
        converted = {
            "version": 2,
            "framework": framework,
            "external_session_id": job.external_session_id,
            "record_count": count,
            "prefix_sha256": prefix.hex(),
            "mapping_sha256": new.mapping_digest,
            "timestamp_anchor": new.anchor.isoformat(),
            "output_contract": "codeplane-native-v1",
            "source": source.checkpoint(),
            "decoded_event_count": decoded,
            "emitted_event_count": emitted,
        }
        evidence = {
            "contract": _CONTRACT,
            "original_checkpoint": original,
            "original_tail_offset": offset,
            "converted_checkpoint": converted,
            "canonical_sha256": canonical_digest.hex(),
            "continuation_sha256": continuation.hex(),
            "pending_starts": len(old_starts),
            "history_sha256": history_digest,
        }
        if retained is not None:
            _require(evidence == retained["evidence"], "retained conversion proof no longer matches either reader")
        status = "rolled_back" if rollback else "converted"
        record = {
            "status": status,
            "evidence": evidence,
            "sha256": hashlib.sha256(_canonical_record(evidence)).hexdigest(),
        }
        target = {**(original if rollback else converted), "conversion": record}
        async with serialized_write(factory) as session:
            await JobRepository(session).compare_and_set_ingest_checkpoint(saved, target, decoded)
            _require(
                _LegacyNativeIdentity(framework, job.external_session_id, job_id, job.created_at).mapping_digest
                == old.mapping_digest
                and _NativeIdentity(framework, job.external_session_id, job_id, job.created_at).mapping_digest
                == new.mapping_digest,
                "mapping implementation changed during conversion",
            )
            _require(
                await _history_digest(session, job_id, framework, job.external_session_id, count) == history_digest,
                "history changed during conversion",
            )
            await source.verify_reconstruction()
        return _result(status, job_id, record)


def _result(status: str, job_id: str, record: dict[str, Any]) -> dict[str, Any]:
    evidence = record["evidence"]
    return {
        "status": status,
        "job_id": job_id,
        "conversion_sha256": record["sha256"],
        "record_count": evidence["converted_checkpoint"]["record_count"],
        "decoded_event_count": evidence["original_tail_offset"],
    }
