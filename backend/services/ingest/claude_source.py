"""TraceForge-backed Claude CLI imported session source."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.domain import Job, JobSource, JobState
from backend.models.events import EventKind
from backend.services.ingest._base import TraceForgeIngestBase, repo_basename

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CPLConfig
    from backend.services.analytics.model_pricing import ModelPricingService
    from backend.services.events.event_processor import EventProcessor
    from backend.services.git.git_service import GitService
    from backend.services.runtime import RuntimeService

log = structlog.get_logger()

_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_DISCOVERY_POLL_S = 2.0
_STALE_FILE_SECONDS = 6.0
_SESSION_ID_PATTERN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_SESSION_FILE_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")


def _encode_cwd(path: str) -> str:
    """Encode a cwd path to Claude's project directory form."""
    return path.replace("/", "-")


def _is_pid_alive(pid: int) -> bool:
    """Cross-platform check that a PID still belongs to a Claude process."""
    try:
        import psutil

        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        text = " ".join([proc.name(), *proc.cmdline()]).lower()
        return "claude" in text
    except Exception:
        if os.name == "nt":
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _find_claude_pids_at_cwd(repo_path: str, exclude_pids: frozenset[int] = frozenset()) -> list[int]:
    """Find Claude processes whose cwd matches repo_path using psutil when available."""
    try:
        import psutil
    except Exception:
        return []
    results: list[int] = []
    try:
        repo_resolved = Path(repo_path).resolve()
    except OSError:
        return []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            pid = int(proc.info["pid"])
            if pid in exclude_pids:
                continue
            cmd = " ".join([str(proc.info.get("name") or ""), *(proc.info.get("cmdline") or [])]).lower()
            if "claude" not in cmd:
                continue
            cwd = proc.info.get("cwd")
            if cwd and Path(cwd).resolve() == repo_resolved:
                results.append(pid)
        except (OSError, psutil.Error, TypeError, ValueError):
            continue
    return results


def _is_claude_process_alive(session_id: str, repo_path: str | None = None) -> bool:
    """Return whether a Claude process appears alive, optionally scoped by cwd."""
    try:
        import psutil
    except Exception:
        return False
    if repo_path:
        return bool(_find_claude_pids_at_cwd(repo_path))
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            text = " ".join([str(proc.info.get("name") or ""), *(proc.info.get("cmdline") or [])]).lower()
            if "claude" in text:
                return True
        except (psutil.Error, TypeError):
            continue
    return False


class ClaudeSessionStateWatcher(TraceForgeIngestBase):
    """Discovers and ingests Claude CLI sessions from local JSONL files."""

    _watcher_log_prefix = "claude_watcher"

    def __init__(
        self,
        event_processor: EventProcessor,
        runtime_service: RuntimeService,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
        git_service: GitService | None = None,
        model_pricing: ModelPricingService | None = None,
    ) -> None:
        super().__init__(event_processor, runtime_service, session_factory, "claude")
        self._config = config
        self._git = git_service
        self._model_pricing = model_pricing
        self._job_to_session: dict[str, str] = {}
        self._pending_messages: dict[str, list[str]] = {}
        self._finalizing: set[str] = set()
        self._session_pids: dict[str, int] = {}
        self._started_at: float = 0.0

    async def start(self) -> None:
        self._install_stop_hook()
        if not _CLAUDE_PROJECTS_DIR.exists():
            log.info("claude_watcher_no_projects_dir", path=str(_CLAUDE_PROJECTS_DIR))
            return
        self._running = True
        self._started_at = datetime.now(UTC).timestamp()
        await self._load_existing_sessions()
        self._discovery_task = asyncio.create_task(self._discovery_loop(), name="claude-session-discovery")

    async def stop(self) -> None:
        await self._stop_common()
        log.info("claude_watcher_stopped")

    def get_pending_messages(self, session_id: str) -> list[str]:
        job_id = self._session_to_job.get(session_id)
        if not job_id:
            return []
        return self._pending_messages.pop(job_id, [])

    async def send_operator_message(self, job_id: str, message: str) -> None:
        self._pending_messages.setdefault(job_id, []).append(message)
        log.info("claude_operator_message_queued", job_id=job_id)

    async def abort_session(self, job_id: str) -> None:
        self._pending_messages.setdefault(job_id, []).append(
            "OPERATOR: Session abort requested. Please stop immediately."
        )
        log.info("claude_abort_queued", job_id=job_id)

    def _get_hook_url(self) -> str:
        host = self._config.server.host if hasattr(self._config, "server") else "127.0.0.1"
        port = self._config.server.port if hasattr(self._config, "server") else 8080
        return f"http://{host}:{port}/api/hooks/claude"

    def _install_stop_hook(self) -> None:
        import tempfile

        hook_url = self._get_hook_url()
        settings_dir = _CLAUDE_SETTINGS_PATH.parent
        settings_dir.mkdir(parents=True, exist_ok=True)
        try:
            settings = json.loads(_CLAUDE_SETTINGS_PATH.read_text()) if _CLAUDE_SETTINGS_PATH.exists() else {}
            hooks = settings.setdefault("hooks", {})
            stop_hooks = hooks.setdefault("Stop", [])
            if hook_url in stop_hooks:
                return
            stop_hooks.append(hook_url)
            fd, tmp_path = tempfile.mkstemp(dir=str(settings_dir), suffix=".tmp", prefix=".settings-")
            try:
                with open(fd, "w") as file:
                    json.dump(settings, file, indent=2)
                    file.write("\n")
                Path(tmp_path).replace(_CLAUDE_SETTINGS_PATH)
            except BaseException:
                Path(tmp_path).unlink(missing_ok=True)
                raise
        except Exception:
            log.warning("claude_watcher_hook_install_failed", exc_info=True)

    async def _load_existing_sessions(self) -> None:
        running_jobs: list[tuple[str, str, int, str | None, str | None, str | None]] = []
        try:
            async with self._session_factory() as session:
                from sqlalchemy import text

                result = await session.execute(
                    text(
                        "SELECT id, external_session_id, state, tail_offset, worktree_path, base_ref, repo "
                        "FROM jobs WHERE external_session_id IS NOT NULL AND source = 'claude_cli'"
                    )
                )
                for row in result:
                    job_id, ext_sid, state, count, wt, base, repo = row
                    self._tracked_sessions.add(ext_sid)
                    if state == "running":
                        running_jobs.append((job_id, ext_sid, count or 0, wt, base, repo))
            for job_id, ext_sid, count, wt, base, repo in running_jobs:
                repo_for_liveness = wt or repo or None
                alive = await asyncio.to_thread(_is_claude_process_alive, ext_sid, repo_for_liveness)
                if not alive:
                    await self._finalize_session(job_id, error_reason="session ended while CodePlane was offline")
                    continue
                self._session_to_job[ext_sid] = job_id
                self._job_to_session[job_id] = ext_sid
                self._job_worktrees[job_id] = wt or repo or ""
                self._job_base_refs[job_id] = base or "HEAD"
                await self._runtime.register_external_session(
                    job_id,
                    self._job_worktrees[job_id],
                    self._job_base_refs[job_id],
                )
                jsonl_path = self._find_session_file(ext_sid)
                if jsonl_path:
                    self._start_tail(ext_sid, job_id, jsonl_path, initial_skip_count=count)
        except Exception:
            log.warning("claude_watcher_load_existing_failed", exc_info=True)

    def _find_session_file(self, session_id: str) -> Path | None:
        if not _CLAUDE_PROJECTS_DIR.exists():
            return None
        for project_dir in _CLAUDE_PROJECTS_DIR.iterdir():
            if project_dir.is_dir():
                candidate = project_dir / f"{session_id}.jsonl"
                if candidate.exists():
                    return candidate
        return None

    async def _discovery_loop(self) -> None:
        while self._running:
            try:
                managed_paths = await self._managed_repo_paths()
                for session_id, jsonl_path, repo_path in await asyncio.to_thread(
                    self._scan_for_new_sessions, managed_paths
                ):
                    if session_id in self._tracked_sessions:
                        continue
                    self._tracked_sessions.add(session_id)
                    await self._attach_session(session_id, jsonl_path, repo_path)
            except asyncio.CancelledError:
                return
            except Exception:
                log.debug("claude_watcher_discovery_error", exc_info=True)
            await asyncio.sleep(_DISCOVERY_POLL_S)

    async def _managed_repo_paths(self) -> list[str]:
        from backend.services.project.repo_membership import (
            list_managed_repo_paths_from_factory,
        )

        return await list_managed_repo_paths_from_factory(self._config, self._session_factory)

    def _scan_for_new_sessions(self, managed_repo_paths: list[str] | None = None) -> list[tuple[str, Path, str]]:
        repo_paths = self._config.repos if managed_repo_paths is None else managed_repo_paths
        if not _CLAUDE_PROJECTS_DIR.exists() or not repo_paths:
            return []
        results: list[tuple[str, Path, str]] = []
        for repo_path in set(repo_paths):
            project_dir = _CLAUDE_PROJECTS_DIR / _encode_cwd(repo_path)
            if not project_dir.is_dir():
                continue
            try:
                for entry in project_dir.iterdir():
                    if not entry.is_file() or not _SESSION_FILE_RE.match(entry.name):
                        continue
                    session_id = entry.stem
                    if session_id in self._tracked_sessions or not all(c in _SESSION_ID_PATTERN for c in session_id):
                        continue
                    try:
                        if entry.stat().st_mtime < self._started_at:
                            continue
                    except OSError:
                        continue
                    results.append((session_id, entry, repo_path))
            except OSError:
                continue
        return results

    async def _attach_session(self, session_id: str, jsonl_path: Path, repo_path: str) -> None:
        job = await self._create_job(session_id, repo_path)
        if job is None:
            self._tracked_sessions.discard(session_id)
            return
        job_id = job.id
        self._session_to_job[session_id] = job_id
        self._job_to_session[job_id] = session_id
        self._job_worktrees[job_id] = job.worktree_path or repo_path
        self._job_base_refs[job_id] = job.base_ref or "HEAD"
        claimed = frozenset(self._session_pids.values())
        pids = await asyncio.to_thread(_find_claude_pids_at_cwd, repo_path, claimed)
        if pids:
            self._session_pids[session_id] = pids[0]
        await self._runtime.register_external_session(job_id, self._job_worktrees[job_id], self._job_base_refs[job_id])
        self._start_tail(session_id, job_id, jsonl_path)

    def _start_tail(self, session_id: str, job_id: str, jsonl_path: Path, *, initial_skip_count: int = 0) -> None:
        task = asyncio.create_task(
            self._tail_traceforge_events(
                session_id,
                job_id,
                jsonl_path,
                initial_skip_count=initial_skip_count,
                finalize_on_raw=self._raw_terminal,
            ),
            name=f"claude-tail-{session_id[:8]}",
        )
        self._tail_tasks[session_id] = task
        task.add_done_callback(self._make_tail_pop_callback(session_id))
        live = asyncio.create_task(
            self._claude_liveness_loop(session_id, job_id, jsonl_path),
            name=f"claude-live-{session_id[:8]}",
        )
        self._liveness_tasks[session_id] = live
        live.add_done_callback(self._make_liveness_pop_callback(session_id))

    @staticmethod
    def _raw_terminal(raw: dict[str, Any]) -> tuple[bool, str | None]:
        return raw.get("type") == "last-prompt", None

    async def _claude_liveness_loop(self, session_id: str, job_id: str, jsonl_path: Path) -> None:
        while self._running and session_id in self._session_to_job:
            await asyncio.sleep(_DISCOVERY_POLL_S)
            cached_pid = self._session_pids.get(session_id)
            if cached_pid is not None:
                alive = await asyncio.to_thread(_is_pid_alive, cached_pid)
            else:
                alive = await asyncio.to_thread(_is_claude_process_alive, session_id, self._job_worktrees.get(job_id))
            if alive:
                continue
            try:
                stale = (datetime.now(UTC).timestamp() - jsonl_path.stat().st_mtime) >= _STALE_FILE_SECONDS
            except OSError:
                stale = True
            if stale:
                await self._finalize_session(job_id, error_reason="session ended without clean shutdown")
                task = self._tail_tasks.get(session_id)
                if task:
                    task.cancel()
                return

    async def _create_job(self, session_id: str, repo_path: str) -> Job | None:
        if not self._git:
            log.warning("claude_watcher_no_git_service")
            return None
        try:
            branch = await self._git.get_current_branch(cwd=repo_path)
        except Exception:
            branch = "unknown"
        try:
            base_ref = await self._git.rev_parse("HEAD", cwd=repo_path)
        except Exception:
            base_ref = "HEAD"
        job_id = f"{repo_basename(repo_path)}-{hashlib.sha256(session_id.encode()).hexdigest()[:12]}"
        now = datetime.now(UTC)
        try:
            from backend.persistence.database import serialized_write
            from backend.persistence.job_repo import JobRepository
            from backend.persistence.project_repo import ProjectRepository

            async with serialized_write(self._session_factory) as session:
                repo = JobRepository(session)
                existing = await repo.get(job_id)
                if existing is not None:
                    self._finalizing.discard(job_id)
                    self._finalized_jobs.discard(job_id)
                    await repo.update_state(job_id, JobState.running, updated_at=now)
                    await self._publish_lifecycle(
                        job_id,
                        EventKind.job_state_changed,
                        {"state": JobState.running, "new_state": JobState.running},
                    )
                    return existing
                project_id = await ProjectRepository(session).resolve_project_id_for_repo_path(repo_path)
        except Exception:
            log.debug("claude_watcher_job_check_failed", job_id=job_id, exc_info=True)
            return None

        if project_id is None:
            # A Job can never exist without an owning Project (AD-5) — a repo
            # discovered by the watcher that no longer maps to exactly one
            # Project is not job-eligible, even if it was previously managed.
            log.warning("claude_watcher_repo_not_project_owned", repo=repo_path)
            return None

        job = Job(
            id=job_id,
            repo=repo_path,
            project_id=project_id,
            prompt="",
            state=JobState.running,
            base_ref=base_ref,
            branch=branch,
            worktree_path=repo_path,
            session_id=None,
            created_at=now,
            updated_at=now,
            sdk="claude",
            source=JobSource.claude_cli,
            external_session_id=session_id,
        )
        try:
            from backend.persistence.database import serialized_write
            from backend.persistence.job_repo import JobRepository
            from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

            async with serialized_write(self._session_factory) as session:
                await JobRepository(session).create(job)
                await TelemetrySummaryRepository(session).init_job(job_id, sdk="claude", repo=repo_path, branch=branch)
        except Exception:
            log.warning("claude_watcher_job_create_failed", session_id=session_id, exc_info=True)
            return None
        await self._publish_lifecycle(
            job_id,
            EventKind.job_created,
            {"repo": repo_path, "branch": branch, "base_ref": base_ref, "source": JobSource.claude_cli, "prompt": ""},
        )
        await self._publish_lifecycle(
            job_id,
            EventKind.job_state_changed,
            {"state": JobState.running, "new_state": JobState.running},
        )
        return job

    async def _finalize_session(self, job_id: str, *, error_reason: str | None = None) -> None:
        if job_id in self._finalizing:
            return
        self._finalizing.add(job_id)
        await super()._finalize_session(job_id, error_reason=error_reason)

    def _cleanup_job(self, job_id: str) -> None:
        super()._cleanup_job(job_id)
        self._pending_messages.pop(job_id, None)
        sid_to_remove = self._job_to_session.pop(job_id, None)
        if sid_to_remove:
            self._session_to_job.pop(sid_to_remove, None)
            self._tracked_sessions.discard(sid_to_remove)
            self._tail_tasks.pop(sid_to_remove, None)
            self._liveness_tasks.pop(sid_to_remove, None)
            self._session_pids.pop(sid_to_remove, None)
