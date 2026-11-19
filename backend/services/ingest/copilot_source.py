"""TraceForge-backed Copilot CLI imported session source."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
import yaml

from backend.models.domain import Job, JobSource, JobState
from backend.models.events import EventKind
from backend.services.completers.copilot_steer import NativeDeliveryError, NativeSessionBinding
from backend.services.ingest._base import TraceForgeIngestBase, repo_basename

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CPLConfig
    from backend.services.completers.copilot_steer import CopilotSteerClient
    from backend.services.events.event_processor import EventProcessor
    from backend.services.git.git_service import GitService
    from backend.services.runtime import RuntimeService

log = structlog.get_logger()

_COPILOT_HOME = Path(os.environ.get("COPILOT_HOME", str(Path.home() / ".copilot"))).expanduser().resolve()
_SESSION_STORE_PATH = _COPILOT_HOME / "session-store.db"
_SESSION_STATE_DIR = _COPILOT_HOME / "session-state"
_DISCOVERY_POLL_S = 2.0
_SESSION_ID_PATTERN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _cwd_matches_repo(cwd: str, repo_path: str) -> bool:
    return cwd == repo_path or cwd.startswith(repo_path + "/") or cwd.startswith(repo_path + "\\")


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


class SessionStateWatcher(TraceForgeIngestBase):
    """Discovers and ingests remote-steerable Copilot CLI sessions."""

    _watcher_log_prefix = "session_state_watcher"

    def __init__(
        self,
        event_processor: EventProcessor,
        runtime_service: RuntimeService,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
        git_service: GitService | None = None,
        steer_client: CopilotSteerClient | None = None,
    ) -> None:
        super().__init__(event_processor, runtime_service, session_factory, "copilot")
        self._config = config
        self._git = git_service
        self._steer = steer_client
        self._started_at: str | None = None

    async def start(self) -> None:
        if not _SESSION_STORE_PATH.exists():
            log.info("session_state_watcher_no_store", path=str(_SESSION_STORE_PATH))
        self._running = True
        self._started_at = datetime.now(UTC).isoformat()
        await self._load_existing_sessions()
        self._discovery_task = asyncio.create_task(self._discovery_loop(), name="session-state-discovery")

    async def stop(self) -> None:
        await self._stop_common()
        log.info("session_state_watcher_stopped")

    def _resolve_binding(self, session_id: str) -> NativeSessionBinding:
        if not _is_uuid(session_id):
            raise NativeDeliveryError("Copilot local session identity is invalid")
        try:
            root = _SESSION_STATE_DIR.resolve(strict=True)
            directory = root / session_id
            workspace = directory / "workspace.yaml"
            events = directory / "events.jsonl"
            if (
                directory.resolve(strict=True) != directory
                or workspace.resolve(strict=True) != workspace
                or events.resolve(strict=True) != events
            ):
                raise NativeDeliveryError("Copilot session metadata is outside its owned path")
            metadata = yaml.safe_load(workspace.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, RuntimeError, yaml.YAMLError) as exc:
            if isinstance(exc, NativeDeliveryError):
                raise
            raise NativeDeliveryError("Copilot published task binding is missing or unreadable") from None
        if (
            not isinstance(metadata, dict)
            or metadata.get("id") != session_id
            or metadata.get("mc_session_id") != session_id
        ):
            raise NativeDeliveryError("Copilot published task binding does not match the local session")
        task_id = metadata.get("mc_task_id")
        if not isinstance(task_id, str) or not _is_uuid(task_id):
            raise NativeDeliveryError("Copilot published task identity is missing or invalid")
        if metadata.get("remote_steerable") is not True or not self._is_remote_steerable(session_id):
            raise NativeDeliveryError("Copilot remote control is not enabled")
        return NativeSessionBinding(session_id, task_id)

    async def send_message(self, session_id: str, message: str) -> None:
        if self._steer is None:
            raise NativeDeliveryError("Copilot steering is unavailable")
        binding = self._resolve_binding(session_id)
        await self._steer.send_message(binding.task_id, message)

    async def abort_session(self, session_id: str) -> None:
        if self._steer is None:
            raise NativeDeliveryError("Copilot steering is unavailable")
        binding = self._resolve_binding(session_id)
        await self._steer.abort(binding.task_id)

    async def _load_existing_sessions(self) -> None:
        running_jobs: list[tuple[str, str, int, str | None, str | None, str | None]] = []
        try:
            async with self._session_factory() as session:
                from sqlalchemy import text

                result_sdk = await session.execute(
                    text("SELECT sdk_session_id FROM jobs WHERE sdk_session_id IS NOT NULL")
                )
                for row in result_sdk:
                    self._tracked_sessions.add(row[0])

                result_cli = await session.execute(
                    text(
                        "SELECT id, external_session_id, state, tail_offset, worktree_path, base_ref, repo "
                        "FROM jobs WHERE external_session_id IS NOT NULL AND source = 'copilot_cli'"
                    )
                )
                for row in result_cli:
                    job_id, ext_sid, state, count, wt, base, repo = row
                    self._tracked_sessions.add(ext_sid)
                    if state == "running":
                        running_jobs.append((job_id, ext_sid, count or 0, wt, base, repo))

                result_other = await session.execute(
                    text(
                        "SELECT external_session_id FROM jobs "
                        "WHERE external_session_id IS NOT NULL AND source != 'copilot_cli'"
                    )
                )
                for row in result_other:
                    self._tracked_sessions.add(row[0])

            for job_id, ext_sid, count, wt, base, repo in running_jobs:
                # Reattach observation; only explicit source termination establishes an end.
                self._session_to_job[ext_sid] = job_id
                self._job_worktrees[job_id] = wt or repo or ""
                self._job_base_refs[job_id] = base or "HEAD"
                await self._runtime.register_external_session(
                    job_id,
                    self._job_worktrees[job_id],
                    self._job_base_refs[job_id],
                )
                self._start_tail(
                    ext_sid,
                    job_id,
                    _SESSION_STATE_DIR / ext_sid / "events.jsonl",
                    initial_skip_count=count,
                )
        except Exception:
            log.warning("session_state_watcher_load_existing_failed", exc_info=True)

    async def _discovery_loop(self) -> None:
        while self._running:
            try:
                managed_paths = await self._managed_repo_paths()
                for session_id, cwd, _summary in await asyncio.to_thread(self._query_new_sessions, managed_paths):
                    if session_id in self._tracked_sessions:
                        continue
                    self._tracked_sessions.add(session_id)
                    await self._attach_session(session_id, cwd)
            except asyncio.CancelledError:
                return
            except Exception:
                log.debug("session_state_watcher_discovery_error", exc_info=True)
            await asyncio.sleep(_DISCOVERY_POLL_S)

    async def _managed_repo_paths(self) -> list[str]:
        from backend.services.project.repo_membership import (
            list_managed_repo_paths_from_factory,
        )

        return await list_managed_repo_paths_from_factory(self._config, self._session_factory)

    def _query_new_sessions(self, managed_repo_paths: list[str] | None = None) -> list[tuple[str, str, str]]:
        managed_paths = set(self._config.repos if managed_repo_paths is None else managed_repo_paths)
        if not _SESSION_STORE_PATH.exists() or not managed_paths:
            return []
        results: list[tuple[str, str, str]] = []
        try:
            db = sqlite3.connect(f"{_SESSION_STORE_PATH.as_uri()}?mode=ro", uri=True, timeout=2.0)
            try:
                query = "SELECT id, cwd, summary FROM sessions WHERE 1=1"
                params: tuple[Any, ...] = ()
                if self._started_at:
                    query += " AND created_at >= ?"
                    params = (self._started_at,)
                for sid, cwd, summary in db.execute(query, params).fetchall():
                    cwd = cwd or ""
                    if sid in self._tracked_sessions or not sid or not all(c in _SESSION_ID_PATTERN for c in sid):
                        continue
                    if not any(_cwd_matches_repo(cwd, repo_path) for repo_path in managed_paths):
                        continue
                    if self._is_remote_steerable(sid):
                        results.append((sid, cwd, summary or ""))
            finally:
                db.close()
        except (sqlite3.Error, OSError):
            log.debug("session_state_watcher_db_error", exc_info=True)
        return results

    def _is_remote_steerable(self, session_id: str) -> bool:
        events_path = _SESSION_STATE_DIR / session_id / "events.jsonl"
        steerable = False
        try:
            with events_path.open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        log.debug(
                            "session_state_watcher_control_record_incomplete",
                            session_id=session_id,
                            line_number=line_number,
                        )
                        continue
                    if not isinstance(event, dict) or event.get("type") not in {
                        "session.start",
                        "session.started",
                        "session.resume",
                        "session.remote_steerable_changed",
                    }:
                        continue
                    data = event.get("data")
                    if isinstance(data, dict) and isinstance(data.get("remoteSteerable"), bool):
                        steerable = data["remoteSteerable"]
        except (OSError, UnicodeError):
            log.debug("session_state_watcher_control_read_failed", session_id=session_id, exc_info=True)
            return False
        return steerable

    async def _attach_session(self, session_id: str, cwd: str) -> None:
        job = await self._create_job(session_id, cwd)
        if job is None:
            self._tracked_sessions.discard(session_id)
            return
        job_id = job.id
        self._session_to_job[session_id] = job_id
        self._job_worktrees[job_id] = job.worktree_path or cwd
        self._job_base_refs[job_id] = job.base_ref or "HEAD"
        await self._runtime.register_external_session(job_id, self._job_worktrees[job_id], self._job_base_refs[job_id])
        self._start_tail(session_id, job_id, _SESSION_STATE_DIR / session_id / "events.jsonl")

    def _start_tail(self, session_id: str, job_id: str, events_path: Path, *, initial_skip_count: int = 0) -> None:
        task = asyncio.create_task(
            self._tail_traceforge_events(
                session_id,
                job_id,
                events_path,
                initial_skip_count=initial_skip_count,
                finalize_on_raw=self._raw_terminal,
            ),
            name=f"copilot-tail-{session_id[:8]}",
        )
        self._tail_tasks[session_id] = task
        task.add_done_callback(self._make_tail_pop_callback(session_id))

    @staticmethod
    def _raw_terminal(raw: dict[str, Any]) -> tuple[bool, str | None]:
        typ = raw.get("type")
        if typ == "session.error":
            data = raw.get("data") or {}
            return True, str(data.get("message") or data.get("error") or "session error")
        return typ == "session.shutdown", None

    async def _create_job(self, session_id: str, cwd: str) -> Job | None:
        if not self._git:
            log.warning("session_state_watcher_no_git_service")
            return None
        repo_path = cwd
        try:
            repo_root = await self._git._run_git("rev-parse", "--show-toplevel", cwd=cwd)  # noqa: SLF001
            repo_path = repo_root.strip()
            git_common = await self._git._run_git(  # noqa: SLF001
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
                cwd=cwd,
            )
            git_common = git_common.strip()
            if git_common.endswith("/.git"):
                parent_repo = git_common[: -len("/.git")]
                if parent_repo != repo_path:
                    repo_path = parent_repo
        except Exception:
            repo_path = cwd
        try:
            branch = await self._git.get_current_branch(cwd=cwd)
        except Exception:
            branch = "unknown"
        try:
            base_ref = await self._git.rev_parse("HEAD", cwd=cwd)
        except Exception:
            base_ref = "HEAD"

        job_id = f"{repo_basename(repo_path)}-{hashlib.sha256(session_id.encode()).hexdigest()[:12]}"
        now = datetime.now(UTC)
        try:
            from backend.persistence.database import serialized_write
            from backend.persistence.project_repo import ProjectRepository

            async with serialized_write(self._session_factory) as session:
                project_id = await ProjectRepository(session).resolve_project_id_for_repo_path(repo_path)
        except Exception:
            log.debug("session_state_watcher_project_lookup_failed", repo=repo_path, exc_info=True)
            return None

        if project_id is None:
            # A Job can never exist without an owning Project (AD-5).
            log.warning("session_state_watcher_repo_not_project_owned", repo=repo_path)
            return None

        job = Job(
            id=job_id,
            repo=repo_path,
            project_id=project_id,
            prompt="",
            state=JobState.running,
            base_ref=base_ref,
            branch=branch,
            worktree_path=cwd,
            session_id=None,
            created_at=now,
            updated_at=now,
            sdk="copilot",
            source=JobSource.copilot_cli,
            external_session_id=session_id,
        )
        try:
            from backend.persistence.database import serialized_write
            from backend.persistence.job_repo import JobRepository
            from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

            async with serialized_write(self._session_factory) as session:
                await JobRepository(session).create(job)
                await TelemetrySummaryRepository(session).init_job(job_id, sdk="copilot", repo=repo_path, branch=branch)
        except Exception:
            log.warning("session_state_watcher_job_create_failed", session_id=session_id, exc_info=True)
            return None
        await self._publish_lifecycle(
            job_id,
            EventKind.job_created,
            {"repo": repo_path, "branch": branch, "base_ref": base_ref, "source": JobSource.copilot_cli, "prompt": ""},
        )
        await self._publish_lifecycle(
            job_id,
            EventKind.job_state_changed,
            {"state": JobState.running, "new_state": JobState.running},
        )
        return job
