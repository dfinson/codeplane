"""Tests for terminal artifact collection orchestration."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.models.api_schemas import ArtifactType, ExecutionPhase
from backend.models.db import Base
from backend.models.domain import Artifact, Job, JobState
from backend.models.events import EventKind
from backend.persistence.artifact_repo import ArtifactRepository
from backend.persistence.database import _set_sqlite_pragmas, serialized_write
from backend.persistence.job_repo import JobRepository
from backend.services.artifacts.finalization_service import ArtifactFinalizationService
from backend.services.events.event_bus import EventBus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _create_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: str = "job-1",
    status: str = "pending",
) -> None:
    now = datetime.now(UTC)
    job = Job(
        id=job_id,
        project_id="proj-1",
        repo="C:/repo",
        prompt="Audit the repository",
        state=JobState.review,
        base_ref="main",
        branch="audit",
        worktree_path="C:/worktree",
        session_id=None,
        created_at=now,
        updated_at=now,
        completed_at=now,
        artifact_collection_status=status,
    )
    async with serialized_write(session_factory) as session:
        from backend.persistence.project_repo import ProjectRepository

        existing = await ProjectRepository(session).get("proj-1")
        if existing is None:
            await ProjectRepository(session).create("proj-1", "Test Project", ["C:/repo"])
        await JobRepository(session).create(job)


def _make_service(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    *,
    snapshot_error: Exception | None = None,
    create_artifact: bool = True,
) -> tuple[ArtifactFinalizationService, AsyncMock, AsyncMock, list[object]]:
    summarization = AsyncMock()
    telemetry = AsyncMock()
    published: list[object] = []
    event_bus = EventBus()

    async def collect_event(event: object) -> None:
        published.append(event)

    event_bus.subscribe(collect_event)

    async def save_snapshot(job_id: str) -> None:
        if snapshot_error is not None:
            raise snapshot_error
        if not create_artifact:
            return
        disk_path = tmp_path / f"{job_id}-session-log.json"
        disk_path.write_text('{"sessions": []}', encoding="utf-8")
        async with serialized_write(session_factory) as session:
            existing = await ArtifactRepository(session).list_for_job(job_id)
            if existing:
                return
            await ArtifactRepository(session).create(
                Artifact(
                    id=f"art-{job_id}",
                    job_id=job_id,
                    name="session-log.json",
                    type=ArtifactType.session_log,
                    mime_type="application/json",
                    size_bytes=disk_path.stat().st_size,
                    disk_path=str(disk_path),
                    phase=ExecutionPhase.post_completion,
                    created_at=datetime.now(UTC),
                )
            )

    summarization.save_snapshot_to_disk.side_effect = save_snapshot
    return (
        ArtifactFinalizationService(session_factory, event_bus, summarization, telemetry),
        summarization,
        telemetry,
        published,
    )


@pytest.mark.asyncio
async def test_review_finalization_creates_artifacts_once(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _create_job(session_factory)
    service, summarization, telemetry, published = _make_service(session_factory, tmp_path)

    assert await service.finalize("job-1") == "completed"
    assert await service.finalize("job-1") == "completed"

    summarization.save_snapshot_to_disk.assert_awaited_once_with("job-1")
    telemetry.store_post_completion_artifacts.assert_awaited_once_with("job-1")
    async with session_factory() as session:
        job = await JobRepository(session).get("job-1")
        artifacts = await ArtifactRepository(session).list_for_job("job-1")
    assert job is not None
    assert job.artifact_collection_status == "completed"
    assert job.artifact_collection_error is None
    assert len(artifacts) == 1
    assert [event.kind for event in published] == [EventKind.artifacts_updated]


@pytest.mark.asyncio
async def test_concurrent_finalization_runs_collection_once(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _create_job(session_factory)
    service, summarization, telemetry, published = _make_service(session_factory, tmp_path)

    statuses = await asyncio.gather(
        service.finalize("job-1"),
        service.finalize("job-1"),
        service.finalize("job-1"),
    )

    assert statuses == ["completed", "completed", "completed"]
    summarization.save_snapshot_to_disk.assert_awaited_once_with("job-1")
    telemetry.store_post_completion_artifacts.assert_awaited_once_with("job-1")
    assert [event.kind for event in published] == [EventKind.artifacts_updated]


@pytest.mark.asyncio
async def test_collection_failure_is_durable_and_published(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _create_job(session_factory)
    service, _summarization, telemetry, published = _make_service(
        session_factory,
        tmp_path,
        snapshot_error=OSError("workspace disappeared"),
    )

    assert await service.finalize("job-1") == "failed"

    telemetry.store_post_completion_artifacts.assert_not_awaited()
    async with session_factory() as session:
        job = await JobRepository(session).get("job-1")
    assert job is not None
    assert job.artifact_collection_status == "failed"
    assert job.artifact_collection_error == "OSError: workspace disappeared"
    assert published[-1].payload["collection_status"] == "failed"


@pytest.mark.asyncio
async def test_recovery_backfills_terminal_zero_artifact_jobs(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _create_job(session_factory, job_id="pending-job")
    await _create_job(session_factory, job_id="stale-job", status="collecting")
    service, summarization, telemetry, _published = _make_service(session_factory, tmp_path)

    assert await service.recover_eligible() == 2

    assert summarization.save_snapshot_to_disk.await_count == 2
    assert telemetry.store_post_completion_artifacts.await_count == 2
    async with session_factory() as session:
        jobs = [
            await JobRepository(session).get("pending-job"),
            await JobRepository(session).get("stale-job"),
        ]
    assert all(job is not None and job.artifact_collection_status == "completed" for job in jobs)


@pytest.mark.asyncio
async def test_completed_zero_artifact_job_is_not_recovered_repeatedly(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _create_job(session_factory)
    service, summarization, telemetry, published = _make_service(
        session_factory,
        tmp_path,
        create_artifact=False,
    )

    assert await service.finalize("job-1") == "completed"
    assert await service.finalize("job-1") == "completed"
    assert await service.recover_eligible() == 0

    summarization.save_snapshot_to_disk.assert_awaited_once_with("job-1")
    telemetry.store_post_completion_artifacts.assert_awaited_once_with("job-1")
    async with session_factory() as session:
        job = await JobRepository(session).get("job-1")
        artifacts = await ArtifactRepository(session).list_for_job("job-1")
    assert job is not None
    assert job.artifact_collection_status == "completed"
    assert job.artifact_collection_session_count == job.session_count
    assert artifacts == []
    assert [event.kind for event in published] == [EventKind.artifacts_updated]


class TestArtifactHomeIsolation:
    @pytest.mark.parametrize("existing_location", ["legacy", "active", "default"])
    async def test_finalization_persists_same_id_path_and_size_across_sessions(
        self, existing_location, session_factory, tmp_path, monkeypatch
    ):
        import backend.config as config
        import backend.services.artifacts.artifact_service as mod

        home = tmp_path / "home"
        legacy = home / ".codeplane" / "artifacts"
        active = tmp_path / "configured" / "artifacts"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr(config, "_codeplane_dir", None)
        monkeypatch.setattr(mod, "_ARTIFACTS_BASE", legacy, raising=False)
        if existing_location == "default":
            monkeypatch.delenv("CODEPLANE_HOME", raising=False)
            active = legacy
        else:
            monkeypatch.setenv("CODEPLANE_HOME", str(active.parent))
        root = legacy if existing_location == "legacy" else active
        original = root / "job-1" / "art-log-history.json"
        original.parent.mkdir(parents=True)
        original.write_text(
            '{"original_task":"original","sessions":[{"session_number":1,"changed_files":["old.py"]}]}',
            encoding="utf-8",
        )
        before = original.read_bytes()
        await _create_job(session_factory)
        async with serialized_write(session_factory) as session:
            await ArtifactRepository(session).create(
                Artifact(
                    id="art-log",
                    job_id="job-1",
                    name="history.json",
                    type=ArtifactType.session_log,
                    mime_type="application/json",
                    size_bytes=len(before),
                    disk_path=str(original),
                    phase=ExecutionPhase.post_completion,
                    created_at=datetime.now(UTC),
                )
            )
        service, summarization, telemetry, published = _make_service(session_factory, tmp_path)

        async def collect(job_id):
            async with serialized_write(session_factory) as session:
                await mod.ArtifactService.from_session(session).upsert_session_log(
                    job_id, {"session_number": 2, "changed_files": ["new.py"]}
                )

        summarization.save_snapshot_to_disk.side_effect = collect
        assert await service.finalize("job-1") == "completed"
        assert await service.finalize("job-1") == "completed"
        async with session_factory() as session:
            rows = await ArtifactRepository(session).list_for_job("job-1")
            job = await JobRepository(session).get("job-1")
        assert len(rows) == 1
        persisted = rows[0]
        assert persisted.id == "art-log"
        assert Path(persisted.disk_path).resolve().is_relative_to(active.resolve())
        assert persisted.size_bytes == Path(persisted.disk_path).stat().st_size
        assert persisted.size_bytes != len(before)
        if existing_location == "legacy":
            assert original.read_bytes() == before
            assert Path(persisted.disk_path) != original
        else:
            assert Path(persisted.disk_path) == original
        content = json.loads(Path(persisted.disk_path).read_text(encoding="utf-8"))
        assert [s["session_number"] for s in content["sessions"]] == [1, 2]
        assert content["all_changed_files"] == ["new.py", "old.py"]
        assert content["original_task"] == "original"
        assert job is not None and job.artifact_collection_status == "completed"
        summarization.save_snapshot_to_disk.assert_awaited_once()
        telemetry.store_post_completion_artifacts.assert_awaited_once()
        assert [event.kind for event in published] == [EventKind.artifacts_updated]
