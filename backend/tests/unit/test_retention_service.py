"""Tests for RetentionService — artifact cleanup, worktree cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import CPLConfig
from backend.models.db import ArtifactRow, Base, JobRow, ProjectRow
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.job.retention_service import RetentionService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def config() -> CPLConfig:
    cfg = CPLConfig()
    cfg.retention.artifact_retention_days = 7
    return cfg


@pytest.fixture
def retention_svc(session_factory: async_sessionmaker[AsyncSession], config: CPLConfig) -> RetentionService:
    return RetentionService(session_factory, config)


class TestRunCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_returns_summary(self, retention_svc: RetentionService) -> None:
        result = await retention_svc.run_cleanup()
        assert "artifacts_deleted" in result
        assert "snapshots_deleted" in result
        assert "worktrees_deleted" in result

    @pytest.mark.asyncio
    async def test_cleanup_deletes_old_artifacts(
        self,
        retention_svc: RetentionService,
        session_factory: async_sessionmaker[AsyncSession],
        tmp_path: Path,
    ) -> None:
        # Insert a job and an old artifact
        old_time = datetime.now(UTC) - timedelta(days=30)
        async with session_factory() as session:
            session.add(
                ProjectRow(
                    id="proj-old",
                    name="Test Project",
                    repo_paths='["/test"]',
                    created_at=old_time,
                    updated_at=old_time,
                )
            )
            await session.flush()
            session.add(
                JobRow(
                    id="job-old",
                    repo="/test",
                    project_id="proj-old",
                    prompt="test",
                    state="completed",
                    base_ref="main",
                    created_at=old_time,
                    updated_at=old_time,
                )
            )
            await session.flush()
            disk_file = tmp_path / "old-artifact.json"
            disk_file.write_text("{}")
            session.add(
                ArtifactRow(
                    id="art-old",
                    job_id="job-old",
                    name="old.json",
                    type="custom",
                    mime_type="application/json",
                    size_bytes=2,
                    disk_path=str(disk_file),
                    phase="post_completion",
                    created_at=old_time,
                )
            )
            await session.commit()

        result = await retention_svc.run_cleanup()
        assert result["artifacts_deleted"] >= 1

    @pytest.mark.asyncio
    async def test_cleanup_keeps_recent_artifacts(
        self,
        retention_svc: RetentionService,
        session_factory: async_sessionmaker[AsyncSession],
        tmp_path: Path,
    ) -> None:
        # Insert a recent artifact (should NOT be deleted)
        now = datetime.now(UTC)
        async with session_factory() as session:
            session.add(
                ProjectRow(
                    id="proj-new",
                    name="Test Project",
                    repo_paths='["/test"]',
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                JobRow(
                    id="job-new",
                    repo="/test",
                    project_id="proj-new",
                    prompt="test",
                    state="completed",
                    base_ref="main",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            disk_file = tmp_path / "new-artifact.json"
            disk_file.write_text("{}")
            session.add(
                ArtifactRow(
                    id="art-new",
                    job_id="job-new",
                    name="new.json",
                    type="custom",
                    mime_type="application/json",
                    size_bytes=2,
                    disk_path=str(disk_file),
                    phase="post_completion",
                    created_at=now,
                )
            )
            await session.commit()

        result = await retention_svc.run_cleanup()
        assert result["artifacts_deleted"] == 0
        assert disk_file.exists()


@pytest.mark.parametrize("archive_mode", ["manual", "retention"])
async def test_archive_preserves_pending_tracker_operation_evidence(
    session_factory,
    config,
    retention_svc,
    archive_mode,
):
    from backend.models.db import CredentialRow, TrackerLinkRow
    from backend.persistence.tracker_operation_repo import TrackerOperationRepository
    from backend.services.job.approval_service import ApprovalService
    from backend.services.job.job_service import JobService
    from backend.services.tracker_write_service import TrackerWriteAction, TrackerWriteRequest, TrackerWriteService

    old = datetime.now(UTC) - timedelta(days=30)
    config.retention.auto_archive_days = 7
    service = TrackerWriteService(ApprovalService(session_factory), session_factory=session_factory)
    async with session_factory() as session:
        session.add(
            ProjectRow(
                id="outbox-project",
                name="Outbox",
                repo_paths='["/test"]',
                created_at=old,
                updated_at=old,
            )
        )
        session.add(
            CredentialRow(
                id="outbox-credential",
                provider="github",
                label="Test",
                base_url="https://api.github.com",
                encrypted_secret="unused",
                created_at=old.isoformat(),
            )
        )
        await session.flush()
        session.add(
            JobRow(
                id="outbox-job",
                project_id="outbox-project",
                repo="/test",
                prompt="Test",
                state="completed",
                resolution="merged",
                base_ref="main",
                created_at=old,
                updated_at=old,
                completed_at=old,
            )
        )
        session.add(
            TrackerLinkRow(
                id="outbox-link",
                project_id="outbox-project",
                credential_id="outbox-credential",
                external_ref="owner/1",
                created_at=old.isoformat(),
            )
        )
        await session.flush()
        operation = await service.enqueue_in_session(
            session,
            "outbox-job",
            TrackerWriteRequest(
                "outbox-link",
                "owner/repo#7",
                TrackerWriteAction.comment,
                "Retain exact evidence.",
            ),
        )
        await session.commit()
    if archive_mode == "manual":
        async with session_factory() as session:
            await JobService.from_session(session, config).archive_job("outbox-job")
            await session.commit()
    else:
        result = await RetentionService(session_factory, config).run_cleanup()
        assert result["auto_archived"] == 1
    async with session_factory() as session:
        job = await session.get(JobRow, "outbox-job")
        retained = await TrackerOperationRepository(session).get(operation.id)
        assert job is not None and job.archived_at is not None
        assert retained is not None
        assert retained.job_id == retained.job_ref == "outbox-job"
        assert retained.project_id == retained.project_ref == "outbox-project"
        assert retained.state == "awaiting_approval"
        assert retained.value == "Retain exact evidence."
    await service.close()
