"""Retention policy — artifact cleanup, worktree cleanup, daily background task."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import delete, select

from backend.config import CODEPLANE_DIR
from backend.models.db import ArtifactRow, DiffSnapshotRow, JobRow
from backend.models.domain import TERMINAL_STATES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CPLConfig

log = structlog.get_logger()

ARTIFACTS_DIR = CODEPLANE_DIR / "artifacts"
CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours


class RetentionService:
    """Manages retention cleanup for artifacts, diff snapshots, and worktrees."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        self._session_factory = session_factory
        self._retention_days = config.retention.artifact_retention_days
        self._auto_archive_days = config.retention.auto_archive_days
        self._worktrees_dirname = config.runtime.worktrees_dirname

    async def run_cleanup(self) -> dict[str, int]:
        """Run a full retention cleanup pass.

        Returns a summary dict with counts of deleted items.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(days=self._retention_days)
        log.info("retention_cleanup_start", cutoff=cutoff.isoformat())

        artifacts_deleted = await self._cleanup_artifacts(cutoff)
        snapshots_deleted = await self._cleanup_diff_snapshots(cutoff)
        worktrees_deleted = await self._cleanup_worktrees(cutoff)

        archive_cutoff = datetime.now(tz=UTC) - timedelta(days=self._auto_archive_days)
        auto_archived = await self._auto_archive_resolved_jobs(archive_cutoff)

        summary = {
            "artifacts_deleted": artifacts_deleted,
            "snapshots_deleted": snapshots_deleted,
            "worktrees_deleted": worktrees_deleted,
            "auto_archived": auto_archived,
        }
        log.info("retention_cleanup_done", **summary)
        return summary

    async def daily_loop(self) -> None:
        """Run cleanup every 24 hours. Designed to be launched as a background task."""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            try:
                await self.run_cleanup()
            except Exception:
                log.exception("retention_cleanup_error")

    # ------------------------------------------------------------------
    # Internal cleanup methods
    # ------------------------------------------------------------------

    async def _cleanup_artifacts(self, cutoff: datetime) -> int:
        """Delete artifact files and metadata older than cutoff."""
        async with self._session_factory() as session:
            # Find expired artifacts
            stmt = select(ArtifactRow).where(ArtifactRow.created_at < cutoff)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            if not rows:
                return 0

            # Delete metadata rows first (orphan files are harmless; orphan rows cause errors)
            artifact_ids = [str(row.id) for row in rows]
            await session.execute(delete(ArtifactRow).where(ArtifactRow.id.in_(artifact_ids)))
            await session.commit()

            # Delete files from disk — only if under the expected artifacts directory
            artifacts_root = ARTIFACTS_DIR.resolve()
            for row in rows:
                disk_path = Path(str(row.disk_path)).resolve()
                if not disk_path.is_relative_to(artifacts_root):
                    log.warning("retention_artifact_path_outside_store", path=str(disk_path))
                    continue
                if disk_path.exists():
                    disk_path.unlink(missing_ok=True)

            # Delete per-job artifact directories if empty
            job_dirs: set[Path] = set()
            for row in rows:
                job_dir = Path(str(row.disk_path)).resolve()
                if job_dir.is_relative_to(artifacts_root):
                    job_dirs.add(job_dir.parent)

            for job_dir in job_dirs:
                if job_dir.exists() and not any(job_dir.iterdir()):
                    job_dir.rmdir()

            log.info("retention_artifacts_cleaned", count=len(rows))
            return len(rows)

    async def _cleanup_diff_snapshots(self, cutoff: datetime) -> int:
        """Delete diff snapshots for terminal-state jobs older than cutoff."""
        async with self._session_factory() as session:
            # Find jobs in terminal states completed before cutoff
            stmt = select(JobRow.id).where(
                JobRow.state.in_(TERMINAL_STATES),
                JobRow.completed_at < cutoff,
            )
            result = await session.execute(stmt)
            job_ids = [str(row[0]) for row in result.all()]

            if not job_ids:
                return 0

            del_stmt = delete(DiffSnapshotRow).where(DiffSnapshotRow.job_id.in_(job_ids))
            del_result = await session.execute(del_stmt)
            count = del_result.rowcount  # type: ignore[attr-defined]
            await session.commit()

            log.info("retention_snapshots_cleaned", count=count)
            return int(count)

    async def _cleanup_worktrees(self, cutoff: datetime) -> int:
        """Remove worktree directories for terminal-state jobs older than cutoff."""
        async with self._session_factory() as session:
            stmt = select(JobRow).where(
                JobRow.state.in_(TERMINAL_STATES),
                JobRow.completed_at < cutoff,
                JobRow.worktree_path.isnot(None),
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            if not rows:
                return 0

            count = 0
            for row in rows:
                wt_path = Path(str(row.worktree_path)).resolve()
                repo_worktrees_dir = (Path(str(row.repo)) / self._worktrees_dirname).resolve()
                if not str(wt_path).startswith(str(repo_worktrees_dir) + "/"):
                    log.warning("retention_worktree_outside_dir", path=str(wt_path))
                    continue
                if wt_path.exists() and wt_path.is_dir():
                    shutil.rmtree(wt_path, ignore_errors=True)
                    count += 1
                    log.info("retention_worktree_removed", path=str(wt_path))

            return count

    async def _auto_archive_resolved_jobs(self, cutoff: datetime) -> int:
        """Auto-archive resolved jobs older than the cutoff."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(JobRow).where(
                    JobRow.state == "succeeded",
                    JobRow.resolution.in_(["merged", "pr_created", "discarded"]),
                    JobRow.archived_at.is_(None),
                    JobRow.completed_at < cutoff,
                )
            )
            rows = result.scalars().all()
            now = datetime.now(UTC)
            count = 0
            for row in rows:
                row.archived_at = now  # type: ignore[assignment]
                count += 1
            await session.commit()
            if count > 0:
                log.info("auto_archived_resolved_jobs", count=count)
            return count
