"""Tests for native plan capture from manage_todo_list / TodoWrite tool calls."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.events import DomainEvent, DomainEventKind
from backend.services.progress_tracking_service import ProgressTrackingService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def event_bus() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def utility_session() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def service(utility_session: MagicMock, event_bus: AsyncMock) -> ProgressTrackingService:
    return ProgressTrackingService(utility_session=utility_session, event_bus=event_bus)


# ---------------------------------------------------------------------------
# feed_native_plan
# ---------------------------------------------------------------------------


class TestFeedNativePlan:
    """Tests for ProgressTrackingService.feed_native_plan."""

    @pytest.mark.asyncio()
    async def test_copilot_manage_todo_list(self, service: ProgressTrackingService, event_bus: AsyncMock) -> None:
        """Copilot-style todoList items are correctly mapped to plan steps."""
        service.start_tracking("job-1")
        items = [
            {"id": 1, "title": "Explore codebase", "status": "completed"},
            {"id": 2, "title": "Implement feature", "status": "in-progress"},
            {"id": 3, "title": "Write tests", "status": "not-started"},
        ]
        await service.feed_native_plan("job-1", items)

        event_bus.publish.assert_called_once()
        event: DomainEvent = event_bus.publish.call_args[0][0]
        assert event.kind == DomainEventKind.agent_plan_updated
        assert event.job_id == "job-1"
        steps = event.payload["steps"]
        assert steps == [
            {"label": "Explore codebase", "status": "done"},
            {"label": "Implement feature", "status": "active"},
            {"label": "Write tests", "status": "pending"},
        ]

    @pytest.mark.asyncio()
    async def test_claude_todo_write(self, service: ProgressTrackingService, event_bus: AsyncMock) -> None:
        """Claude-style todos with 'content' field are correctly mapped."""
        service.start_tracking("job-1")
        items = [
            {"id": "1", "content": "Read source files", "status": "completed"},
            {"id": "2", "content": "Fix the bug", "status": "in_progress"},
            {"id": "3", "content": "Run tests", "status": "pending"},
        ]
        await service.feed_native_plan("job-1", items)

        event_bus.publish.assert_called_once()
        steps = event_bus.publish.call_args[0][0].payload["steps"]
        assert steps == [
            {"label": "Read source files", "status": "done"},
            {"label": "Fix the bug", "status": "active"},
            {"label": "Run tests", "status": "pending"},
        ]

    @pytest.mark.asyncio()
    async def test_duplicate_plan_not_republished(self, service: ProgressTrackingService, event_bus: AsyncMock) -> None:
        """Identical plan data is not re-published."""
        service.start_tracking("job-1")
        items = [
            {"id": 1, "title": "Task A", "status": "in-progress"},
            {"id": 2, "title": "Task B", "status": "not-started"},
        ]
        await service.feed_native_plan("job-1", items)
        assert event_bus.publish.call_count == 1

        # Feed the same items again
        await service.feed_native_plan("job-1", items)
        assert event_bus.publish.call_count == 1  # no new publish

    @pytest.mark.asyncio()
    async def test_updated_plan_republished(self, service: ProgressTrackingService, event_bus: AsyncMock) -> None:
        """When plan steps change, a new event is published."""
        service.start_tracking("job-1")
        items_v1 = [
            {"id": 1, "title": "Task A", "status": "in-progress"},
            {"id": 2, "title": "Task B", "status": "not-started"},
        ]
        await service.feed_native_plan("job-1", items_v1)
        assert event_bus.publish.call_count == 1

        items_v2 = [
            {"id": 1, "title": "Task A", "status": "completed"},
            {"id": 2, "title": "Task B", "status": "in-progress"},
        ]
        await service.feed_native_plan("job-1", items_v2)
        assert event_bus.publish.call_count == 2

    @pytest.mark.asyncio()
    async def test_empty_items_ignored(self, service: ProgressTrackingService, event_bus: AsyncMock) -> None:
        """Empty items list does not publish an event."""
        service.start_tracking("job-1")
        await service.feed_native_plan("job-1", [])
        event_bus.publish.assert_not_called()

    @pytest.mark.asyncio()
    async def test_items_without_labels_skipped(self, service: ProgressTrackingService, event_bus: AsyncMock) -> None:
        """Items missing both title and content are filtered out."""
        service.start_tracking("job-1")
        items = [
            {"id": 1, "status": "in-progress"},  # no title/content
            {"id": 2, "title": "Valid task", "status": "not-started"},
        ]
        await service.feed_native_plan("job-1", items)

        steps = event_bus.publish.call_args[0][0].payload["steps"]
        assert len(steps) == 1
        assert steps[0]["label"] == "Valid task"

    @pytest.mark.asyncio()
    async def test_unknown_status_maps_to_pending(self, service: ProgressTrackingService, event_bus: AsyncMock) -> None:
        """Unknown status values default to 'pending'."""
        service.start_tracking("job-1")
        items = [{"id": 1, "title": "Some task", "status": "weird_status"}]
        await service.feed_native_plan("job-1", items)

        steps = event_bus.publish.call_args[0][0].payload["steps"]
        assert steps[0]["status"] == "pending"

    @pytest.mark.asyncio()
    async def test_native_plan_suppresses_llm_extraction(self, service: ProgressTrackingService) -> None:
        """Once native plan is fed, the job is flagged to suppress LLM extraction."""
        service.start_tracking("job-1")
        items = [{"id": 1, "title": "Task", "status": "in-progress"}]
        await service.feed_native_plan("job-1", items)
        assert "job-1" in service._native_plan_active

    @pytest.mark.asyncio()
    async def test_cleanup_clears_native_flag(self, service: ProgressTrackingService) -> None:
        """Cleanup removes native plan flag."""
        service.start_tracking("job-1")
        items = [{"id": 1, "title": "Task", "status": "in-progress"}]
        await service.feed_native_plan("job-1", items)
        assert "job-1" in service._native_plan_active

        service.cleanup("job-1")
        assert "job-1" not in service._native_plan_active


# ---------------------------------------------------------------------------
# RuntimeService._ingest_native_plan
# ---------------------------------------------------------------------------


class TestIngestNativePlan:
    """Tests for RuntimeService._ingest_native_plan parsing logic."""

    @pytest.mark.asyncio()
    async def test_copilot_payload(self, service: ProgressTrackingService, event_bus: AsyncMock) -> None:
        """Copilot-style tool_args with todoList are parsed correctly."""
        service.start_tracking("job-1")

        # Simulate what RuntimeService._ingest_native_plan does
        payload = {
            "tool_name": "manage_todo_list",
            "tool_args": json.dumps(
                {
                    "todoList": [
                        {"id": 1, "title": "Setup project", "status": "completed"},
                        {"id": 2, "title": "Write code", "status": "in-progress"},
                    ]
                }
            ),
        }
        args = json.loads(payload["tool_args"])
        items = args.get("todoList") or args.get("todos") or []
        await service.feed_native_plan("job-1", items)

        steps = event_bus.publish.call_args[0][0].payload["steps"]
        assert steps[0] == {"label": "Setup project", "status": "done"}
        assert steps[1] == {"label": "Write code", "status": "active"}

    @pytest.mark.asyncio()
    async def test_claude_payload(self, service: ProgressTrackingService, event_bus: AsyncMock) -> None:
        """Claude-style tool_args with todos are parsed correctly."""
        service.start_tracking("job-1")

        payload = {
            "tool_name": "TodoWrite",
            "tool_args": json.dumps(
                {
                    "todos": [
                        {"id": "1", "content": "Investigate issue", "status": "completed"},
                        {"id": "2", "content": "Apply fix", "status": "in_progress"},
                    ]
                }
            ),
        }
        args = json.loads(payload["tool_args"])
        items = args.get("todoList") or args.get("todos") or []
        await service.feed_native_plan("job-1", items)

        steps = event_bus.publish.call_args[0][0].payload["steps"]
        assert steps[0] == {"label": "Investigate issue", "status": "done"}
        assert steps[1] == {"label": "Apply fix", "status": "active"}
