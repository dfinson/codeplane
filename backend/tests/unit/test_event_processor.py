"""Tests for event_processor — the one TF-native funnel.

Events arrive already in TraceForge shape (dotted ``kind`` + TF payload
fields). The processor does NOT translate — it annotates (diff trigger,
turn_id synth/rotate, step/trail) and publishes. There is no ``_translate_event``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.events import EventKind, SessionEvent, new_event
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor


def _tf(kind: EventKind, payload: dict | None = None) -> SessionEvent:
    """Build a TF-native SessionEvent for job ``j1``."""
    return new_event(session_id="j1", kind=kind, payload=payload or {})


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def processor(event_bus: EventBus) -> EventProcessor:
    return EventProcessor(event_bus)


# ---------------------------------------------------------------------------
# process_event — publishing
# ---------------------------------------------------------------------------


class TestRuntimeContract:
    async def test_terminal_orphan_has_same_annotations_as_live_event(self):
        from traceforge.enricher import Enricher

        bus = EventBus()
        received = []
        bus.subscribe(AsyncMock(side_effect=received.append))
        processor = EventProcessor(bus, enricher=Enricher())
        event = _tf(EventKind.tool_call_started, {"tool_call_id": "pending", "tool_name": "view"})
        await processor.process_event("j1", event)
        await processor.on_job_terminal("j1", "review")
        assert len(received) == 1
        assert received[0].payload.get("turn_id")


class TestProcessEvent:
    @pytest.mark.asyncio
    async def test_log_event_published(self, processor: EventProcessor, event_bus: EventBus) -> None:
        received: list[SessionEvent] = []

        async def _handler(e: SessionEvent) -> None:
            if e.kind == EventKind.log_line_emitted:
                received.append(e)

        event_bus.subscribe(_handler)

        result = await processor.process_event("j1", _tf(EventKind.log_line_emitted, {"message": "test"}))
        assert result is not None
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_non_transcript_event_not_annotated(self, processor: EventProcessor) -> None:
        # A plain log event is published verbatim — no turn_id / step_number.
        result = await processor.process_event("j1", _tf(EventKind.log_line_emitted, {"message": "hi"}))
        assert result is not None
        assert "turn_id" not in result.payload
        assert "step_number" not in result.payload

    # -- diff triggering -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_file_edited_triggers_diff_and_consumes(self) -> None:
        bus = EventBus()
        diff_svc = AsyncMock()
        proc = EventProcessor(bus, diff_service=diff_svc)

        result = await proc.process_event(
            "j1", _tf(EventKind.file_edited, {"path": "a.py"}), worktree_path="/w", base_ref="main"
        )
        # file.edited is consumed (diff surfaces via a synthesized diff.updated).
        assert result is None
        diff_svc.on_worktree_file_modified.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_completed_triggers_diff(self) -> None:
        bus = EventBus()
        diff_svc = AsyncMock()
        proc = EventProcessor(bus, diff_service=diff_svc)

        result = await proc.process_event(
            "j1",
            _tf(EventKind.tool_call_completed, {"tool_name": "write_file", "arguments": "{}", "success": True}),
            worktree_path="/w",
            base_ref="main",
        )
        assert result is not None
        diff_svc.on_worktree_file_modified.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_intent_skips_diff(self) -> None:
        bus = EventBus()
        diff_svc = AsyncMock()
        proc = EventProcessor(bus, diff_service=diff_svc)

        result = await proc.process_event(
            "j1",
            _tf(EventKind.tool_call_completed, {"tool_name": "report_intent"}),
            worktree_path="/w",
            base_ref="main",
        )
        assert result is not None
        diff_svc.on_worktree_file_modified.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_diff_without_worktree(self) -> None:
        bus = EventBus()
        diff_svc = AsyncMock()
        proc = EventProcessor(bus, diff_service=diff_svc)

        # Missing worktree_path/base_ref => not diff-eligible.
        result = await proc.process_event("j1", _tf(EventKind.tool_call_completed, {"tool_name": "write_file"}))
        assert result is not None
        diff_svc.on_worktree_file_modified.assert_not_awaited()

    # -- turn_id synthesis / rotation ---------------------------------------

    @pytest.mark.asyncio
    async def test_transcript_gets_synthesized_turn_id(self, processor: EventProcessor) -> None:
        result = await processor.process_event(
            "j1", _tf(EventKind.tool_call_started, {"tool_name": "grep", "content": "running"})
        )
        assert result is not None
        assert result.payload.get("turn_id") is not None

    @pytest.mark.asyncio
    async def test_supplied_turn_id_preserved(self, processor: EventProcessor) -> None:
        result = await processor.process_event(
            "j1", _tf(EventKind.tool_call_started, {"tool_name": "grep", "turn_id": "supplied-turn"})
        )
        assert result is not None
        assert result.payload["turn_id"] == "supplied-turn"

    @pytest.mark.asyncio
    async def test_turn_id_stable_within_turn(self, processor: EventProcessor) -> None:
        r1 = await processor.process_event("j1", _tf(EventKind.tool_call_started, {"tool_name": "a"}))
        r2 = await processor.process_event("j1", _tf(EventKind.tool_call_completed, {"tool_name": "a"}))
        assert r1 is not None and r2 is not None
        assert r1.payload["turn_id"] == r2.payload["turn_id"]

    @pytest.mark.asyncio
    async def test_turn_id_rotates_after_agent_message(self, processor: EventProcessor) -> None:
        r1 = await processor.process_event("j1", _tf(EventKind.tool_call_started, {"tool_name": "a"}))
        tid1 = r1.payload["turn_id"]

        # A completed assistant message closes the turn.
        await processor.process_event("j1", _tf(EventKind.message_assistant, {"content": "done"}))

        r3 = await processor.process_event("j1", _tf(EventKind.tool_call_started, {"tool_name": "b"}))
        assert r3.payload["turn_id"] != tid1

    # -- step / trail annotation --------------------------------------------

    @pytest.mark.asyncio
    async def test_step_annotation_on_transcript(self) -> None:
        bus = EventBus()
        tracker = AsyncMock()
        tracker.current_step = MagicMock(return_value=MagicMock(step_number=7))
        trail = MagicMock()
        trail.get_active_plan_step_id = MagicMock(return_value="ps-abc")
        proc = EventProcessor(bus, step_tracker=tracker, trail_service=trail)

        result = await proc.process_event("j1", _tf(EventKind.tool_call_completed, {"tool_name": "grep"}))
        assert result is not None
        tracker.on_transcript_event.assert_awaited_once()
        assert result.payload["step_number"] == 7
        assert result.payload["step_id"] == "ps-abc"

    @pytest.mark.asyncio
    async def test_streaming_kind_skips_step_tracking(self) -> None:
        bus = EventBus()
        tracker = AsyncMock()
        proc = EventProcessor(bus, step_tracker=tracker)

        # message.delta is a streaming partial — display, but do not advance steps.
        result = await proc.process_event("j1", _tf(EventKind.message_delta, {"content": "par"}))
        assert result is not None
        tracker.on_transcript_event.assert_not_awaited()


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_clears_turn_ids(self, processor: EventProcessor) -> None:
        processor._turn_ids["j1"] = "some-id"
        processor.cleanup("j1")
        assert "j1" not in processor._turn_ids

    def test_cleanup_with_step_tracker(self) -> None:
        bus = EventBus()
        tracker = MagicMock()
        proc = EventProcessor(bus, step_tracker=tracker)
        proc.cleanup("j1")
        tracker.cleanup.assert_called_once_with("j1")

    def test_cleanup_with_diff_service(self) -> None:
        bus = EventBus()
        diff_svc = MagicMock()
        proc = EventProcessor(bus, diff_service=diff_svc)
        proc.cleanup("j1")
        diff_svc.cleanup.assert_called_once_with("j1")


# ---------------------------------------------------------------------------
# on_job_terminal
# ---------------------------------------------------------------------------


class TestOnJobTerminal:
    @pytest.mark.asyncio
    async def test_notifies_step_tracker(self) -> None:
        bus = EventBus()
        tracker = AsyncMock()
        proc = EventProcessor(bus, step_tracker=tracker)
        await proc.on_job_terminal("j1", "completed")
        tracker.on_job_terminal.assert_awaited_once_with("j1", "completed")

    @pytest.mark.asyncio
    async def test_no_tracker_no_error(self) -> None:
        bus = EventBus()
        proc = EventProcessor(bus)
        # Should not raise
        await proc.on_job_terminal("j1", "completed")


# ---------------------------------------------------------------------------
# register_worktree
# ---------------------------------------------------------------------------


class TestRegisterWorktree:
    def test_with_step_tracker(self) -> None:
        bus = EventBus()
        tracker = MagicMock()
        proc = EventProcessor(bus, step_tracker=tracker)
        proc.register_worktree("j1", "/tmp/wt")
        tracker.register_worktree.assert_called_once_with("j1", "/tmp/wt")

    def test_without_step_tracker(self) -> None:
        bus = EventBus()
        proc = EventProcessor(bus)
        # Should not raise
        proc.register_worktree("j1", "/tmp/wt")
