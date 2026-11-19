from datetime import UTC, datetime
from unittest.mock import AsyncMock

from backend.models.events import EventKind, new_event
from backend.models.session_event import EventMetadata
from backend.services.artifacts.diff_service import DiffService
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.steps.tracker import StepTracker


async def test_concurrent_attempt_completion_never_flushes_or_commits_sibling_workspace(tmp_path) -> None:
    git = AsyncMock()
    git.rev_parse.return_value = "same-sha"
    git.is_merge_in_progress.return_value = False
    git.merge_base.return_value = "base-sha"
    git.diff.return_value = ""
    bus = EventBus()
    published = []

    async def receive(event):
        published.append(event)

    bus.subscribe(receive)
    tracker = StepTracker(bus, git)
    processor = EventProcessor(bus, DiffService(git, bus), tracker)
    first_path, second_path = str(tmp_path / "first"), str(tmp_path / "second")
    processor.register_attempt_worktree("job", "first", "epoch", first_path, managed=True, base_ref="main")
    processor.register_attempt_worktree("job", "second", "epoch", second_path, managed=False, base_ref="main")
    for attempt in ("first", "second"):
        event = new_event(
            "job",
            EventKind.message_user,
            {"content": f"Read {attempt}", "turn_id": "same-vendor-turn"},
            timestamp=datetime.now(UTC),
            metadata=EventMetadata(attempt_id=attempt, execution_epoch="epoch", recipe_run_id="run"),
        )
        await processor.process_event("job", event, "/wrong-primary-workspace", "wrong-base")
    second = tracker.current_step("job", attempt_id="second", execution_epoch="epoch")
    assert second is not None
    assert tracker.current_step("job") is None
    await processor.on_attempt_terminal("job", "first", "epoch", "completed")
    git.auto_commit.assert_awaited_once_with(cwd=first_path, message="codeplane: step 1")
    assert tracker.current_step("job", attempt_id="second", execution_epoch="epoch") is second
    assert all(event.metadata.attempt_id == "first" for event in published if event.kind == EventKind.step_completed)
    await processor.on_attempt_terminal("job", "second", "epoch", "completed")
    assert git.auto_commit.await_count == 1
    assert {call.kwargs["cwd"] for call in git.rev_parse.await_args_list} == {first_path, second_path}
    starts = [event for event in published if event.kind == EventKind.step_started]
    assert len({event.payload["step_id"] for event in starts}) == 2
    assert all(event.session_id == "job" and event.metadata.recipe_run_id == "run" for event in starts)


async def test_read_only_attempt_diff_never_changes_git_index(tmp_path) -> None:
    git = AsyncMock()
    git.is_merge_in_progress.return_value = False
    git.merge_base.return_value = "base"
    git.diff.return_value = ""
    bus = EventBus()
    processor = EventProcessor(bus, DiffService(git, bus))
    processor.register_attempt_worktree("job", "read", "epoch", str(tmp_path), managed=False, base_ref="main")
    await processor.process_event(
        "job",
        new_event(
            "job",
            EventKind.file_edited,
            {},
            metadata=EventMetadata(attempt_id="read", execution_epoch="epoch"),
        ),
    )
    git.add_intent_to_add.assert_not_awaited()
    git.diff.assert_awaited_once_with("base", cwd=str(tmp_path))
