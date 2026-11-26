"""Application-owned structural history rebuilding, without TF or model inference."""

import builtins
from datetime import UTC, datetime, timedelta

import pytest

from backend.models.events import EventKind, new_event
from backend.models.session_event import EventMetadata
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.services.events import reenrich
from backend.tests.unit.test_approval_service import session_factory as session_factory


@pytest.fixture(autouse=True)
def forbid_retired_runtime(monkeypatch):
    original = builtins.__import__

    def checked(name, *args, **kwargs):
        if name == "traceforge" or name.startswith("traceforge.") or name == "title_model":
            raise AssertionError("Final history rebuilding must not load a retired dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked)


async def test_rebuild_preserves_raw_history_old_metadata_and_legacy_marker(session_factory):
    start = new_event(
        "job-1",
        EventKind.tool_call_started,
        {"tool_call_id": "read-1", "tool_name": "read_file", "arguments": {"path": "file"}},
        timestamp=datetime.now(UTC),
        metadata=EventMetadata(execution_epoch="epoch"),
        raw_event={"native": "original start"},
    )
    finish = new_event(
        "job-1",
        EventKind.tool_call_completed,
        {"tool_call_id": "read-1", "success": True, "result": "contents"},
        timestamp=start.timestamp + timedelta(seconds=1),
        metadata=EventMetadata(execution_epoch="epoch"),
        raw_event={"native": "original completion"},
    )
    legacy_marker = new_event("job-1", EventKind.reenrich_complete, {"updated_count": 7})
    async with serialized_write(session_factory) as session:
        for event in (start, finish, legacy_marker):
            await EventRepository(session).append(event)
    assert await reenrich.reenrich_job_events("job-1", session_factory) == 1
    async with session_factory() as session:
        repo = EventRepository(session)
        retained = await repo.get(finish.id)
        assert retained.metadata.duration_ms == 1000
        assert retained.metadata.tool_display is not None
        assert retained.payload == finish.payload and retained.raw_event == finish.raw_event
        assert retained.id == finish.id and retained.timestamp == finish.timestamp
        assert await repo.get(legacy_marker.id) == legacy_marker
        assert await repo.get(start.id) == start
    assert await reenrich.reenrich_job_events("job-1", session_factory) == 0
    assert await reenrich.reenrich_job_events("job-1", session_factory, force=True) == 0
    async with session_factory() as session:
        markers = await EventRepository(session).list_by_job("job-1", [EventKind.reenrich_complete])
        assert len(markers) == 2 and any(event.id == legacy_marker.id for event in markers)


async def test_rebuild_never_pairs_identical_tool_ids_across_execution_owners(session_factory):
    start = new_event(
        "job-1", EventKind.tool_call_started, {"tool_call_id": "same"}, metadata=EventMetadata(execution_epoch="old")
    )
    finish = new_event(
        "job-1", EventKind.tool_call_completed, {"tool_call_id": "same"}, metadata=EventMetadata(execution_epoch="new")
    )
    async with serialized_write(session_factory) as session:
        for event in (start, finish):
            await EventRepository(session).append(event)
    assert await reenrich.reenrich_job_events("job-1", session_factory) == 0
    async with session_factory() as session:
        assert await EventRepository(session).get(finish.id) == finish


async def test_paginated_rebuild_keeps_existing_display_and_duration(session_factory, monkeypatch):
    monkeypatch.setattr(reenrich, "_BATCH_SIZE", 1)
    start = new_event("job-1", EventKind.tool_call_started, {"tool_call_id": "call", "tool_name": "read_file"})
    finish = new_event(
        "job-1",
        EventKind.tool_call_completed,
        {"tool_call_id": "call", "success": False},
        metadata=EventMetadata(duration_ms=42, tool_display="Retained display"),
    )
    async with serialized_write(session_factory) as session:
        for event in (start, finish):
            await EventRepository(session).append(event)
    assert await reenrich.reenrich_job_events("job-1", session_factory) == 0
    async with session_factory() as session:
        assert await EventRepository(session).get(finish.id) == finish


async def test_failed_rebuild_rolls_back_metadata_and_never_marks_completion(session_factory, monkeypatch):
    start = new_event("job-1", EventKind.tool_call_started, {"tool_call_id": "call", "tool_name": "read_file"})
    finish = new_event(
        "job-1",
        EventKind.tool_call_completed,
        {"tool_call_id": "call", "success": True},
        timestamp=start.timestamp + timedelta(seconds=1),
    )
    later = new_event(
        "job-1",
        EventKind.message_assistant,
        {"content": "later"},
        timestamp=start.timestamp + timedelta(seconds=2),
    )
    async with serialized_write(session_factory) as session:
        for event in (start, finish, later):
            await EventRepository(session).append(event)
    process = reenrich.ToolPairing.process

    def fail_after_pair(pairing, event):
        if event.id == later.id:
            raise ValueError("controlled projection failure")
        return process(pairing, event)

    with monkeypatch.context() as patched:
        patched.setattr(reenrich.ToolPairing, "process", fail_after_pair)
        with pytest.raises(ValueError, match="controlled projection failure"):
            await reenrich.reenrich_job_events("job-1", session_factory)
    async with session_factory() as session:
        repo = EventRepository(session)
        assert await repo.get(finish.id) == finish
        assert await repo.list_by_job("job-1", [EventKind.reenrich_complete]) == []
    assert await reenrich.reenrich_job_events("job-1", session_factory) == 1
