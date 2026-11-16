from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.api.job_artifacts import archive_job, resolve_job
from backend.models.api_schemas import ResolveJobRequest
from backend.models.domain import Job, JobState, Preset, Resolution


def _make_job(job_id: str, *, resolution: Resolution | None = Resolution.unresolved) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        project_id="proj-1",
        repo="/tmp/repo",
        prompt="prompt",
        state=JobState.review,
        base_ref="main",
        branch="feat/test",
        worktree_path="/tmp/repo/.wt",
        session_id=None,
        created_at=now,
        updated_at=now,
        resolution=resolution,
        preset=Preset.autonomous,
    )


@pytest.mark.asyncio
async def test_resolve_job_publishes_after_commit() -> None:
    committed = False
    session = SimpleNamespace()

    async def _commit() -> None:
        nonlocal committed
        committed = True

    session.commit = AsyncMock(side_effect=_commit)

    from backend.models.events import EventKind, new_event

    now = datetime.now(UTC)
    resolved_event = new_event(
        event_id="evt-1",
        session_id="job-1",
        timestamp=now,
        kind=EventKind.job_resolved,
        payload={"resolution": "discarded"},
    )
    completed_event = new_event(
        event_id="evt-2",
        session_id="job-1",
        timestamp=now,
        kind=EventKind.job_completed,
        payload={"resolution": "discarded", "merge_status": "discarded", "pr_url": None},
    )
    svc = SimpleNamespace(
        validate_for_resolution=AsyncMock(return_value=_make_job("job-1")),
        resolve_and_complete=AsyncMock(return_value=("discarded", None, None, None, [resolved_event, completed_event])),
    )
    runtime_service = SimpleNamespace(resume_job=AsyncMock())
    merge_service = object()

    published_events: list[object] = []

    async def _publish(published_event: object) -> None:
        assert committed is True
        published_events.append(published_event)

    event_bus = SimpleNamespace(publish=AsyncMock(side_effect=_publish))

    response = await resolve_job(
        job_id="job-1",
        body=ResolveJobRequest(action="discard"),
        svc=svc,
        session=session,
        runtime_service=runtime_service,
        merge_service=merge_service,
        event_bus=event_bus,
    )

    assert response.resolution == "discarded"
    session.commit.assert_awaited_once()
    # Two events: job_resolved + job_completed
    assert event_bus.publish.await_count == 2
    assert published_events[0] is resolved_event
    assert published_events[1].kind == EventKind.job_completed


@pytest.mark.asyncio
async def test_archive_job_publishes_after_commit() -> None:
    committed = False
    session = SimpleNamespace()

    async def _commit() -> None:
        nonlocal committed
        committed = True

    session.commit = AsyncMock(side_effect=_commit)

    event = object()
    svc = SimpleNamespace(
        archive_job=AsyncMock(return_value=_make_job("job-2")),
        build_job_archived_event=Mock(return_value=event),
    )

    async def _publish(published_event: object) -> None:
        assert committed is True
        assert published_event is event

    event_bus = SimpleNamespace(publish=AsyncMock(side_effect=_publish))

    await archive_job(
        job_id="job-2",
        svc=svc,
        session=session,
        event_bus=event_bus,
    )

    session.commit.assert_awaited_once()
    svc.archive_job.assert_awaited_once_with("job-2")
    svc.build_job_archived_event.assert_called_once_with("job-2")
    event_bus.publish.assert_awaited_once()
