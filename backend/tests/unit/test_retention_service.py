"""Tests for RetentionService — artifact cleanup, worktree cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import CPLConfig
from backend.models.db import ArtifactRow, Base, JobRow
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.retention_service import RetentionService

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
                JobRow(
                    id="job-old",
                    repo="/test",
                    prompt="test",
                    state="succeeded",
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
                JobRow(
                    id="job-new",
                    repo="/test",
                    prompt="test",
                    state="succeeded",
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
