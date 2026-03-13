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
    from backend.services.event_bus import EventBus
    from backend.services.execution_strategy import ExecutionStrategy
    from backend.services.job_service import JobService

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
        mcp_servers=mcp_servers,
        protected_paths=protected_paths,
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
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._adapter = adapter
        self._config = config
        self._approval_service = approval_service
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._strategies: dict[str, ExecutionStrategy] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_activity: dict[str, float] = {}
        self._session_ids: dict[str, str] = {}
        self._dequeue_lock = asyncio.Lock()
        self._shutting_down = False

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

    async def start_or_enqueue(self, job: Job) -> None:
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

            await self._start_job(job)

    async def _start_job(self, job: Job) -> None:
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

        task = asyncio.create_task(
            self._run_job(job.id, strategy, session_config),
            name=f"job-{job.id}",
        )
        self._tasks[job.id] = task
        log.info("job_started", job_id=job.id, strategy=strategy_name)

    async def _run_job(
        self,
        job_id: str,
        strategy: ExecutionStrategy,
        config: SessionConfig,
    ) -> None:
        """Execute a job strategy, translate events, and handle completion."""
        # Start heartbeat
        import time

        self._last_activity[job_id] = time.monotonic()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id),
            name=f"heartbeat-{job_id}",
        )
        self._heartbeat_tasks[job_id] = heartbeat_task

        session_id: str | None = None
        error_reason: str | None = None
        try:
            async for session_event in strategy.execute(config, self._adapter):
                self._last_activity[job_id] = time.monotonic()
                domain_event = self._translate_event(job_id, session_event)
                if domain_event is not None:
                    if session_id is None and domain_event.payload.get("session_id"):
                        session_id = domain_event.payload["session_id"]
                        self._session_ids[job_id] = session_id
                    if domain_event.kind == DomainEventKind.job_failed:
                        error_reason = domain_event.payload.get("message", "Agent error")

                    # Handle approval requests: persist, transition, wait, resume
                    if domain_event.kind == DomainEventKind.approval_requested and self._approval_service is not None:
                        approval = await self._approval_service.create_request(
                            job_id=job_id,
                            description=domain_event.payload.get("description", ""),
                            proposed_action=domain_event.payload.get("proposed_action"),
                        )
                        # Inject approval_id into the event payload
                        domain_event.payload["approval_id"] = approval.id

                        # Transition to waiting_for_approval
                        async with self._session_factory() as sess:
                            svc = self._make_job_service(sess)
                            await svc.transition_state(job_id, JobState.waiting_for_approval)
                            await sess.commit()

                        await self._event_bus.publish(domain_event)

                        # Wait for operator resolution
                        resolution = await self._approval_service.wait_for_resolution(approval.id)

                        # Publish approval_resolved event
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

                        # Transition back to running
                        async with self._session_factory() as sess:
                            svc = self._make_job_service(sess)
                            await svc.transition_state(job_id, JobState.running)
                            await sess.commit()
                        await self._publish_state_event(job_id, JobState.waiting_for_approval, JobState.running)
                        self._last_activity[job_id] = time.monotonic()

                        # If rejected, abort
                        if resolution == "rejected":
                            error_reason = "Approval rejected by operator"
                            break
                        continue

                    await self._event_bus.publish(domain_event)

            if error_reason:
                # An error event was received during execution
                await self._fail_job(job_id, error_reason)
                return

            # Strategy completed normally → succeeded
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.transition_state(job_id, JobState.succeeded)
                await session.commit()
            await self._event_bus.publish(
                DomainEvent(
                    event_id=_make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.job_succeeded,
                    payload={},
                )
            )
            log.info("job_succeeded", job_id=job_id)
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
            heartbeat_task.cancel()
            self._heartbeat_tasks.pop(job_id, None)
            self._tasks.pop(job_id, None)
            self._strategies.pop(job_id, None)
            self._last_activity.pop(job_id, None)
            self._session_ids.pop(job_id, None)
            if self._approval_service is not None:
                self._approval_service.cleanup_job(job_id)
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
        """Send a message to a running job's strategy. Returns True if sent."""
        strategy = self._strategies.get(job_id)
        if strategy is None:
            log.warning("send_message_no_strategy", job_id=job_id)
            return False
        await strategy.send_message(message)
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
            SessionEventKind.file_changed: DomainEventKind.diff_updated,
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
