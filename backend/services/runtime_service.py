"""Long-running job execution manager."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import (
    Job,
    JobState,
    MCPServerConfig,
    SessionConfig,
    SessionEvent,
    SessionEventKind,
)
from backend.models.events import DomainEvent, DomainEventKind
from backend.services.execution_strategy import STRATEGY_REGISTRY

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import TowerConfig
    from backend.services.agent_adapter import AgentAdapterInterface
    from backend.services.approval_service import ApprovalService
    from backend.services.diff_service import DiffService
    from backend.services.event_bus import EventBus
    from backend.services.execution_strategy import ExecutionStrategy
    from backend.services.job_service import JobService
    from backend.services.merge_service import MergeService
    from backend.services.summarization_service import SummarizationService

log = structlog.get_logger()

# Heartbeat configuration
_HEARTBEAT_INTERVAL_S = 30
_HEARTBEAT_WARNING_S = 90
_HEARTBEAT_TIMEOUT_S = 300  # 5 minutes


def _discover_mcp_servers(repo_path: str, config: TowerConfig) -> dict[str, MCPServerConfig]:
    """Discover MCP servers from .vscode/mcp.json and global config, respecting .tower.yml disabled list."""
    import json
    from pathlib import Path

    import yaml

    servers: dict[str, MCPServerConfig] = {}

    # 1. Global config: tools.mcp section
    global_config_path = Path.home() / ".tower" / "config.yaml"
    if global_config_path.exists():
        try:
            with open(global_config_path) as f:
                raw = yaml.safe_load(f) or {}
            tools_mcp = raw.get("tools", {}).get("mcp", {})
            if isinstance(tools_mcp, dict):
                for name, entry in tools_mcp.items():
                    if name == "disabled" or not isinstance(entry, dict):
                        continue
                    servers[name] = MCPServerConfig(
                        command=entry.get("command", ""),
                        args=entry.get("args", []),
                        env=entry.get("env"),
                    )
        except Exception:
            log.warning("mcp_global_config_read_failed", path=str(global_config_path))

    # 2. Repo-level: .vscode/mcp.json (takes precedence over global)
    mcp_json_path = Path(repo_path) / ".vscode" / "mcp.json"
    if mcp_json_path.exists():
        try:
            with open(mcp_json_path) as f:
                mcp_data = json.load(f)
            repo_servers = mcp_data.get("servers", {})
            if isinstance(repo_servers, dict):
                for name, entry in repo_servers.items():
                    if not isinstance(entry, dict):
                        continue
                    servers[name] = MCPServerConfig(
                        command=entry.get("command", ""),
                        args=entry.get("args", []),
                        env=entry.get("env"),
                    )
        except Exception:
            log.warning("mcp_repo_config_read_failed", path=str(mcp_json_path))

    # 3. Apply .tower.yml disabled list
    tower_yml_path = Path(repo_path) / ".tower.yml"
    if tower_yml_path.exists():
        try:
            with open(tower_yml_path) as f:
                tower_config = yaml.safe_load(f) or {}
            disabled = tower_config.get("tools", {}).get("mcp", {}).get("disabled", [])
            if isinstance(disabled, list):
                for name in disabled:
                    servers.pop(str(name), None)
        except Exception:
            log.warning("tower_yml_read_failed", path=str(tower_yml_path))

    return servers


def _resolve_protected_paths(repo_path: str) -> list[str]:
    """Read protected_paths from .tower.yml if present."""
    from pathlib import Path

    import yaml

    tower_yml = Path(repo_path) / ".tower.yml"
    if not tower_yml.exists():
        return []
    try:
        with open(tower_yml) as f:
            data = yaml.safe_load(f) or {}
        paths = data.get("protected_paths", [])
        return [str(p) for p in paths] if isinstance(paths, list) else []
    except Exception:
        return []


def _build_session_config(job: Job, config: TowerConfig) -> SessionConfig:
    """Build a SessionConfig from a Job record and resolved config."""
    workspace = job.worktree_path or job.repo
    mcp_servers = _discover_mcp_servers(job.repo, config)
    protected_paths = _resolve_protected_paths(job.repo)
    return SessionConfig(
        workspace_path=workspace,
        prompt=job.prompt,
        job_id=job.id,
        mcp_servers=mcp_servers,
        protected_paths=protected_paths,
        permission_mode=job.permission_mode or "auto",
    )


class RuntimeService:
    """Manages active job tasks, capacity enforcement, and queueing."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        adapter: AgentAdapterInterface,
        config: TowerConfig,
        approval_service: ApprovalService | None = None,
        diff_service: DiffService | None = None,
        merge_service: MergeService | None = None,
        summarization_service: SummarizationService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._adapter = adapter
        self._config = config
        self._approval_service = approval_service
        self._diff_service = diff_service
        self._merge_service = merge_service
        self._summarization_service = summarization_service
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._strategies: dict[str, ExecutionStrategy] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_activity: dict[str, float] = {}
        self._session_ids: dict[str, str] = {}
        self._dequeue_lock = asyncio.Lock()
        self._shutting_down = False
        # Contents to suppress when the SDK echoes them back (already published locally)
        self._echo_suppress: dict[str, set[str]] = {}

    def _make_job_service(self, session: AsyncSession) -> JobService:
        from backend.persistence.job_repo import JobRepository
        from backend.services.git_service import GitService
        from backend.services.job_service import JobService

        return JobService(
            job_repo=JobRepository(session),
            git_service=GitService(self._config),
            config=self._config,
        )

    @property
    def running_count(self) -> int:
        """Number of currently running job tasks."""
        return len(self._tasks)

    @property
    def max_concurrent(self) -> int:
        return self._config.runtime.max_concurrent_jobs

    async def start_or_enqueue(
        self, job: Job, override_prompt: str | None = None, resume_sdk_session_id: str | None = None
    ) -> None:
        """Start the job if capacity allows, otherwise keep it queued."""
        if self._shutting_down:
            log.warning("job_rejected_shutting_down", job_id=job.id)
            return
        async with self._dequeue_lock:
            if self.running_count >= self.max_concurrent:
                # Job is already queued from create_job; only transition if needed
                if job.state != JobState.queued:
                    async with self._session_factory() as session:
                        svc = self._make_job_service(session)
                        await svc.transition_state(job.id, JobState.queued)
                        await session.commit()
                    await self._publish_state_event(job.id, None, JobState.queued)
                log.info("job_enqueued", job_id=job.id, running=self.running_count)
                return

            await self._start_job(job, override_prompt=override_prompt, resume_sdk_session_id=resume_sdk_session_id)

    async def _start_job(
        self, job: Job, override_prompt: str | None = None, resume_sdk_session_id: str | None = None
    ) -> None:
        """Create an asyncio task to execute the job."""
        if job.id in self._tasks:
            return  # Already running (race-condition guard)

        from backend.models.api_schemas import StrategyKind

        strategy_name = job.strategy or "single_agent"
        try:
            strategy_kind = StrategyKind(strategy_name)
        except ValueError:
            strategy_kind = StrategyKind.single_agent

        strategy_cls = STRATEGY_REGISTRY.get(strategy_kind)
        if strategy_cls is None:
            log.error("unknown_strategy", strategy=strategy_name, job_id=job.id)
            await self._fail_job(job.id, f"Unknown strategy: {strategy_name}")
            return

        strategy = strategy_cls()
        self._strategies[job.id] = strategy

        # Ensure job is in running state
        if job.state != JobState.running:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.transition_state(job.id, JobState.running)
                await session.commit()
            await self._publish_state_event(job.id, job.state, JobState.running)

        session_config = _build_session_config(job, self._config)
        if override_prompt is not None:
            import dataclasses

            session_config = dataclasses.replace(session_config, prompt=override_prompt)
        if resume_sdk_session_id is not None:
            import dataclasses

            session_config = dataclasses.replace(session_config, resume_sdk_session_id=resume_sdk_session_id)

        # For supervised mode, inject the blocking approval callback so _on_permission
        # can directly block the agent and await the operator decision.
        if job.permission_mode == "supervised" and self._approval_service is not None:
            import dataclasses

            session_config = dataclasses.replace(
                session_config,
                blocking_permission_handler=self._make_blocking_handler(job.id),
            )

        task = asyncio.create_task(
            self._run_job(job.id, strategy, session_config),
            name=f"job-{job.id}",
        )
        self._tasks[job.id] = task
        # Pre-register prompt for echo suppression so the SDK user.message
        # echo of the initial prompt is discarded (shown via the synthetic entry).
        self._echo_suppress.setdefault(job.id, set()).add(session_config.prompt)
        log.info("job_started", job_id=job.id, strategy=strategy_name)

    def _make_blocking_handler(self, job_id: str) -> object:
        """Build an async callback for supervised-mode permission requests.

        The callback is injected into SessionConfig.blocking_permission_handler.
        It handles the full approval lifecycle: create record → publish SSE →
        transition state → await operator → transition back → return resolution.
        """
        import time as _time

        async def _handler(description: str, proposed_action: str | None) -> str:
            assert self._approval_service is not None
            approval = await self._approval_service.create_request(
                job_id=job_id,
                description=description,
                proposed_action=proposed_action,
            )
            approval_event = DomainEvent(
                event_id=_make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.approval_requested,
                payload={
                    "approval_id": approval.id,
                    "description": description,
                    "proposed_action": proposed_action,
                },
            )
            async with self._session_factory() as sess:
                svc = self._make_job_service(sess)
                await svc.transition_state(job_id, JobState.waiting_for_approval)
                await sess.commit()
            await self._event_bus.publish(approval_event)

            resolution = await self._approval_service.wait_for_resolution(approval.id)

            await self._event_bus.publish(
                DomainEvent(
                    event_id=_make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.approval_resolved,
                    payload={
                        "approval_id": approval.id,
                        "resolution": resolution,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
            )
            async with self._session_factory() as sess:
                svc = self._make_job_service(sess)
                await svc.transition_state(job_id, JobState.running)
                await sess.commit()
            await self._publish_state_event(job_id, JobState.waiting_for_approval, JobState.running)
            self._last_activity[job_id] = _time.monotonic()
            return resolution

        return _handler

    async def _run_job(
        self,
        job_id: str,
        strategy: ExecutionStrategy,
        config: SessionConfig,
    ) -> None:
        """Execute a job strategy, translate events, and handle completion."""
        import time

        self._last_activity[job_id] = time.monotonic()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id),
            name=f"heartbeat-{job_id}",
        )
        self._heartbeat_tasks[job_id] = heartbeat_task

        # Start telemetry tracking
        from backend.services.telemetry import collector as tel

        tel.start_job(job_id)

        # Resolve worktree_path and base_ref for diff calculations
        worktree_path: str | None = None
        base_ref: str | None = None
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                job = await svc.get_job(job_id)
            if job is not None:
                worktree_path = job.worktree_path or job.repo
                base_ref = job.base_ref
        except Exception:
            log.warning("diff_job_lookup_failed", job_id=job_id, exc_info=True)

        session_id: str | None = None
        error_reason: str | None = None
        try:
            async for session_event in strategy.execute(config, self._adapter):
                self._last_activity[job_id] = time.monotonic()

                # Intercept file_changed events and route through DiffService
                if (
                    session_event.kind == SessionEventKind.file_changed
                    and self._diff_service is not None
                    and worktree_path
                    and base_ref
                ):
                    await self._diff_service.handle_file_changed(job_id, worktree_path, base_ref)
                    continue

                # Capture SDK session_id on first event (strategy sets _session_id before first yield)
                if session_id is None and hasattr(strategy, "session_id") and strategy.session_id:
                    session_id = strategy.session_id
                    self._session_ids[job_id] = session_id
                    asyncio.create_task(
                        self._persist_sdk_session_id(job_id, session_id),
                        name=f"persist-session-{job_id}",
                    )

                domain_event = self._translate_event(job_id, session_event)
                if domain_event is not None:
                    if domain_event.kind == DomainEventKind.job_failed:
                        error_reason = domain_event.payload.get("message", "Agent error")

                    # Suppress SDK echoes for messages already published locally
                    # (operator messages and silent system instructions like pause).
                    if domain_event.kind == DomainEventKind.transcript_updated and job_id in self._echo_suppress:
                        content = domain_event.payload.get("content", "")
                        if content in self._echo_suppress[job_id]:
                            self._echo_suppress[job_id].discard(content)
                            continue

                    await self._event_bus.publish(domain_event)

            if error_reason:
                # An error event was received during execution
                await self._fail_job(job_id, error_reason)
                return

            # Final diff snapshot before merge/PR
            if self._diff_service is not None and worktree_path and base_ref:
                try:
                    await self._diff_service.finalize(job_id, worktree_path, base_ref)
                except Exception:
                    log.warning("diff_finalize_failed", job_id=job_id, exc_info=True)

            # Merge-back or PR creation
            merge_result = None
            pr_url: str | None = None
            if self._merge_service is not None and base_ref:
                try:
                    async with self._session_factory() as session:
                        svc = self._make_job_service(session)
                        full_job = await svc.get_job(job_id)
                    if full_job is not None:
                        merge_result = await self._merge_service.try_merge_back(
                            job_id=job_id,
                            repo_path=full_job.repo,
                            worktree_path=full_job.worktree_path,
                            branch=full_job.branch,
                            base_ref=full_job.base_ref,
                            prompt=full_job.prompt,
                        )
                        pr_url = merge_result.pr_url
                except Exception:
                    log.warning("merge_back_failed", job_id=job_id, exc_info=True)
            else:
                pr_url = await self._try_create_pr(job_id)

            # Strategy completed normally → succeeded
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.transition_state(job_id, JobState.succeeded)
                if pr_url:
                    from backend.persistence.job_repo import JobRepository

                    job_repo = JobRepository(session)
                    await job_repo.update_pr_url(job_id, pr_url)
                await session.commit()

            payload: dict[str, str] = {}
            if pr_url:
                payload["pr_url"] = pr_url
            if merge_result:
                payload["merge_status"] = merge_result.status

            await self._event_bus.publish(
                DomainEvent(
                    event_id=_make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.job_succeeded,
                    payload=payload,
                )
            )
            log.info(
                "job_succeeded",
                job_id=job_id,
                pr_url=pr_url,
                merge_status=merge_result.status if merge_result else None,
            )
        except asyncio.CancelledError:
            log.info("job_canceled_by_task", job_id=job_id)
            try:
                await strategy.abort()
            except Exception:
                log.warning("strategy_abort_failed", job_id=job_id, exc_info=True)
            try:
                async with self._session_factory() as session:
                    svc = self._make_job_service(session)
                    current = await svc.get_job(job_id)
                    if current and current.state != JobState.canceled:
                        await svc.transition_state(job_id, JobState.canceled)
                        await session.commit()
                        await self._event_bus.publish(
                            DomainEvent(
                                event_id=_make_event_id(),
                                job_id=job_id,
                                timestamp=datetime.now(UTC),
                                kind=DomainEventKind.job_canceled,
                                payload={"reason": "operator_cancel"},
                            )
                        )
                    else:
                        await session.commit()
            except Exception:
                log.warning("job_cancel_transition_failed", job_id=job_id, exc_info=True)
        except Exception:
            log.error("job_execution_failed", job_id=job_id, exc_info=True)
            await self._fail_job(job_id, "Execution error")
        finally:
            tel.end_job(job_id)
            heartbeat_task.cancel()
            self._heartbeat_tasks.pop(job_id, None)
            self._tasks.pop(job_id, None)
            self._strategies.pop(job_id, None)
            self._last_activity.pop(job_id, None)
            self._session_ids.pop(job_id, None)
            self._echo_suppress.pop(job_id, None)
            if self._approval_service is not None:
                self._approval_service.cleanup_job(job_id)
            if self._diff_service is not None:
                self._diff_service.cleanup(job_id)
            # Fire background summarization for completed/failed sessions
            if self._summarization_service is not None:
                asyncio.create_task(
                    self._summarize_session_background(job_id),
                    name=f"summarize-{job_id}",
                )
            # Check if any queued jobs can now start
            await self._dequeue_next()

    async def _heartbeat_loop(self, job_id: str) -> None:
        """Emit periodic heartbeats; timeout based on time since last activity."""
        import time

        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)

                last = self._last_activity.get(job_id)
                if last is None:
                    return
                since_last = time.monotonic() - last

                if since_last >= _HEARTBEAT_TIMEOUT_S:
                    log.warning("job_heartbeat_timeout", job_id=job_id, idle_s=since_last)
                    await self._fail_job(job_id, "heartbeat_timeout")
                    task = self._tasks.get(job_id)
                    if task:
                        task.cancel()
                    return

                if since_last >= _HEARTBEAT_WARNING_S:
                    log.warning("job_heartbeat_warning", job_id=job_id, idle_s=since_last)

                session_id = self._session_ids.get(job_id, "")
                await self._event_bus.publish(
                    DomainEvent(
                        event_id=_make_event_id(),
                        job_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=DomainEventKind.session_heartbeat,
                        payload={
                            "job_id": job_id,
                            "session_id": session_id,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    )
                )
        except asyncio.CancelledError:
            pass

    async def cancel(self, job_id: str) -> None:
        """Cancel a running job by cancelling its asyncio task.

        State transitions for non-running jobs (e.g. queued) are handled
        by the service layer (JobService.cancel_job). This method only
        interacts with in-memory runtime tasks.
        """
        task = self._tasks.get(job_id)
        if task is not None:
            task.cancel()
            log.info("job_cancel_requested", job_id=job_id)
        else:
            log.info("job_cancel_no_running_task", job_id=job_id)

    async def send_message(self, job_id: str, message: str) -> bool:
        """Send an operator message to a running job.

        Publishes the transcript event locally for immediate UI feedback and
        suppresses the SDK echo to avoid showing the message twice.
        """
        strategy = self._strategies.get(job_id)
        if strategy is None:
            log.warning("send_message_no_strategy", job_id=job_id)
            return False
        now = datetime.now(UTC)
        await strategy.send_message(message)
        # Publish immediately so the operator message appears in the transcript
        # without waiting for the SDK to echo it back.
        await self._event_bus.publish(
            DomainEvent(
                event_id=_make_event_id(),
                job_id=job_id,
                timestamp=now,
                kind=DomainEventKind.transcript_updated,
                payload={
                    "job_id": job_id,
                    "seq": 0,
                    "timestamp": now.isoformat(),
                    "role": "operator",
                    "content": message,
                },
            )
        )
        # Suppress the SDK echo so the same content is not published twice.
        self._echo_suppress.setdefault(job_id, set()).add(message)
        return True

    async def pause_job(self, job_id: str) -> bool:
        """Send a silent pause instruction to the agent. Returns True if sent.

        The pause message is never shown in the transcript — the agent receives
        the instruction to stop and wait for further operator input.
        """
        _pause_msg = (
            "Please stop what you are doing right now and wait. "
            "Do not take any further actions until the operator sends a follow-up message."
        )
        strategy = self._strategies.get(job_id)
        if strategy is None:
            log.warning("pause_job_no_strategy", job_id=job_id)
            return False
        # Pre-register the echo suppression before sending so the SDK echo
        # (if any) is discarded and never appears in the transcript.
        self._echo_suppress.setdefault(job_id, set()).add(_pause_msg)
        await strategy.send_message(_pause_msg)
        log.info("job_pause_requested", job_id=job_id)
        return True

    async def _dequeue_next(self) -> None:
        """Start the next queued job if capacity allows."""
        async with self._dequeue_lock:
            if self.running_count >= self.max_concurrent:
                return
            try:
                async with self._session_factory() as session:
                    svc = self._make_job_service(session)
                    queued_jobs = await svc.list_jobs(state=JobState.queued, limit=1)
                    jobs, _, _ = queued_jobs
                if jobs:
                    await self._start_job(jobs[0])
            except Exception:
                log.error("dequeue_failed", exc_info=True)

    async def _fail_job(self, job_id: str, reason: str) -> None:
        """Transition a job to failed state and publish the event."""
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.get_job(job_id)
                await svc.transition_state(job_id, JobState.failed)
                await session.commit()
            await self._event_bus.publish(
                DomainEvent(
                    event_id=_make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.job_failed,
                    payload={"reason": reason},
                )
            )
        except Exception:
            log.error("fail_job_transition_failed", job_id=job_id, exc_info=True)

    async def _summarize_session_background(self, job_id: str) -> None:
        """Fire-and-forget background task: summarize completed/failed session."""
        if self._summarization_service is None:
            return
        try:
            async with self._session_factory() as session:
                from backend.persistence.job_repo import JobRepository

                job_repo = JobRepository(session)
                job = await job_repo.get(job_id)
            if job is None:
                return
            # Skip if already summarized (e.g. rapid retry)
            from backend.persistence.artifact_repo import ArtifactRepository
            from backend.services.artifact_service import ArtifactService

            async with self._session_factory() as session:
                artifact_svc = ArtifactService(ArtifactRepository(session))
                existing = await artifact_svc.get_latest_session_summary(job_id)
                if existing is not None:
                    return
            await self._summarization_service.summarize_and_store(job_id, job.session_count, job.prompt)
        except Exception:
            log.warning("summarize_session_background_failed", job_id=job_id, exc_info=True)

    async def _persist_sdk_session_id(self, job_id: str, sdk_session_id: str) -> None:
        """Persist the Copilot SDK session ID so resume_job() can reconnect to it later."""
        try:
            async with self._session_factory() as session:
                from backend.persistence.job_repo import JobRepository

                job_repo = JobRepository(session)
                await job_repo.update_sdk_session_id(job_id, sdk_session_id)
                await session.commit()
        except Exception:
            log.warning("persist_sdk_session_id_failed", job_id=job_id, exc_info=True)

    async def resume_job(self, job_id: str, instruction: str) -> Job:
        """Resume a terminal job in-place.

        Primary path: reconnect to the existing Copilot SDK session (full conversation history
        intact, no summarization cost). Fallback: use LLM-generated session summary when the
        SDK session is no longer available (daemon restart, session expired, etc.).
        """
        from pathlib import Path

        from backend.models.domain import TERMINAL_STATES
        from backend.persistence.job_repo import JobRepository
        from backend.services.job_service import JobNotFoundError, StateConflictError

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)
            if job is None:
                raise JobNotFoundError(f"Job {job_id} does not exist.")
            if job.state not in TERMINAL_STATES:
                raise StateConflictError(f"Job {job_id} is not in a terminal state (current: {job.state}).")

            # Ensure worktree still exists; re-create from branch if missing
            if job.worktree_path and job.worktree_path != job.repo:
                wt = Path(job.worktree_path)
                if not wt.exists() and job.branch:
                    from backend.services.git_service import GitService

                    git = GitService(self._config)
                    try:
                        new_wt = await git.reattach_worktree(job.repo, job.id, job.branch)
                        await job_repo.update_worktree_path(job_id, new_wt)
                        job.worktree_path = new_wt
                        log.info("worktree_reattached", job_id=job_id, path=new_wt)
                    except Exception:
                        log.warning("worktree_reattach_failed", job_id=job_id, exc_info=True)

            new_session_count = job.session_count + 1

            if job.sdk_session_id:
                # Primary path: SDK native session resume — full history intact, no summarization cost.
                log.info("resume_via_sdk_session", job_id=job_id, sdk_session_id=job.sdk_session_id)
                override_prompt = instruction
                resume_sdk_session_id: str | None = job.sdk_session_id
            else:
                # Fallback path: summarization-based context injection.
                log.info("resume_via_summarization", job_id=job_id)
                from backend.persistence.artifact_repo import ArtifactRepository
                from backend.persistence.event_repo import EventRepository
                from backend.services.artifact_service import ArtifactService
                from backend.services.summarization_service import _build_resume_prompt, _extract_changed_files

                artifact_repo = ArtifactRepository(session)
                artifact_svc = ArtifactService(artifact_repo)
                summary_artifact = await artifact_svc.get_latest_session_summary(job_id)

                event_repo = EventRepository(session)
                diff_events = await event_repo.list_by_job(job_id, kinds=[DomainEventKind.diff_updated])
                changed_files = _extract_changed_files(diff_events)

                if summary_artifact is None and self._summarization_service is not None:
                    try:
                        await self._summarization_service.summarize_and_store(job_id, job.session_count, job.prompt)
                        summary_artifact = await artifact_svc.get_latest_session_summary(job_id)
                    except Exception:
                        log.warning("inline_summarization_failed", job_id=job_id, exc_info=True)

                summary_text: str | None = None
                if summary_artifact is not None:
                    try:
                        summary_text = Path(summary_artifact.disk_path).read_text(encoding="utf-8")
                    except Exception:
                        log.warning("summary_read_failed", job_id=job_id, exc_info=True)

                override_prompt = _build_resume_prompt(
                    summary_text, changed_files, instruction, new_session_count, job_id, job.prompt
                )
                resume_sdk_session_id = None

            await job_repo.reset_for_resume(job_id, new_session_count)
            await session.commit()

        # Publish session_resumed event
        now = datetime.now(UTC)
        await self._event_bus.publish(
            DomainEvent(
                event_id=_make_event_id(),
                job_id=job_id,
                timestamp=now,
                kind=DomainEventKind.session_resumed,
                payload={
                    "session_number": new_session_count,
                    "instruction": instruction,
                    "timestamp": now.isoformat(),
                },
            )
        )

        # Reload job and start execution
        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found after resume reset")
        await self.start_or_enqueue(job, override_prompt=override_prompt, resume_sdk_session_id=resume_sdk_session_id)

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            reloaded = await job_repo.get(job_id)
        if reloaded is None:
            raise ValueError(f"Job {job_id} not found after start")
        return reloaded

    async def _cleanup_job_worktree(self, job: Job) -> None:
        """Remove the secondary worktree for a finished job (failed/canceled).

        The main worktree (where worktree_path == repo) is never removed.
        """
        import contextlib

        worktree_path = job.worktree_path
        if not worktree_path or worktree_path == job.repo:
            return  # main worktree — leave it alone
        from backend.services.git_service import GitService

        git = GitService(self._config)
        with contextlib.suppress(Exception):
            await git.remove_worktree(job.repo, worktree_path)
            log.info("worktree_cleaned_up", job_id=job.id, worktree=worktree_path)

    async def _try_create_pr(self, job_id: str) -> str | None:
        """Best-effort PR creation via ``gh pr create``. Returns the PR URL or None."""
        import re
        import shutil
        import subprocess  # noqa: S404

        if shutil.which("gh") is None:
            log.info("pr_creation_skipped_no_gh", job_id=job_id)
            return None

        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            job = await svc.get_job(job_id)

        if job is None or not job.worktree_path or not job.branch:
            log.info("pr_creation_skipped_no_worktree", job_id=job_id)
            return None

        # Validate branch and base_ref to prevent argument injection
        _ref_pattern = re.compile(r"^[a-zA-Z0-9/_.-]+$")
        if not _ref_pattern.match(job.branch):
            log.warning("pr_creation_invalid_branch", job_id=job_id)
            return None
        if not _ref_pattern.match(job.base_ref):
            log.warning("pr_creation_invalid_base_ref", job_id=job_id)
            return None

        try:
            result = await asyncio.to_thread(
                subprocess.run,  # noqa: S603
                [
                    "gh",
                    "pr",
                    "create",
                    "--title",
                    f"[Tower] {job.prompt[:80]}",
                    "--body",
                    f"Automated PR created by Tower for job `{job_id}`.",
                    "--head",
                    job.branch,
                    "--base",
                    job.base_ref,
                    "--",
                ],
                capture_output=True,
                text=True,
                cwd=job.worktree_path,
                timeout=60,
            )
            if result.returncode == 0:
                pr_url = result.stdout.strip()
                log.info("pr_created", job_id=job_id, pr_url=pr_url)
                return pr_url
            log.warning(
                "pr_creation_failed",
                job_id=job_id,
                returncode=result.returncode,
                stderr=result.stderr[:500],
            )
        except Exception:
            log.warning("pr_creation_error", job_id=job_id, exc_info=True)
        return None

    async def _publish_state_event(self, job_id: str, previous_state: str | None, new_state: str) -> None:
        """Publish a job state change event."""
        await self._event_bus.publish(
            DomainEvent(
                event_id=_make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.job_state_changed,
                payload={
                    "previous_state": previous_state,
                    "new_state": new_state,
                },
            )
        )

    def _translate_event(self, job_id: str, event: SessionEvent) -> DomainEvent | None:
        """Translate a SessionEvent into a DomainEvent."""
        mapping: dict[SessionEventKind, DomainEventKind] = {
            SessionEventKind.log: DomainEventKind.log_line_emitted,
            SessionEventKind.transcript: DomainEventKind.transcript_updated,
            SessionEventKind.approval_request: DomainEventKind.approval_requested,
            SessionEventKind.error: DomainEventKind.job_failed,
        }
        kind = mapping.get(event.kind)
        if kind is None:
            # 'done' events are handled at the _run_job level
            return None
        return DomainEvent(
            event_id=_make_event_id(),
            job_id=job_id,
            timestamp=datetime.now(UTC),
            kind=kind,
            payload=event.payload,
        )

    async def recover_on_startup(self) -> None:
        """Recover from a previous crash: fail orphaned running jobs, re-enqueue queued ones."""
        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            # Fail jobs that were 'running' or 'waiting_for_approval' — we can't reconnect
            for state in (JobState.running, JobState.waiting_for_approval):
                jobs, _, _ = await svc.list_jobs(state=state, limit=10000)
                for job in jobs:
                    log.warning("recovering_orphaned_job", job_id=job.id, state=state)
                    await svc.transition_state(job.id, JobState.failed)
                    await self._event_bus.publish(
                        DomainEvent(
                            event_id=_make_event_id(),
                            job_id=job.id,
                            timestamp=datetime.now(UTC),
                            kind=DomainEventKind.job_failed,
                            payload={"reason": "process_restarted"},
                        )
                    )

            # Re-enqueue queued jobs
            queued_jobs, _, _ = await svc.list_jobs(state=JobState.queued, limit=10000)
            await session.commit()

        for job in queued_jobs:
            await self.start_or_enqueue(job)

    async def shutdown(self) -> None:
        """Gracefully shut down all running jobs."""
        self._shutting_down = True
        for job_id in list(self._tasks):
            # Cancel with server_shutdown reason
            task = self._tasks.get(job_id)
            if task is not None:
                task.cancel()
                try:
                    async with self._session_factory() as session:
                        svc = self._make_job_service(session)
                        current = await svc.get_job(job_id)
                        if current and current.state not in (
                            JobState.canceled,
                            JobState.succeeded,
                            JobState.failed,
                        ):
                            await svc.transition_state(job_id, JobState.canceled)
                            await session.commit()
                            await self._event_bus.publish(
                                DomainEvent(
                                    event_id=_make_event_id(),
                                    job_id=job_id,
                                    timestamp=datetime.now(UTC),
                                    kind=DomainEventKind.job_canceled,
                                    payload={"reason": "server_shutdown"},
                                )
                            )
                except Exception:
                    log.warning("shutdown_cancel_failed", job_id=job_id, exc_info=True)
        # Wait briefly for tasks to complete
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down


def _make_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"
