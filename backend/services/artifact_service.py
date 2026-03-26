"""Artifact storage and retrieval."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.api_schemas import ArtifactType, ExecutionPhase
from backend.models.domain import Artifact

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.models.api_schemas import DiffFileModel
    from backend.persistence.artifact_repo import ArtifactRepository

log = structlog.get_logger()

# Default base directory for artifact files on disk
_ARTIFACTS_BASE = Path.home() / ".codeplane" / "artifacts"

# Maximum file size for workspace artifacts (50 MB)
_MAX_WORKSPACE_ARTIFACT_BYTES = 50 * 1024 * 1024


def get_artifacts_base() -> Path:
    """Return the base directory for artifact files on disk."""
    return _ARTIFACTS_BASE


class ArtifactService:
    """Collects, stores, and retrieves job artifacts."""

    def __init__(self, artifact_repo: ArtifactRepository) -> None:
        self._repo = artifact_repo

    @classmethod
    def from_session(cls, session: AsyncSession) -> ArtifactService:
        """Construct an ArtifactService from a DB session.

        This factory keeps persistence imports inside the service layer so
        that callers (e.g. API routes) never import repository classes.
        """
        from backend.persistence.artifact_repo import ArtifactRepository

        return cls(ArtifactRepository(session))

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
            if entry_size > _MAX_WORKSPACE_ARTIFACT_BYTES:
                log.warning("artifact_too_large", path=str(entry), size=entry_size)
                continue
            artifact_id = f"art-{uuid.uuid4().hex[:12]}"
            # Copy to central store
            disk_dir = _ARTIFACTS_BASE / job_id
            disk_dir.mkdir(parents=True, exist_ok=True)
            dest = disk_dir / f"{artifact_id}-{entry.name}"
            dest.write_bytes(entry.read_bytes())

            mime = _guess_mime(entry.name)
            art_type = _classify_artifact(entry.name)
            artifact = Artifact(
                id=artifact_id,
                job_id=job_id,
                name=entry.name,
                type=art_type,
                mime_type=mime,
                size_bytes=dest.stat().st_size,
                disk_path=str(dest),
                phase=ExecutionPhase.post_completion,
                created_at=datetime.now(UTC),
            )
            collected.append(await self._repo.create(artifact))
        return collected

    async def collect_from_session_storage(
        self,
        job_id: str,
        sdk_session_id: str,
        config_dir: Path | None = None,
    ) -> list[Artifact]:
        """Collect markdown files the agent created in its Copilot session-state folder.

        Scans the top-level of ``{config_dir}/session-state/{sdk_session_id}/`` and
        the ``files/`` subdirectory for ``*.md`` files, storing each as a ``document``
        artifact.  Other subdirectories (e.g. ``checkpoints/``) are intentionally
        skipped to avoid capturing auto-generated files.

        On subsequent sessions, existing document artifacts with the same name are
        updated in-place rather than duplicated, so the artifacts panel shows one
        entry per document across all handoffs.
        """
        collected: list[Artifact] = []
        base = config_dir if config_dir is not None else (Path.home() / ".copilot")
        session_dir = base / "session-state" / sdk_session_id
        if not session_dir.is_dir():
            return collected

        # Pre-load existing document artifacts for this job so we can upsert by name.
        existing_artifacts = await self._repo.list_for_job(job_id)
        existing_docs_by_name = {a.name: a for a in existing_artifacts if a.type == ArtifactType.document}

        # Scan the top-level directory and the designated files/ subdirectory.
        # Other subdirectories (e.g. checkpoints/) are intentionally skipped to
        # avoid capturing auto-generated files.
        scan_dirs = [session_dir, session_dir / "files"]
        for scan_dir in scan_dirs:
            if not scan_dir.is_dir():
                continue
            for entry in sorted(scan_dir.iterdir()):
                if not entry.is_file() or entry.is_symlink():
                    continue
                if entry.suffix.lower() != ".md":
                    continue
                # Guard against symlink escape (resolved path must stay inside session_dir)
                if not entry.resolve().is_relative_to(session_dir.resolve()):
                    log.warning("session_storage_artifact_outside_dir", path=str(entry))
                    continue
                entry_size = entry.stat().st_size
                if entry_size > _MAX_WORKSPACE_ARTIFACT_BYTES:
                    log.warning("session_storage_artifact_too_large", path=str(entry), size=entry_size)
                    continue

                # Preserve the relative sub-path in the artifact name so that two
                # files with the same filename in different directories remain
                # distinguishable in the UI (e.g. "plan.md" vs "files/plan.md").
                relative_name = str(entry.relative_to(session_dir))

                # Upsert: overwrite the existing artifact's file if one already exists
                # for this job with the same name, so sessions don't litter the panel.
                existing_doc = existing_docs_by_name.get(relative_name)
                if existing_doc is not None:
                    dest = Path(existing_doc.disk_path)
                    dest.write_bytes(entry.read_bytes())
                    await self._repo.update_size_bytes(existing_doc.id, dest.stat().st_size)
                    collected.append(existing_doc)
                    continue

                artifact_id = f"art-{uuid.uuid4().hex[:12]}"
                disk_dir = _ARTIFACTS_BASE / job_id
                disk_dir.mkdir(parents=True, exist_ok=True)
                dest = disk_dir / f"{artifact_id}-{entry.name}"
                dest.write_bytes(entry.read_bytes())

                mime = _guess_mime(entry.name)
                art_type = _classify_artifact(entry.name)
                artifact = Artifact(
                    id=artifact_id,
                    job_id=job_id,
                    name=relative_name,
                    type=art_type,
                    mime_type=mime,
                    size_bytes=dest.stat().st_size,
                    disk_path=str(dest),
                    phase=ExecutionPhase.post_completion,
                    created_at=datetime.now(UTC),
                )
                collected.append(await self._repo.create(artifact))
        return collected

    async def list_for_job(self, job_id: str) -> list[Artifact]:
        """Return all artifacts for a job.

        Pass-through to the repository layer. This indirection exists so
        that future business logic (permission checks, filtering, caching)
        can be added in one place without changing callers.
        """
        return await self._repo.list_for_job(job_id)

    async def get(self, artifact_id: str) -> Artifact | None:
        """Retrieve a single artifact by ID.

        Pass-through to the repository layer — same rationale as
        ``list_for_job`` above.
        """
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

    async def store_session_snapshot(
        self,
        job_id: str,
        session_number: int,
        snapshot_json: str,
        *,
        slug: str = "",
    ) -> Artifact:
        """Persist a raw session snapshot (deduped transcript + changed files).

        This is cheap (no LLM) and stored at session end. The actual
        LLM-based summary is generated on-demand during cold resumes.

        Delegates to `upsert_session_log` to maintain a single unified
        session log per job.  Kept for backward compatibility.
        """
        session_data = json.loads(snapshot_json)
        return await self.upsert_session_log(job_id, session_data, slug=slug)

    async def upsert_session_log(
        self,
        job_id: str,
        session_data: dict[str, Any],
        *,
        slug: str = "",
    ) -> Artifact:
        """Create or append to a unified session log for this job.

        The log accumulates every session into a single JSON file so that
        the full task history lives in one artifact with a human-friendly
        name derived from the job title / worktree name.

        *session_data* must contain at least ``session_number`` and
        ``transcript_turns``.  ``original_task`` and ``changed_files``
        are also expected.

        On first call the file is created; on subsequent calls the new
        session entry is appended and the file is overwritten in-place.
        Any legacy ``session_snapshot`` artifacts for this job are
        retroactively merged into the unified log.
        """
        import re as _re

        session_number = session_data.get("session_number", 0)

        # --- Try to find an existing unified log artifact ---
        existing_log = await self._get_session_log_artifact(job_id)

        if existing_log is not None:
            # Append to existing unified log
            try:
                log_contents = json.loads(Path(existing_log.disk_path).read_text(encoding="utf-8"))
            except Exception:
                log.warning(
                    "session_log_read_failed",
                    job_id=job_id,
                    disk_path=existing_log.disk_path,
                    exc_info=True,
                )
                log_contents = {"sessions": []}

            # Avoid duplicate session entries
            existing_nums = {s.get("session_number") for s in log_contents.get("sessions", [])}
            if session_number not in existing_nums:
                log_contents.setdefault("sessions", []).append(
                    {
                        "session_number": session_number,
                        "transcript_turns": session_data.get("transcript_turns", []),
                        "changed_files": session_data.get("changed_files", []),
                    }
                )
                log_contents["sessions"].sort(key=lambda s: s.get("session_number", 0))

            # Merge changed_files across all sessions
            all_files: set[str] = set()
            for sess in log_contents.get("sessions", []):
                all_files.update(sess.get("changed_files", []))
            log_contents["all_changed_files"] = sorted(all_files)

            # Update original_task if provided (first session wins)
            if "original_task" not in log_contents and session_data.get("original_task"):
                log_contents["original_task"] = session_data["original_task"]

            disk_path = Path(existing_log.disk_path)
            disk_path.write_text(json.dumps(log_contents, indent=2), encoding="utf-8")
            await self._repo.update_size_bytes(existing_log.id, disk_path.stat().st_size)

            log.info(
                "session_log_updated",
                job_id=job_id,
                session=session_number,
                total_sessions=len(log_contents["sessions"]),
            )
            return existing_log

        # --- First time: retroactively merge any old session_snapshot artifacts ---
        all_artifacts = await self._repo.list_for_job(job_id)
        old_snapshots = sorted(
            [a for a in all_artifacts if a.type == ArtifactType.session_snapshot],
            key=lambda a: a.created_at,
        )

        sessions: list[dict[str, Any]] = []
        original_task = session_data.get("original_task", "")
        merged_files: set[str] = set()

        for snap in old_snapshots:
            try:
                snap_data = json.loads(Path(snap.disk_path).read_text(encoding="utf-8"))
                snap_num = snap_data.get("session_number", 0)
                sessions.append(
                    {
                        "session_number": snap_num,
                        "transcript_turns": snap_data.get("transcript_turns", []),
                        "changed_files": snap_data.get("changed_files", []),
                    }
                )
                merged_files.update(snap_data.get("changed_files", []))
                if not original_task:
                    original_task = snap_data.get("original_task", "")
            except Exception:
                log.warning("session_snapshot_migration_failed", artifact_id=snap.id, job_id=job_id)

        # Add the current session (avoid duplicates)
        existing_nums = {s.get("session_number") for s in sessions}
        if session_number not in existing_nums:
            sessions.append(
                {
                    "session_number": session_number,
                    "transcript_turns": session_data.get("transcript_turns", []),
                    "changed_files": session_data.get("changed_files", []),
                }
            )
            merged_files.update(session_data.get("changed_files", []))

        sessions.sort(key=lambda s: s.get("session_number", 0))

        log_contents = {
            "job_id": job_id,
            "original_task": original_task,
            "sessions": sessions,
            "all_changed_files": sorted(merged_files),
        }

        # --- Smart naming from slug ---
        tag = slug.strip() if slug else ""
        tag = _re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")[:40]
        if not tag:
            tag = job_id[:12]
        name = f"{tag}-session-log.json"

        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        disk_dir = _ARTIFACTS_BASE / job_id
        disk_dir.mkdir(parents=True, exist_ok=True)
        disk_path = disk_dir / f"{artifact_id}-{name}"
        disk_path.write_text(json.dumps(log_contents, indent=2), encoding="utf-8")

        artifact = Artifact(
            id=artifact_id,
            job_id=job_id,
            name=name,
            type=ArtifactType.session_log,
            mime_type="application/json",
            size_bytes=disk_path.stat().st_size,
            disk_path=str(disk_path),
            phase=ExecutionPhase.post_completion,
            created_at=datetime.now(UTC),
        )
        created = await self._repo.create(artifact)

        migrated = len(old_snapshots)
        log.info(
            "session_log_created",
            job_id=job_id,
            session=session_number,
            total_sessions=len(sessions),
            migrated_snapshots=migrated,
        )
        return created

    async def _get_first_artifact_by_type(
        self,
        job_id: str,
        art_type: ArtifactType,
        *,
        name_suffix: str = "",
    ) -> Artifact | None:
        """Return the first (oldest) artifact of *art_type* for a job, or None.

        When *name_suffix* is provided, only artifacts whose name ends with
        that string are considered (used to disambiguate ``document`` entries
        that map to different file kinds, e.g. ``agent.log``).
        """
        all_artifacts = await self._repo.list_for_job(job_id)
        matches = [a for a in all_artifacts if a.type == art_type]
        if name_suffix:
            matches = [a for a in matches if a.name.endswith(name_suffix)]
        return matches[0] if matches else None

    async def _get_session_log_artifact(self, job_id: str) -> Artifact | None:
        """Return the unified session_log artifact for a job, if one exists."""
        return await self._get_first_artifact_by_type(job_id, ArtifactType.session_log)

    async def get_session_log(self, job_id: str) -> Artifact | None:
        """Return the unified session log, or fall back to the latest session_snapshot."""
        unified = await self._get_session_log_artifact(job_id)
        if unified is not None:
            return unified
        # Fall back to legacy session_snapshot for jobs that haven't been unified yet
        return await self.get_latest_session_snapshot(job_id)

    async def get_latest_session_snapshot(self, job_id: str) -> Artifact | None:
        """Return the most recent session_snapshot artifact, or None."""
        all_artifacts = await self._repo.list_for_job(job_id)
        snapshots = [a for a in all_artifacts if a.type == ArtifactType.session_snapshot]
        if not snapshots:
            return None

        # Sort by creation time — names are no longer guaranteed to contain a numeric session ID.
        return max(snapshots, key=lambda a: a.created_at)

    async def store_log_artifact(
        self,
        job_id: str,
        log_events: list[dict[str, Any]],
        *,
        slug: str = "",
    ) -> Artifact:
        """Write agent log lines as a plain-text downloadable artifact.

        ``log_events`` is a list of ``log_line_emitted`` event payloads, each
        expected to have ``timestamp``, ``level``, ``message``,
        ``session_number`` (optional), and ``context`` (optional) keys.

        Lines are grouped by session_number so handoff boundaries are visible.
        The resulting ``.log`` file is registered as a ``document`` artifact
        with ``text/plain`` MIME type so the ArtifactViewer can preview it in-
        browser and offer a download link.

        Upserts: if a log artifact already exists for this job it is overwritten
        with the full accumulated log so resumed sessions produce a single
        cumulative document rather than one per session completion.
        """
        import re as _re

        tag = _re.sub(r"[^a-z0-9]+", "-", (slug or "").lower()).strip("-")[:40]
        if not tag:
            tag = job_id[:12]
        name = f"{tag}-agent.log"

        # Sort by sequence number (or timestamp as fallback).
        # Events without session_number (legacy) are treated as session 1.
        sorted_events = sorted(log_events, key=lambda e: (e.get("session_number") or 1, e.get("seq") or 0))

        lines: list[str] = []
        current_session: int | None = None
        for evt in sorted_events:
            sess_num = evt.get("session_number") or 1
            if sess_num != current_session:
                current_session = sess_num
                lines.append(f"\n── session {sess_num} ──────────────────────────────────────\n")
            ts = evt.get("timestamp", "")
            if hasattr(ts, "isoformat"):
                ts = ts.isoformat()
            level = str(evt.get("level", "info")).upper().ljust(5)
            message = evt.get("message", "")
            ctx = evt.get("context")
            ctx_str = ""
            if ctx:
                ctx_str = "  " + "  ".join(f"{k}={v}" for k, v in ctx.items())
            lines.append(f"{ts}  {level}  {message}{ctx_str}\n")

        content = f"# CodePlane agent log — job {job_id}\n" + "".join(lines)

        # Upsert: reuse existing log artifact if one exists so we don't accumulate
        # one file per session completion.
        existing = await self._get_first_artifact_by_type(job_id, ArtifactType.document, name_suffix="agent.log")
        if existing is not None:
            disk_path = Path(existing.disk_path)
            disk_path.write_text(content, encoding="utf-8")
            new_size = disk_path.stat().st_size
            await self._repo.update_size_bytes(existing.id, new_size)
            log.info("log_artifact_updated", job_id=job_id, name=existing.name, size=new_size)
            return existing

        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        disk_dir = _ARTIFACTS_BASE / job_id
        disk_dir.mkdir(parents=True, exist_ok=True)
        disk_path = disk_dir / f"{artifact_id}-{name}"
        disk_path.write_text(content, encoding="utf-8")

        artifact = Artifact(
            id=artifact_id,
            job_id=job_id,
            name=name,
            type=ArtifactType.document,
            mime_type="text/plain",
            size_bytes=disk_path.stat().st_size,
            disk_path=str(disk_path),
            phase=ExecutionPhase.post_completion,
            created_at=datetime.now(UTC),
        )
        log.info("log_artifact_stored", job_id=job_id, name=name, size=artifact.size_bytes)
        return await self._repo.create(artifact)

    async def store_telemetry_report(
        self,
        job_id: str,
        telemetry_dict: dict[str, Any],
        *,
        slug: str = "",
    ) -> Artifact:
        """Persist the final telemetry snapshot as a downloadable artifact.

        Upserts: if a telemetry report already exists for this job it is
        overwritten in-place so resumed sessions accumulate into a single
        artifact rather than creating one per session completion.
        """
        content = json.dumps(telemetry_dict, indent=2)

        existing = await self._get_first_artifact_by_type(job_id, ArtifactType.telemetry_report)
        if existing is not None:
            disk_path = Path(existing.disk_path)
            disk_path.write_text(content, encoding="utf-8")
            await self._repo.update_size_bytes(existing.id, disk_path.stat().st_size)
            return existing

        import re as _re

        tag = _re.sub(r"[^a-z0-9]+", "-", (slug or "").lower()).strip("-")[:40]
        if not tag:
            tag = job_id[:12]
        name = f"{tag}-telemetry.json"

        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        disk_dir = _ARTIFACTS_BASE / job_id
        disk_dir.mkdir(parents=True, exist_ok=True)
        disk_path = disk_dir / f"{artifact_id}-{name}"
        disk_path.write_text(content, encoding="utf-8")

        artifact = Artifact(
            id=artifact_id,
            job_id=job_id,
            name=name,
            type=ArtifactType.telemetry_report,
            mime_type="application/json",
            size_bytes=disk_path.stat().st_size,
            disk_path=str(disk_path),
            phase=ExecutionPhase.post_completion,
            created_at=datetime.now(UTC),
        )
        return await self._repo.create(artifact)

    async def store_agent_plan(
        self,
        job_id: str,
        steps: list[dict[str, str]],
        *,
        slug: str = "",
    ) -> Artifact | None:
        """Persist the agent's execution plan steps as an artifact.

        Returns None if steps is empty (no plan was generated).

        Upserts: if a plan artifact already exists for this job it is
        overwritten in-place so resumed sessions keep a single up-to-date plan.
        """
        if not steps:
            return None

        content = json.dumps({"steps": steps}, indent=2)

        existing = await self._get_first_artifact_by_type(job_id, ArtifactType.agent_plan)
        if existing is not None:
            disk_path = Path(existing.disk_path)
            disk_path.write_text(content, encoding="utf-8")
            await self._repo.update_size_bytes(existing.id, disk_path.stat().st_size)
            return existing

        import re as _re

        tag = _re.sub(r"[^a-z0-9]+", "-", (slug or "").lower()).strip("-")[:40]
        if not tag:
            tag = job_id[:12]
        name = f"{tag}-plan.json"

        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        disk_dir = _ARTIFACTS_BASE / job_id
        disk_dir.mkdir(parents=True, exist_ok=True)
        disk_path = disk_dir / f"{artifact_id}-{name}"
        disk_path.write_text(content, encoding="utf-8")

        artifact = Artifact(
            id=artifact_id,
            job_id=job_id,
            name=name,
            type=ArtifactType.agent_plan,
            mime_type="application/json",
            size_bytes=disk_path.stat().st_size,
            disk_path=str(disk_path),
            phase=ExecutionPhase.post_completion,
            created_at=datetime.now(UTC),
        )
        return await self._repo.create(artifact)

    async def store_approval_history(
        self,
        job_id: str,
        approvals: list[dict[str, Any]],
        *,
        slug: str = "",
    ) -> Artifact | None:
        """Persist the approval request/resolution history as an artifact.

        Returns None if there were no approval requests during the job.

        Upserts: if an approval history artifact already exists for this job it
        is overwritten in-place so resumed sessions accumulate into one record.
        """
        if not approvals:
            return None

        content = json.dumps({"approvals": approvals}, indent=2)

        existing = await self._get_first_artifact_by_type(job_id, ArtifactType.approval_history)
        if existing is not None:
            disk_path = Path(existing.disk_path)
            disk_path.write_text(content, encoding="utf-8")
            await self._repo.update_size_bytes(existing.id, disk_path.stat().st_size)
            return existing

        import re as _re

        tag = _re.sub(r"[^a-z0-9]+", "-", (slug or "").lower()).strip("-")[:40]
        if not tag:
            tag = job_id[:12]
        name = f"{tag}-approvals.json"

        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        disk_dir = _ARTIFACTS_BASE / job_id
        disk_dir.mkdir(parents=True, exist_ok=True)
        disk_path = disk_dir / f"{artifact_id}-{name}"
        disk_path.write_text(content, encoding="utf-8")

        artifact = Artifact(
            id=artifact_id,
            job_id=job_id,
            name=name,
            type=ArtifactType.approval_history,
            mime_type="application/json",
            size_bytes=disk_path.stat().st_size,
            disk_path=str(disk_path),
            phase=ExecutionPhase.post_completion,
            created_at=datetime.now(UTC),
        )
        return await self._repo.create(artifact)


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


_DOCUMENT_EXTENSIONS = frozenset({".md", ".txt", ".html", ".csv", ".log"})


def _classify_artifact(filename: str) -> ArtifactType:
    """Classify a workspace artifact by extension."""
    ext = Path(filename).suffix.lower()
    if ext in _DOCUMENT_EXTENSIONS:
        return ArtifactType.document
    return ArtifactType.custom
