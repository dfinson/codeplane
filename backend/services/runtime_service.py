"""Long-running job execution manager."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict

import structlog

from backend.models.domain import (
    Job,
    JobState,
    MCPServerConfig,
    PermissionMode,
    SessionConfig,
    SessionEvent,
    SessionEventKind,
)
from backend.models.events import DomainEvent, DomainEventKind
from backend.services.execution_strategy import STRATEGY_REGISTRY


class _ToolCallEntry(TypedDict):
    tool_name: str
    tool_args: str
    tool_intent: str
    tool_title: str


class _TurnToolBuffer(TypedDict):
    turn_id: str
    tool_calls: list[_ToolCallEntry]


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
    from backend.services.platform_adapter import PlatformRegistry
    from backend.services.summarization_service import SummarizationService
    from backend.services.utility_session import UtilitySessionService

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


def _resolve_permission_mode(repo_path: str) -> str | None:
    """Read permission_mode from .tower.yml if present (per-repo override)."""
    from pathlib import Path

    import yaml

    tower_yml = Path(repo_path) / ".tower.yml"
    if not tower_yml.exists():
        return None
    try:
        with open(tower_yml) as f:
            data = yaml.safe_load(f) or {}
        mode = data.get("permission_mode")
        if mode and str(mode) in ("permissive", "auto", "supervised", "readonly"):
            return str(mode)
        return None
    except Exception:
        return None


def _build_session_config(
    job: Job,
    config: TowerConfig,
    permission_mode_override: str | None = None,
) -> SessionConfig:
    """Build a SessionConfig from a Job record and resolved config.

    Permission mode priority: per-job override > .tower.yml > global config.
    """
    workspace = job.worktree_path or job.repo
    mcp_servers = _discover_mcp_servers(job.repo, config)
    protected_paths = _resolve_protected_paths(job.repo)

    # Resolve permission_mode with priority chain
    if permission_mode_override:
        mode_str = permission_mode_override
    else:
        repo_mode = _resolve_permission_mode(job.repo)
        mode_str = repo_mode or config.runtime.permission_mode

    try:
        mode = PermissionMode(mode_str)
    except ValueError:
        mode = PermissionMode.auto

    return SessionConfig(
        workspace_path=workspace,
        prompt=job.prompt,
        job_id=job.id,
        model=job.model,
        mcp_servers=mcp_servers,
        protected_paths=protected_paths,
        permission_mode=mode,
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
        platform_registry: PlatformRegistry | None = None,
        utility_session: UtilitySessionService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._adapter = adapter
        self._config = config
        self._approval_service = approval_service
        self._diff_service = diff_service
        self._merge_service = merge_service
        self._summarization_service = summarization_service
        self._platform_registry = platform_registry
        self._utility_session = utility_session
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._strategies: dict[str, ExecutionStrategy] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task[None]] = {}
        self._headline_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_activity: dict[str, float] = {}
        self._session_ids: dict[str, str] = {}
        self._permission_overrides: dict[str, str] = {}  # job_id → permission_mode
        self._dequeue_lock = asyncio.Lock()
        self._shutting_down = False
        # Transcript buffer for headline generation (last N agent turns per job)
        self._headline_transcript: dict[str, list[str]] = {}
        # Last snapshot used for headline generation (fallback when buffer is empty)
        self._headline_last_snapshot: dict[str, list[str]] = {}
        # Last generated headline per job (avoid exact repeats)
        self._headline_last_text: dict[str, str] = {}
        # Contents to suppress when the SDK echoes them back (already published locally)
        self._echo_suppress: dict[str, set[str]] = {}
        # Per-job state for detecting agent turn boundaries and triggering AI tool summaries
        self._turn_tool_buffer: dict[str, _TurnToolBuffer] = {}

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
        self,
        job: Job,
        override_prompt: str | None = None,
        resume_sdk_session_id: str | None = None,
        permission_mode: str | None = None,
    ) -> None:
        """Start the job if capacity allows, otherwise keep it queued."""
        if permission_mode:
            self._permission_overrides[job.id] = permission_mode
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

        session_config = _build_session_config(
            job,
            self._config,
            self._permission_overrides.pop(job.id, None),
        )
        if override_prompt is not None:
            import dataclasses

            session_config = dataclasses.replace(session_config, prompt=override_prompt)
        if resume_sdk_session_id is not None:
            import dataclasses

            session_config = dataclasses.replace(session_config, resume_sdk_session_id=resume_sdk_session_id)

        task = asyncio.create_task(
            self._run_job(job.id, strategy, session_config),
            name=f"job-{job.id}",
        )
        self._tasks[job.id] = task
        # Pre-register prompt for echo suppression so the SDK user.message
        # echo of the initial prompt is discarded (shown via the synthetic entry).
        self._echo_suppress.setdefault(job.id, set()).add(session_config.prompt)
        log.info("job_started", job_id=job.id, strategy=strategy_name)

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

        # Start progress headline generation (periodically summarises what the agent is doing)
        if self._utility_session is not None:
            self._headline_transcript[job_id] = []
            self._headline_last_snapshot[job_id] = []
            self._headline_last_text[job_id] = ""
            headline_task = asyncio.create_task(
                self._headline_loop(job_id),
                name=f"headline-{job_id}",
            )
            self._headline_tasks[job_id] = headline_task
            # Proactively scale the utility pool to match running jobs
            await self._utility_session.notify_job_started()

        # Start telemetry tracking
        from backend.services.telemetry import collector as tel

        tel.start_job(job_id, model=config.model or "")

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

                # Trigger diff recalculation after tool completions.
                # The SDK may not fire session.workspace_file_changed, so we
                # piggyback on tool.execution_complete from the transcript stream.
                # DiffService throttles to 5-second windows, so this is cheap.
                if (
                    session_event.kind == SessionEventKind.transcript
                    and session_event.payload.get("role") == "tool_call"
                    and self._diff_service is not None
                    and worktree_path
                    and base_ref
                ):
                    await self._diff_service.handle_file_changed(job_id, worktree_path, base_ref)

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

                    # Handle approval requests: the adapter now blocks the
                    # SDK directly.  RuntimeService just transitions state
                    # and publishes the SSE event so the frontend can render
                    # the approval banner.  The adapter's _on_permission
                    # callback already created the Approval record and
                    # injected approval_id into the payload.
                    if domain_event.kind == DomainEventKind.approval_requested and self._approval_service is not None:
                        # Transition to waiting_for_approval
                        async with self._session_factory() as sess:
                            svc = self._make_job_service(sess)
                            await svc.transition_state(job_id, JobState.waiting_for_approval)
                            await sess.commit()

                        await self._event_bus.publish(domain_event)

                        # Wait for operator resolution — the adapter is
                        # also awaiting the same Future, so when it
                        # resolves the SDK resumes automatically.
                        approval_id = domain_event.payload.get("approval_id", "")
                        resolution = await self._approval_service.wait_for_resolution(approval_id)

                        # Publish approval_resolved event
                        await self._event_bus.publish(
                            DomainEvent(
                                event_id=_make_event_id(),
                                job_id=job_id,
                                timestamp=datetime.now(UTC),
                                kind=DomainEventKind.approval_resolved,
                                payload={
                                    "approval_id": approval_id,
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

                    # Handle model downgrade: abort the agent and move
                    # the job to sign-off so the operator can decide.
                    if domain_event.kind == DomainEventKind.model_downgraded:
                        requested = domain_event.payload.get("requested_model", "")
                        actual = domain_event.payload.get("actual_model", "")
                        log.warning(
                            "model_downgrade_detected",
                            job_id=job_id,
                            requested=requested,
                            actual=actual,
                        )
                        await self._event_bus.publish(domain_event)
                        # Abort the running strategy/session
                        try:
                            await strategy.abort()
                        except Exception:
                            log.warning("strategy_abort_on_downgrade_failed", job_id=job_id, exc_info=True)

                        # Transition to succeeded with unresolved resolution
                        # so the job lands in the sign-off column.
                        reason = f"Model downgraded: requested {requested} but received {actual}"
                        async with self._session_factory() as session:
                            svc = self._make_job_service(session)
                            await svc.transition_state(job_id, JobState.succeeded, failure_reason=reason)
                            from backend.persistence.job_repo import JobRepository

                            job_repo = JobRepository(session)
                            await job_repo.update_resolution(job_id, "unresolved")
                            await session.commit()

                        await self._event_bus.publish(
                            DomainEvent(
                                event_id=_make_event_id(),
                                job_id=job_id,
                                timestamp=datetime.now(UTC),
                                kind=DomainEventKind.job_succeeded,
                                payload={
                                    "resolution": "unresolved",
                                    "model_downgraded": True,
                                    "requested_model": requested,
                                    "actual_model": actual,
                                },
                            )
                        )
                        log.info("job_moved_to_signoff_model_downgrade", job_id=job_id)
                        return  # skip normal completion flow

                    # Buffer transcript content for progress headlines
                    if domain_event.kind == DomainEventKind.transcript_updated:
                        role = domain_event.payload.get("role", "")
                        content = domain_event.payload.get("content", "")
                        if role == "agent" and content and job_id in self._headline_transcript:
                            buf = self._headline_transcript[job_id]
                            buf.append(content)
                            # Keep only last 5 agent messages
                            if len(buf) > 5:
                                self._headline_transcript[job_id] = buf[-5:]

                        # Track tool calls by turn to trigger AI summaries at turn boundaries
                        if self._utility_session is not None and role == "tool_call":
                            turn_id = str(domain_event.payload.get("turn_id") or "")
                            tool_intent = str(domain_event.payload.get("tool_intent") or "")
                            tool_title = str(domain_event.payload.get("tool_title") or "")
                            tool_name = str(domain_event.payload.get("tool_name") or "tool")
                            tool_args = str(domain_event.payload.get("tool_args") or "")
                            state = self._turn_tool_buffer.get(job_id)

                            # Flush previous turn if turn_id changed
                            if state and state["turn_id"] != turn_id and state["tool_calls"]:
                                asyncio.create_task(
                                    self._summarize_tool_group(job_id, state["turn_id"], state["tool_calls"]),
                                    name=f"tool-summary-{job_id}-{state['turn_id'][:8]}",
                                )

                            if not state or state["turn_id"] != turn_id:
                                self._turn_tool_buffer[job_id] = {"turn_id": turn_id, "tool_calls": []}
                                state = self._turn_tool_buffer[job_id]

                            state["tool_calls"].append(
                                {
                                    "tool_name": tool_name,
                                    "tool_args": tool_args[:100],
                                    "tool_intent": tool_intent,
                                    "tool_title": tool_title,
                                }
                            )

                        # When an agent message closes a turn, flush the tool buffer for that turn
                        if self._utility_session is not None and role == "agent":
                            turn_id = str(domain_event.payload.get("turn_id") or "")
                            state = self._turn_tool_buffer.get(job_id)
                            if state and state["turn_id"] == turn_id and state["tool_calls"]:
                                asyncio.create_task(
                                    self._summarize_tool_group(job_id, state["turn_id"], state["tool_calls"]),
                                    name=f"tool-summary-{job_id}-{state['turn_id'][:8]}",
                                )
                                self._turn_tool_buffer.pop(job_id, None)

                    await self._event_bus.publish(domain_event)

            if error_reason:
                # An error event was received during execution — finalize diff before failing
                if self._diff_service is not None and worktree_path and base_ref:
                    try:
                        await self._diff_service.finalize(job_id, worktree_path, base_ref)
                    except Exception:
                        log.warning("diff_finalize_failed", job_id=job_id, exc_info=True)
                await self._fail_job(job_id, error_reason)
                return

            # Final diff snapshot before resolution
            if self._diff_service is not None and worktree_path and base_ref:
                try:
                    await self._diff_service.finalize(job_id, worktree_path, base_ref)
                except Exception:
                    log.warning("diff_finalize_failed", job_id=job_id, exc_info=True)

            # Always go to sign-off: leave resolution to operator
            final_resolution: str = "unresolved"
            log.info("job_awaiting_sign_off", job_id=job_id)

            # Strategy completed normally → succeeded
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.transition_state(job_id, JobState.succeeded)
                from backend.persistence.job_repo import JobRepository

                job_repo = JobRepository(session)
                await job_repo.update_resolution(job_id, final_resolution, pr_url=None)
                await session.commit()

            await self._event_bus.publish(
                DomainEvent(
                    event_id=_make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.job_succeeded,
                    payload={"resolution": final_resolution},
                )
            )
            log.info(
                "job_succeeded",
                job_id=job_id,
                resolution=final_resolution,
            )
        except asyncio.CancelledError:
            log.info("job_canceled_by_task", job_id=job_id)
            # Finalize diff so changes are preserved even for canceled jobs
            if self._diff_service is not None and worktree_path and base_ref:
                try:
                    await self._diff_service.finalize(job_id, worktree_path, base_ref)
                except Exception:
                    log.warning("diff_finalize_failed", job_id=job_id, exc_info=True)
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
        except Exception as exc:
            log.error("job_execution_failed", job_id=job_id, exc_info=True)
            # Finalize diff so changes are preserved even for crashed jobs
            if self._diff_service is not None and worktree_path and base_ref:
                try:
                    await self._diff_service.finalize(job_id, worktree_path, base_ref)
                except Exception:
                    log.warning("diff_finalize_failed", job_id=job_id, exc_info=True)
            await self._fail_job(job_id, f"Execution error: {exc}")
        finally:
            tel.end_job(job_id)
            heartbeat_task.cancel()
            self._heartbeat_tasks.pop(job_id, None)
            headline_t = self._headline_tasks.pop(job_id, None)
            if headline_t is not None:
                headline_t.cancel()
            self._headline_transcript.pop(job_id, None)
            self._headline_last_snapshot.pop(job_id, None)
            self._headline_last_text.pop(job_id, None)
            self._turn_tool_buffer.pop(job_id, None)
            self._tasks.pop(job_id, None)
            self._strategies.pop(job_id, None)
            self._last_activity.pop(job_id, None)
            self._session_ids.pop(job_id, None)
            self._echo_suppress.pop(job_id, None)
            if self._utility_session is not None:
                await self._utility_session.notify_job_ended()
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

    async def _summarize_tool_group(self, job_id: str, turn_id: str, tool_calls: list[_ToolCallEntry]) -> None:
        """Generate a short AI label for a tool group and publish it as an SSE event.

        Only fires when no SDK intention string is available on all tool calls.
        If every call has an SDK-provided intent, the UI uses that directly.
        """
        if all(tc.get("tool_intent") for tc in tool_calls):
            return

        lines = []
        for tc in tool_calls:
            args_snippet = tc["tool_args"].strip()[:80]
            lines.append(f"{tc['tool_name']}: {args_snippet}" if args_snippet else tc["tool_name"])

        prompt = (
            "Given these tool calls from a coding agent turn, write a single VERY SHORT label "
            'in the format "<tool>: <what it did>" — maximum 7 words total, lowercase, no period. '
            "If multiple different tools appear, pick the most significant one. "
            "Respond with ONLY the label.\n\n" + "\n".join(lines)
        )

        try:
            raw = await self._utility_session.complete(prompt, timeout=10)  # type: ignore[union-attr]
            summary = raw.strip().strip('"').strip(".")
            if summary and len(summary) > 3:
                await self._event_bus.publish(
                    DomainEvent(
                        event_id=_make_event_id(),
                        job_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=DomainEventKind.tool_group_summary,
                        payload={"turn_id": turn_id, "summary": summary},
                    )
                )
                # Also emit as a log line so it appears in the ExecutionTimeline
                await self._event_bus.publish(
                    DomainEvent(
                        event_id=_make_event_id(),
                        job_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=DomainEventKind.log_line_emitted,
                        payload={
                            "level": "info",
                            "message": f"Completed: {summary}",
                            "context": {"source": "tool_summary"},
                        },
                    )
                )
        except Exception:
            log.debug("tool_group_summary_failed", job_id=job_id, turn_id=turn_id, exc_info=True)

    async def _headline_loop(self, job_id: str) -> None:
        """Periodically generate a one-line progress headline from recent transcript."""
        headline_prompt = (
            "Given the following recent agent messages from a coding session, "
            "write a single short sentence (max 12 words) describing what the agent "
            "is currently doing. Use present continuous tense. No period at the end. "
            "Respond with ONLY the headline, nothing else.\n\nMessages:\n"
        )
        initial_delay_s = 8
        interval_s = 15
        try:
            await asyncio.sleep(initial_delay_s)
            first = True
            while True:
                buf = self._headline_transcript.get(job_id)

                if buf:
                    # Fresh messages available — snapshot them
                    recent = list(buf)
                    buf.clear()
                    self._headline_last_snapshot[job_id] = recent
                else:
                    # No new messages — reuse last snapshot as fallback
                    recent = self._headline_last_snapshot.get(job_id, [])

                if not recent:
                    if not first:
                        await asyncio.sleep(interval_s)
                    first = False
                    continue

                prompt = headline_prompt + "\n---\n".join(msg[:300] for msg in recent)

                try:
                    headline = await self._utility_session.complete(prompt, timeout=10)  # type: ignore[union-attr]
                    headline = headline.strip().strip('"').strip(".")
                    last = self._headline_last_text.get(job_id, "")
                    if headline and len(headline) > 3 and headline != last:
                        self._headline_last_text[job_id] = headline
                        await self._event_bus.publish(
                            DomainEvent(
                                event_id=_make_event_id(),
                                job_id=job_id,
                                timestamp=datetime.now(UTC),
                                kind=DomainEventKind.progress_headline,
                                payload={"headline": headline},
                            )
                        )
                        # Also emit as a log line so it appears in the ExecutionTimeline
                        await self._event_bus.publish(
                            DomainEvent(
                                event_id=_make_event_id(),
                                job_id=job_id,
                                timestamp=datetime.now(UTC),
                                kind=DomainEventKind.log_line_emitted,
                                payload={
                                    "level": "info",
                                    "message": f"Progress: {headline}",
                                    "context": {"source": "headline"},
                                },
                            )
                        )
                except Exception:
                    log.debug("headline_generation_failed", job_id=job_id, exc_info=True)

                await asyncio.sleep(interval_s)
                first = False
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
                await svc.transition_state(job_id, JobState.failed, failure_reason=reason)
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
            # Skip if this specific session was already summarized (e.g. rapid retry)
            from backend.persistence.artifact_repo import ArtifactRepository
            from backend.services.artifact_service import ArtifactService

            async with self._session_factory() as session:
                artifact_svc = ArtifactService(ArtifactRepository(session))
                existing = await artifact_svc.get_latest_session_summary(job_id)
                if existing is not None:
                    # Check if the existing summary is for the current session
                    import re

                    m = re.search(r"session-(\d+)-summary", existing.name)
                    existing_session = int(m.group(1)) if m else 0
                    if existing_session >= job.session_count:
                        return  # already summarized this session
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
        # Publish the operator's instruction as a transcript entry so it
        # appears in the chat trace.  The echo-suppression registered in
        # _start_job will prevent the SDK echo from duplicating it.
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
                    "content": instruction,
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
        """Best-effort PR creation via platform adapter. Returns the PR URL or None."""
        if self._platform_registry is None:
            log.info("pr_creation_skipped_no_registry", job_id=job_id)
            return None

        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            job = await svc.get_job(job_id)

        if job is None or not job.worktree_path or not job.branch:
            log.info("pr_creation_skipped_no_worktree", job_id=job_id)
            return None

        import re

        _ref_pattern = re.compile(r"^[a-zA-Z0-9/_.-]+$")
        if not _ref_pattern.match(job.branch):
            log.warning("pr_creation_invalid_branch", job_id=job_id)
            return None
        if not _ref_pattern.match(job.base_ref):
            log.warning("pr_creation_invalid_base_ref", job_id=job_id)
            return None

        adapter = await self._platform_registry.get_adapter(job.repo)
        pr_result = await adapter.create_pr(
            cwd=job.worktree_path,
            head=job.branch,
            base=job.base_ref,
            title=f"[Tower] {job.prompt[:80]}",
            body=f"Automated PR created by Tower for job `{job_id}`.",
        )
        if pr_result.ok:
            log.info("pr_created", job_id=job_id, pr_url=pr_result.url, platform=adapter.name)
            return pr_result.url
        log.warning("pr_creation_failed", job_id=job_id, platform=adapter.name, error=pr_result.error)
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
            SessionEventKind.model_downgraded: DomainEventKind.model_downgraded,
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
                    await svc.transition_state(
                        job.id,
                        JobState.failed,
                        failure_reason="Server restarted while job was running",
                    )
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
