from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.api.jobs import archive_job, resolve_job
from backend.models.api_schemas import ResolveJobRequest
from backend.models.domain import Job, JobState, PermissionMode, Resolution


def _make_job(job_id: str, *, resolution: Resolution | None = Resolution.unresolved) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        repo="/tmp/repo",
        prompt="prompt",
        state=JobState.succeeded,
        base_ref="main",
        branch="feat/test",
        worktree_path="/tmp/repo/.wt",
        session_id=None,
        created_at=now,
        updated_at=now,
        resolution=resolution,
        permission_mode=PermissionMode.auto,
    )


@pytest.mark.asyncio
async def test_resolve_job_publishes_after_commit() -> None:
    committed = False
    session = SimpleNamespace()

    async def _commit() -> None:
        nonlocal committed
        committed = True

    session.commit = AsyncMock(side_effect=_commit)

    event = object()
    svc = SimpleNamespace(
        resolve_job=AsyncMock(return_value=_make_job("job-1")),
        execute_resolve=AsyncMock(return_value=("discarded", None, None, None)),
        build_job_resolved_event=Mock(return_value=event),
    )
    runtime_service = SimpleNamespace(resume_job=AsyncMock())
    merge_service = object()

    async def _publish(published_event: object) -> None:
        assert committed is True
        assert published_event is event

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
    svc.build_job_resolved_event.assert_called_once_with(
        "job-1",
        "discarded",
        pr_url=None,
        conflict_files=None,
        error=None,
    )
    event_bus.publish.assert_awaited_once()


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
