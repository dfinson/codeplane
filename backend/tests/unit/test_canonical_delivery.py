import asyncio
import json

from backend.models.domain import SessionConfig
from backend.models.events import EventKind
from backend.models.session_event import CanonicalSessionEvent
from backend.persistence.event_repo import EventRepository
from backend.services.copilot_adapter import CopilotAdapter
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.events.sse_manager import SSEConnection, SSEManager
from backend.tests.unit.test_approval_service import session_factory as session_factory


async def test_managed_recipe_event_keeps_identity_through_processor_sqlite_and_sse(session_factory) -> None:
    adapter = CopilotAdapter()
    adapter._queues["sdk"] = asyncio.Queue()
    adapter.set_session_metadata(
        "sdk",
        SessionConfig(
            workspace_path="/test",
            prompt="",
            job_id="job-1",
            recipe_run_id="run-1",
            recipe_revision=3,
            step_instance_id="recipe-step",
            attempt_id="attempt-1",
            execution_epoch="epoch-1",
        ),
    )
    adapter._emit_tf(
        "sdk",
        "job-1",
        EventKind.message_assistant,
        {"content": "Completed selected step", "turn_id": "transcript-turn", "step_id": "transcript-step"},
    )
    emitted = adapter._queues["sdk"].get_nowait()
    assert isinstance(emitted, CanonicalSessionEvent)
    bus = EventBus()
    manager = SSEManager()
    connection = SSEConnection(job_id="job-1")
    manager.register(connection)

    async def persist_and_broadcast(event):
        async with session_factory() as session:
            cursor = await EventRepository(session).append(event)
            await session.commit()
        await manager.broadcast_domain_event(event, storage_cursor=cursor)

    bus.subscribe(persist_and_broadcast, required=True)
    processor = EventProcessor(bus)
    processor.register_attempt_worktree("job-1", "attempt-1", "epoch-1", "/test")
    await processor.process_event("job-1", emitted)
    async with session_factory() as session:
        stored = (await EventRepository(session).list_after(0, job_id="job-1"))[0]
    assert isinstance(stored.event, CanonicalSessionEvent)
    assert stored.event.id == emitted.id
    assert stored.event.metadata.recipe_run_id == "run-1"
    assert stored.event.metadata.attempt_id == "attempt-1"
    assert stored.event.payload["turn_id"] == "transcript-turn"
    assert stored.event.payload["step_id"] == "transcript-step"
    frame = connection.queue.get_nowait()
    assert f"id: {stored.storage_cursor}\n" in frame
    body = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
    assert body["id"] == emitted.id
    assert body["metadata"]["attempt_id"] == "attempt-1"
    assert body["metadata"]["recipe_revision"] == 3
    assert body["metadata"]["sequence"] is None
