"""Artifact storage and retrieval."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.models.api_schemas import ArtifactType, ExecutionPhase
from backend.models.domain import Artifact

if TYPE_CHECKING:
    from backend.models.api_schemas import DiffFileModel
    from backend.persistence.artifact_repo import ArtifactRepository

log = structlog.get_logger()

# Default base directory for artifact files on disk
_ARTIFACTS_BASE = Path.home() / ".codeplane" / "artifacts"


class ArtifactService:
    """Collects, stores, and retrieves job artifacts."""

    def __init__(self, artifact_repo: ArtifactRepository) -> None:
        self._repo = artifact_repo

    async def store_diff_snapshot(
        self,
        job_id: str,
        diff_files: list[DiffFileModel],
    ) -> Artifact:
        """Persist the final diff snapshot as an artifact on disk + DB."""
        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        name = "diff-snapshot.json"

        # Serialize to disk
        disk_dir = _ARTIFACTS_BASE / job_id
        disk_dir.mkdir(parents=True, exist_ok=True)
        disk_path = disk_dir / f"{artifact_id}-{name}"
        content = json.dumps(
            [f.model_dump(by_alias=True) for f in diff_files],
            indent=2,
        )
        disk_path.write_text(content, encoding="utf-8")
        size_bytes = disk_path.stat().st_size

        artifact = Artifact(
            id=artifact_id,
            job_id=job_id,
            name=name,
            type=ArtifactType.diff_snapshot,
            mime_type="application/json",
            size_bytes=size_bytes,
            disk_path=str(disk_path),
            phase=ExecutionPhase.post_completion,
            created_at=datetime.now(UTC),
        )
        return await self._repo.create(artifact)

    async def collect_from_workspace(
        self,
        job_id: str,
        worktree_path: str,
    ) -> list[Artifact]:
        """Scan .codeplane/artifacts/ in the worktree for custom artifacts."""
        collected: list[Artifact] = []
        artifacts_dir = Path(worktree_path) / ".codeplane" / "artifacts"
        if not artifacts_dir.is_dir():
            return collected

        for entry in sorted(artifacts_dir.iterdir()):
            if not entry.is_file() or entry.is_symlink():
                continue
            # Ensure resolved path is inside the worktree (prevent symlink escape)
            if not entry.resolve().is_relative_to(Path(worktree_path).resolve()):
                log.warning("artifact_outside_worktree", path=str(entry))
                continue
            # Skip files larger than 50 MB
            entry_size = entry.stat().st_size
            if entry_size > 50 * 1024 * 1024:
                log.warning("artifact_too_large", path=str(entry), size=entry_size)
                continue
            artifact_id = f"art-{uuid.uuid4().hex[:12]}"
            # Copy to central store
            disk_dir = _ARTIFACTS_BASE / job_id
            disk_dir.mkdir(parents=True, exist_ok=True)
            dest = disk_dir / f"{artifact_id}-{entry.name}"
            dest.write_bytes(entry.read_bytes())

            mime = _guess_mime(entry.name)
            artifact = Artifact(
                id=artifact_id,
                job_id=job_id,
                name=entry.name,
                type=ArtifactType.custom,
                mime_type=mime,
                size_bytes=dest.stat().st_size,
                disk_path=str(dest),
                phase=ExecutionPhase.post_completion,
                created_at=datetime.now(UTC),
            )
            collected.append(await self._repo.create(artifact))
        return collected

    async def list_for_job(self, job_id: str) -> list[Artifact]:
        """Return all artifacts for a job."""
        return await self._repo.list_for_job(job_id)

    async def get(self, artifact_id: str) -> Artifact | None:
        """Retrieve a single artifact by ID."""
        return await self._repo.get(artifact_id)

    async def store_session_summary(self, job_id: str, session_number: int, summary_json: str) -> Artifact:
        """Persist a session summary JSON as an agent_summary artifact."""
        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        name = f"session-{session_number}-summary.json"

        disk_dir = _ARTIFACTS_BASE / job_id
        disk_dir.mkdir(parents=True, exist_ok=True)
        disk_path = disk_dir / f"{artifact_id}-{name}"
        disk_path.write_text(summary_json, encoding="utf-8")
        size_bytes = disk_path.stat().st_size

        artifact = Artifact(
            id=artifact_id,
            job_id=job_id,
            name=name,
            type=ArtifactType.agent_summary,
            mime_type="application/json",
            size_bytes=size_bytes,
            disk_path=str(disk_path),
            phase=ExecutionPhase.post_completion,
            created_at=datetime.now(UTC),
        )
        return await self._repo.create(artifact)

    async def get_latest_session_summary(self, job_id: str) -> Artifact | None:
        """Return the most recent agent_summary artifact for a job, or None."""
        all_artifacts = await self._repo.list_for_job(job_id)
        summaries = [a for a in all_artifacts if a.type == ArtifactType.agent_summary]
        if not summaries:
            return None

        # Session summaries are named session-{n}-summary.json — return highest n
        def _session_num(a: Artifact) -> int:
            import re

            m = re.search(r"session-(\d+)-summary", a.name)
            return int(m.group(1)) if m else 0

        return max(summaries, key=_session_num)

    async def store_session_snapshot(self, job_id: str, session_number: int, snapshot_json: str) -> Artifact:
        """Persist a raw session snapshot (deduped transcript + changed files).

        This is cheap (no LLM) and stored at session end. The actual
        LLM-based summary is generated on-demand during cold resumes.
        """
        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        name = f"session-{session_number}-snapshot.json"

        disk_dir = _ARTIFACTS_BASE / job_id
        disk_dir.mkdir(parents=True, exist_ok=True)
        disk_path = disk_dir / f"{artifact_id}-{name}"
        disk_path.write_text(snapshot_json, encoding="utf-8")
        size_bytes = disk_path.stat().st_size

        artifact = Artifact(
            id=artifact_id,
            job_id=job_id,
            name=name,
            type=ArtifactType.session_snapshot,
            mime_type="application/json",
            size_bytes=size_bytes,
            disk_path=str(disk_path),
            phase=ExecutionPhase.post_completion,
            created_at=datetime.now(UTC),
        )
        return await self._repo.create(artifact)

    async def get_latest_session_snapshot(self, job_id: str) -> Artifact | None:
        """Return the most recent session_snapshot artifact, or None."""
        all_artifacts = await self._repo.list_for_job(job_id)
        snapshots = [a for a in all_artifacts if a.type == ArtifactType.session_snapshot]
        if not snapshots:
            return None

        def _session_num(a: Artifact) -> int:
            import re

            m = re.search(r"session-(\d+)-snapshot", a.name)
            return int(m.group(1)) if m else 0

        return max(snapshots, key=_session_num)


def _guess_mime(filename: str) -> str:
    """Simple MIME type guessing from file extension."""
    ext = Path(filename).suffix.lower()
    mapping = {
        ".json": "application/json",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".log": "text/plain",
        ".html": "text/html",
        ".csv": "text/csv",
        ".xml": "application/xml",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
    }
    return mapping.get(ext, "application/octet-stream")
