"""Long-running job execution manager.

RuntimeService orchestrates the full lifecycle of agent jobs: session creation,
event streaming, heartbeat monitoring, diff tracking, approval flow,
cancellation, and post-job cleanup.

Progress tracking (plan management, turn classification, title generation,
activity grouping) is handled by ``TrailService`` — see
``backend/services/trail_service.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import time
import uuid
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.exc import DBAPIError

from backend.config import build_session_config
from backend.models.api_schemas import ExecutionPhase
from backend.models.domain import (
    TERMINAL_STATES,
    ApprovalResolution,
    CurrentExecutionContext,
    CurrentExecutionDispatchResult,
    GitMergeOutcome,
    Job,
    JobMode,
    JobNotFoundError,
    JobSource,
    JobState,
    Resolution,
    ServiceInitError,
    SessionConfig,
    StateConflictError,
)
from backend.models.events import (
    TRANSCRIPT_KINDS,
    EventKind,
    SessionEvent,
    new_event,
)
from backend.models.session_event import EventMetadata
from backend.persistence.job_repo import JobRepository
from backend.services.adapters.integration_policy import IntegrationDisabledError, IntegrationPolicy
from backend.services.completers.copilot_steer import NativeDeliveryError
from backend.services.job.job_service import JobService
from backend.services.runtime.attempt_bindings import AttemptBindingConflictError, AttemptBindings, AttemptExecution
from backend.services.runtime.execution_control import ExecutionControl
from backend.services.runtime.handoff import (
    build_followup_handoff_prompt_for_job,
    build_resume_handoff_prompt_for_job,
    load_handoff_context_for_job,
)
from backend.services.runtime.resume import (
    attempt_resume_fallback as _attempt_resume_fallback_impl,
)
from backend.services.runtime.resume import (
    create_followup_job as _create_followup_job_impl,
)
from backend.services.runtime.resume import (
    ensure_resumable_worktree as _ensure_resumable_worktree_impl,
)
from backend.services.runtime.resume import (
    recover_active_job as _recover_active_job_impl,
)
from backend.services.runtime.resume import (
    resume_job as _resume_job_impl,
)
from backend.services.runtime.resume import (
    resume_orphaned as _resume_orphaned_impl,
)
from backend.services.runtime.resume import (
    rollback_recovery as _rollback_recovery_impl,
)
from backend.services.runtime.telemetry import RuntimeTelemetry
from backend.services.runtime.verify import (
    run_followup_turn as _run_followup_turn_impl,
)
from backend.services.runtime.verify import (
    run_verify_review as _run_verify_review_impl,
)
from backend.validators import REF_PATTERN

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from backend.models.execution_contracts import ExecutionContract, ExecutionContractPin, LegacyStartIntent
    from backend.models.schemas.recipes import RecipeRunResponse, RecipeSelection
    from backend.services.action_policy.classifier import RepoPolicy
    from backend.services.action_policy.governance import GovernanceDecider
    from backend.services.action_policy.production import StandaloneGovernance
    from backend.services.events.event_processor import EventProcessor
    from backend.services.events.governance_subscriber import GovernanceSubscriber
    from backend.services.events.ingest_service import IngestService
    from backend.services.events.telemetry_subscriber import TelemetrySubscriber
    from backend.services.recipe.executor import RecipeExecutor
    from backend.services.runtime.execution_contracts import ExecutionContracts
    from backend.services.runtime.handoff import PendingHandoff
    from backend.services.sidecar.dispatcher import SidecarDispatcher
    from backend.services.steps.tracker import StepTracker
    from backend.services.terminal.terminal_service import TerminalService
    from backend.services.tools.preflight_curator import PreflightCurator, PreflightToolCall
    from backend.services.trail import TrailService


class AgentSession:
    """Thin wrapper around the adapter for a single running agent session."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._adapter: AgentAdapterInterface | None = None
        self.execution_token: str | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def execute(
        self,
        config: SessionConfig,
        adapter: AgentAdapterInterface,
    ) -> AsyncIterator[SessionEvent]:
        self._adapter = adapter
        self.execution_token = uuid.uuid4().hex
        self._session_id = await adapter.create_session(
            dataclass_replace(config, execution_token=self.execution_token),
        )
        async for event in adapter.stream_events(self._session_id):
            yield event

    async def send_message(self, message: str) -> None:
        if self._adapter and self._session_id:
            await self._adapter.send_message(self._session_id, message)

    async def interrupt(self) -> None:
        if self._adapter and self._session_id:
            await self._adapter.interrupt_session(self._session_id)

    def pause_tools(self) -> None:
        if self._adapter and self._session_id:
            self._adapter.pause_tools(self._session_id)

    def resume_tools(self) -> None:
        if self._adapter and self._session_id:
            self._adapter.resume_tools(self._session_id)

    async def abort(self) -> None:
        if self._adapter and self._session_id:
            await self._adapter.abort_session(self._session_id)


class EventAction(enum.Enum):
    """Action directive returned by ``_process_agent_event``."""

    skip = enum.auto()
    publish = enum.auto()
    abort = enum.auto()


@dataclass(frozen=True, slots=True)
class SessionAttemptResult:
    """Outcome of a single ``_execute_session_attempt`` call."""

    session_id: str | None = None
    error_reason: str | None = None
    made_progress: bool = False
    downgrade: tuple[str, str] | None = None  # (requested, actual) model names


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    """Pre-recovery job state for rollback on failure."""

    state: JobState
    session_count: int
    completed_at: datetime | None
    resolution: str | None
    failure_reason: str | None
    archived_at: datetime | None
    merge_status: str | None
    pr_url: str | None


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CPLConfig
    from backend.services.adapters.adapter_registry import AdapterRegistry
    from backend.services.adapters.agent_adapter import AgentAdapterInterface
    from backend.services.adapters.platform_adapter import PlatformRegistry
    from backend.services.artifacts.diff_service import DiffService
    from backend.services.completers.summarization_service import SummarizationService
    from backend.services.events.event_bus import EventBus
    from backend.services.git.git_service import GitService
    from backend.services.job.approval_service import ApprovalService
    from backend.services.merge_service import MergeService
    from backend.services.sidecar.session import SidecarSessionManager

log = structlog.get_logger()

_SERVER_RESTART_RECOVERY_INSTRUCTION = (
    "The CodePlane server restarted while this job was in progress. "
    "Resume this existing job in place from the current worktree and prior context. "
    "Do not start over or create a duplicate job."
)

# Heartbeat configuration
_HEARTBEAT_INTERVAL_S = 30

# Stall detection — after this many seconds of tool inactivity, ask the sidecar
# session whether the tool is likely stuck or legitimately slow.
_STALL_CHECK_THRESHOLD_S = 120  # 2 minutes before first check
_STALL_RECHECK_INTERVAL_S = 120  # re-ask every 2 minutes if sidecar says wait

# If the arbiter itself fails (exception, timeout, or unparseable response)
# this many times in a row for the same job, stop trusting "no verdict" to
# mean "everything is fine". A broken arbiter must not be able to leave a
# job silently stuck forever with no operator-visible signal — so we fall
# back to treating the tool as stalled and go through the normal
# interrupt/re-prompt path once the arbiter has proven unreliable for a
# sustained period.
_STALL_CHECK_MAX_CONSECUTIVE_FAILURES = 3

# Stall detection above only fires while a tool call is actively tracked
# (``_active_tool`` is set). If the agent/SDK hangs *between* tool calls —
# e.g. waiting on the underlying LLM API — there is no tool call to ask the
# arbiter about, and without this fallback the whole stall-detection
# subsystem would be blind to that hang forever. After this much total
# inactivity with no tool in flight, treat the session as stalled directly
# (no arbiter round-trip needed — there's nothing tool-specific to judge).
_STALL_NO_TOOL_INACTIVITY_THRESHOLD_S = 300  # 5 minutes

_STALL_ARBITER_PROMPT = """\
A coding agent is running tool `{tool_name}` which has been active for {elapsed}.
Tool arguments (truncated): {tool_args}

Is this tool call likely stuck (no useful work happening) or legitimately slow
(e.g. a large test suite, long build, big download)?

Respond with ONLY one JSON object:
{{"action": "wait" | "interrupt", "reason": "one sentence"}}
"""


def _session_event_counts_as_resume_progress(event: SessionEvent) -> bool:
    """Return True once a resumed session has produced real agent work."""
    if event.kind in (
        EventKind.file_edited,
        EventKind.approval_requested,
        EventKind.model_downgraded,
    ):
        return True
    return event.kind in TRANSCRIPT_KINDS


def _stringify_tool_args(args: object) -> str:
    """Render TF tool ``arguments`` (dict | str | None) as a string for heartbeat."""
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args)
    except (TypeError, ValueError):
        return str(args)


class RuntimeService:
    """Manages active job tasks, capacity enforcement, and queueing."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        adapter_registry: AdapterRegistry,
        config: CPLConfig,
        approval_service: ApprovalService | None = None,
        diff_service: DiffService | None = None,
        git_service: GitService | None = None,
        merge_service: MergeService | None = None,
        summarization_service: SummarizationService | None = None,
        platform_registry: PlatformRegistry | None = None,
        sidecar_sessions: SidecarSessionManager | None = None,
        step_tracker: StepTracker | None = None,
        trail_service: TrailService | None = None,
        sidecar_dispatcher: SidecarDispatcher | None = None,
        event_processor: EventProcessor | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._adapter_registry = adapter_registry
        self._config = config
        self._approval_service = approval_service
        self._execution_control = approval_service.execution_control if approval_service else ExecutionControl()
        self._execution_epoch = uuid.uuid4().hex
        self._attempt_bindings = AttemptBindings(self._execution_epoch)
        self._external_generations: dict[str, str] = {}
        self._starting_tasks: dict[str, asyncio.Task[Any]] = {}
        self._diff_service = diff_service
        self._git_service = git_service
        self._merge_service = merge_service
        self._summarization_service = summarization_service
        self._platform_registry = platform_registry
        self._sidecar_sessions = sidecar_sessions
        self._step_tracker = step_tracker
        self._sidecar_dispatcher = sidecar_dispatcher
        # The one funnel — both managed (here) and imported (ingest sources)
        # producers route their TF-native events through this processor.
        self._event_processor = event_processor
        # Telemetry bus-subscriber — wired late; runtime only clears its per-job
        # state at true job end (after retries/verify).
        self._telemetry_subscriber: TelemetrySubscriber | None = None
        # Governance substrate — the process-wide decider (one durable store + the
        # three preset pipelines) and its accrual bus-subscriber, both wired late
        # from the composition root so the store path / spend reader are injectable.
        self._governance_decider: GovernanceDecider | None = None
        self._standalone_governance: StandaloneGovernance | None = None
        self._governance_subscriber: GovernanceSubscriber | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._recipe_controllers: dict[str, RecipeExecutor] = {}
        self._recipe_runner_slots = asyncio.Semaphore(config.runtime.max_concurrent_jobs)
        self._legacy_runner_slots: set[str] = set()
        self._queued_session_tokens: dict[str, str] = {}
        self._scout_tasks: dict[str, asyncio.Task[SessionConfig]] = {}
        self._agent_sessions: dict[str, AgentSession] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_activity: dict[str, float] = {}
        self._active_tool: dict[str, tuple[str, str, str]] = {}  # job_id → (tool_name, started_iso, tool_args)
        self._stall_check_pending: set[str] = set()  # job_ids currently being checked
        self._last_stall_check: dict[str, float] = {}  # job_id → monotonic time of last check
        self._stall_detection_disabled: set[str] = set()  # jobs with stall detection explicitly off
        self._stall_check_consecutive_failures: dict[str, int] = {}  # job_id → failed/unparseable checks in a row
        self._waiting_for_approval: set[str] = set()
        self._session_ids: dict[str, str] = {}
        self._policy_routers: dict[str, Any] = {}  # job_id → PolicyRouter
        self._repo_policies: dict[str, RepoPolicy] = {}
        self._execution_contracts: ExecutionContracts | None = None
        self._job_contract_pins: dict[str, ExecutionContractPin] = {}
        self._legacy_recoveries: dict[str, object] = {}
        self._legacy_handoffs: dict[str, PendingHandoff] = {}
        self._recipe_contract_pins: dict[str, ExecutionContractPin | None] = {}
        self._policy_batchers: dict[str, Any] = {}  # job_id → ApprovalBatcher
        self._dequeue_lock = asyncio.Lock()
        self._shutting_down = False
        self._pending_starts: dict[str, tuple[str | None, str | None]] = {}
        self._preflight_curator: PreflightCurator | None = None
        self._ingest_service: IngestService | None = None
        # Active sidecar gates per job: job_id → {sidecar_name: reason}
        self._active_gates: dict[str, dict[str, str]] = {}

        # Subscribe to policy settings changes for mid-job reload
        self._event_bus.subscribe(self._on_policy_settings_changed)
        self._queued_override_prompts: dict[str, str] = {}
        self._queued_resume_session_ids: dict[str, str] = {}
        # Contents to suppress when the SDK echoes them back (already published locally)
        self._echo_suppress: dict[str, set[str]] = {}
        # Trail service (unified timeline, plan, activity tracking)
        self._trail_service = trail_service
        # Observer terminals: job_id → terminal session ID
        self._terminal_service: TerminalService | None = None
        # Telemetry subsystem (extracted)
        self._telemetry = RuntimeTelemetry(
            session_factory=session_factory,
            event_bus=event_bus,
            make_job_service=self._make_job_service,
            resolve_adapter=self._resolve_adapter,
            trail_service=trail_service,
        )
        from backend.services.artifacts.finalization_service import ArtifactFinalizationService

        self._artifact_finalizer = ArtifactFinalizationService(
            session_factory=session_factory,
            event_bus=event_bus,
            summarization_service=summarization_service,
            telemetry=self._telemetry,
        )

    def set_trail_service(self, svc: TrailService) -> None:
        """Wire the TrailService for plan/activity tracking (late binding)."""
        self._trail_service = svc
        self._telemetry.set_trail_service(svc)

    def set_event_processor(self, processor: EventProcessor) -> None:
        """Wire the shared EventProcessor funnel (late binding)."""
        self._event_processor = processor

    def set_telemetry_subscriber(self, subscriber: TelemetrySubscriber) -> None:
        """Wire the telemetry bus-subscriber for per-job state cleanup (late binding).

        The subscriber persists telemetry off the canonical event bus; the runtime
        only needs to clear its per-job state at the true end of the job lifecycle
        (after retries + verify), which the bus-level terminal events can't pinpoint.
        """
        self._telemetry_subscriber = subscriber

    def set_governance(self, decider: GovernanceDecider, subscriber: GovernanceSubscriber) -> None:
        """Wire the process-wide governance decider + accrual subscriber (late binding).

        The decider owns the durable governance store and the three preset
        pipelines; ``_setup_action_policy`` registers each job's preset on it and
        injects it into that job's :class:`PolicyRouter`. The subscriber is already
        attached to the event bus by the composition root; the runtime only needs a
        handle to clear per-job state at true job end.
        """
        self._governance_decider = decider
        self._governance_subscriber = subscriber

    def set_terminal_service(self, svc: TerminalService) -> None:
        """Wire the TerminalService for job terminals."""
        self._terminal_service = svc

    def set_preflight_curator(self, curator: PreflightCurator) -> None:
        """Wire the PreflightCurator for pre-job context curation."""
        self._preflight_curator = curator

    def set_ingest_service(self, svc: IngestService) -> None:
        """Wire the IngestService for operator message delivery to CLI sessions."""
        self._ingest_service = svc

    def _resolve_adapter(self, sdk: str) -> AgentAdapterInterface:
        """Resolve the adapter for a given SDK via the registry."""
        return self._adapter_registry.get_adapter(sdk)

    def _make_job_service(self, session: AsyncSession) -> JobService:
        from backend.persistence.project_repo import ProjectRepository

        return JobService(
            job_repo=JobRepository(session),
            git_service=self._git_service,
            config=self._config,
            event_bus=self._event_bus,
            project_repo=ProjectRepository(session),
        )

    async def _get_job(self, job_id: str) -> Job | None:
        """Load a job by id (convenience wrapper)."""
        async with self._session_factory() as db:
            return await JobRepository(db).get(job_id)

    async def _finalize_diff_safe(self, job_id: str, worktree_path: str | None, base_ref: str | None) -> None:
        """Finalize the diff snapshot, swallowing exceptions."""
        if self._diff_service is None or not worktree_path or not base_ref:
            return
        try:
            await self._diff_service.finalize(job_id, worktree_path, base_ref)
        except (Exception, asyncio.CancelledError):
            log.warning("diff_finalize_failed", job_id=job_id, exc_info=True)

    async def _finalize_artifacts_safe(self, job_id: str) -> None:
        """Finalize terminal artifacts without blocking remaining runtime cleanup."""
        try:
            await self._artifact_finalizer.finalize(job_id)
        except Exception as exc:
            log.error(
                "artifact_finalization_failed",
                job_id=job_id,
                error_type=type(exc).__name__,
                error=str(exc),
                exc_info=True,
            )

    async def _finalize_naming(self, job_id: str) -> None:
        """Retry title generation and produce description for untitled/undescribed jobs."""
        if self._sidecar_sessions is None:
            return
        try:
            from backend.persistence.job_repo import JobRepository

            # Read current job state
            async with self._session_factory() as session:
                repo = JobRepository(session)
                job = await repo.get(job_id)
                if not job:
                    return

                needs_title = job.title is None
                needs_description = job.description is None

            if not needs_title and not needs_description:
                return

            # Build context from the job prompt (first 2000 chars)
            context = (job.prompt or "")[:2000]
            if not context:
                return

            if needs_title:
                title = await self._generate_title_safe(context)
                if title:
                    from backend.persistence.database import serialized_write

                    async with serialized_write(self._session_factory) as ws:
                        await JobRepository(ws).update_title_and_branch(job_id, title=title)
                    await self._event_bus.publish(
                        new_event(
                            session_id=job_id,
                            timestamp=datetime.now(UTC),
                            kind=EventKind.job_title_updated,
                            payload={"title": title},
                        )
                    )
                    log.info("finalize_title_generated", job_id=job_id, title=title)

            if needs_description:
                desc = await self._generate_description_safe(context)
                if desc:
                    from backend.persistence.database import serialized_write

                    async with serialized_write(self._session_factory) as ws:
                        from sqlalchemy import update as sa_update

                        from backend.models.db import JobRow

                        await ws.execute(sa_update(JobRow).where(JobRow.id == job_id).values(description=desc))
                    log.info("finalize_description_generated", job_id=job_id)
        except Exception:
            log.debug("finalize_naming_failed", job_id=job_id, exc_info=True)

    async def _generate_title_safe(self, context: str) -> str | None:
        """One-shot title generation via sidecar. Returns None on failure."""
        try:
            sidecar_sessions = self._sidecar_sessions
            if sidecar_sessions is None:
                return None
            prompt = (
                "Given this coding task prompt, generate a concise 3-8 word title. "
                "Respond with ONLY the title text, no quotes, no punctuation at the end.\n\n"
                f"Task:\n{context}"
            )
            title = await sidecar_sessions.complete(prompt, timeout=10.0)
            title = str(title).strip().strip('"').strip("'")
            return title if title and len(title) >= 3 else None
        except Exception:
            return None

    async def _generate_description_safe(self, context: str) -> str | None:
        """One-shot description generation via sidecar. Returns None on failure."""
        try:
            sidecar_sessions = self._sidecar_sessions
            if sidecar_sessions is None:
                return None
            prompt = (
                "Given this coding task prompt, generate a 1-2 sentence description "
                "of the work being done. Be specific and concise. "
                "Respond with ONLY the description text.\n\n"
                f"Task:\n{context}"
            )
            desc = await sidecar_sessions.complete(prompt, timeout=10.0)
            desc = str(desc).strip()
            return desc if desc and len(desc) >= 10 else None
        except Exception:
            return None

    @property
    def running_count(self) -> int:
        """Number of currently running job tasks."""
        return len(self._tasks)

    @property
    def max_concurrent(self) -> int:
        return self._config.runtime.max_concurrent_jobs

    async def setup_and_start(
        self,
        job: Job,
        session_token: str | None = None,
    ) -> Job:
        """Background task: create worktree for a ``preparing`` job then start it.

        Uses a dedicated DB session so this can run after the HTTP response.
        Publishes ``job_state_changed`` when transitioning to ``queued``.
        If any step fails, the job is transitioned to ``failed`` so the user
        sees the error instead of a stuck-in-preparing state.
        """

        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                updated_job = await svc.setup_workspace(job.id)
                await session.commit()

            if updated_job.state == JobState.failed:
                await self._emit_setup_progress(job.id, "failed")
                await self._publish_state_event(job.id, JobState.preparing, JobState.failed)
                return updated_job
            if updated_job.state != JobState.queued:
                return updated_job

            # Publish preparing → queued transition
            await self._emit_setup_progress(job.id, "workspace_ready")
            await self._publish_state_event(job.id, JobState.preparing, JobState.queued)

            await self.start_or_enqueue(
                updated_job,
                session_token=session_token,
            )
            return updated_job
        except Exception:
            log.error("setup_and_start_failed", job_id=job.id, exc_info=True)
            await self._fail_job(job.id, "Job setup failed")
            raise

    async def start_or_enqueue(
        self,
        job: Job,
        override_prompt: str | None = None,
        resume_sdk_session_id: str | None = None,
        session_token: str | None = None,
    ) -> None:
        """Start the job if capacity allows, otherwise keep it queued."""
        if job.observation_only:
            raise StateConflictError("Observation-only history cannot enter the execution scheduler")
        IntegrationPolicy(self._config).require_enabled(job.sdk)

        if self._shutting_down:
            raise StateConflictError("The runtime is shutting down and cannot start another Job.")
        if session_token is not None:
            self._queued_session_tokens[job.id] = session_token
        async with self._dequeue_lock:
            if self.running_count < self.max_concurrent:
                await self._start_job(job, override_prompt=override_prompt, resume_sdk_session_id=resume_sdk_session_id)
                return

            if job.state != JobState.queued:
                self._pending_starts[job.id] = (override_prompt, resume_sdk_session_id)
                log.info("job_waiting_for_capacity", job_id=job.id, state=job.state, running=self.running_count)
                return
            if override_prompt is not None:
                self._queued_override_prompts[job.id] = override_prompt
            if resume_sdk_session_id is not None:
                self._queued_resume_session_ids[job.id] = resume_sdk_session_id
            log.info("job_enqueued", job_id=job.id, running=self.running_count)

    async def _activate_job_sidecars(self, job: Job) -> None:
        session_token = self._queued_session_tokens.pop(job.id, None)
        # Open the built-in sidecar sessions for this job
        if self._sidecar_sessions is not None:
            from backend.models.domain import SIDECAR_ARBITER, SIDECAR_ENRICHER, SIDECAR_PLANNER

            try:
                for name in (SIDECAR_ARBITER, SIDECAR_PLANNER, SIDECAR_ENRICHER):
                    self._sidecar_sessions.open(job.id, name, token=session_token if name == SIDECAR_ARBITER else None)
            except Exception:
                log.warning("sidecar_session_setup_failed", job_id=job.id, exc_info=True)

        # Activate custom sidecar templates from the library
        if self._sidecar_dispatcher is not None:
            await self._activate_custom_sidecars(job)

    async def _activate_custom_sidecars(self, job: Job) -> None:
        """Load custom sidecar templates from the DB and activate them via the dispatcher."""
        import json

        from backend.persistence.sidecar_template_repo import SidecarTemplateRepository
        from backend.services.sidecar.dispatcher import hydrate_definition

        try:
            async with self._session_factory() as session:
                repo = SidecarTemplateRepository(session)
                templates = await repo.list_enabled()

            definitions = []
            for tpl in templates:
                try:
                    raw = json.loads(tpl.definition_json)
                    defn = hydrate_definition(raw)
                    # Only auto-attach templates that explicitly opt in.
                    # scope must be global/repo AND autoAttach must be true.
                    if defn.scope in ("global", "repo") and defn.auto_attach:
                        defn_with_id = hydrate_definition({**raw, "templateId": tpl.id})
                        definitions.append(defn_with_id)
                except Exception:
                    log.warning("sidecar_template_hydration_failed", template_id=tpl.id, exc_info=True)

            if definitions:
                assert self._sidecar_dispatcher is not None
                self._sidecar_dispatcher.activate(job.id, definitions)
                log.info(
                    "custom_sidecars_activated",
                    job_id=job.id,
                    count=len(definitions),
                    names=[d.name for d in definitions],
                )
                # Run preflight sidecars before the agent starts
                await self._sidecar_dispatcher.run_preflight(job.id)
        except Exception:
            log.warning("custom_sidecar_activation_failed", job_id=job.id, exc_info=True)

    async def _ensure_resumable_worktree(self, job_repo: JobRepository, job: Job) -> Job:
        """Ensure a job has a usable worktree before resuming or recovering it."""
        return await _ensure_resumable_worktree_impl(self, job_repo, job)

    async def _recover_active_job(
        self,
        job_id: str,
        *,
        instruction: str = _SERVER_RESTART_RECOVERY_INSTRUCTION,
    ) -> Job:
        """Restart an active job after backend restart without marking it failed."""
        from backend.persistence.recipe_run_repo import RecipeRunRepository

        async with self._session_factory() as session:
            run = await RecipeRunRepository(session).get_for_job(job_id)
            if run is not None:
                repository = JobRepository(session)
                job = await repository.get(job_id)
                if job is None:
                    raise JobNotFoundError(f"Job {job_id} no longer exists.")
                if job.state not in {JobState.running, JobState.waiting_for_approval}:
                    raise StateConflictError("Only an active recipe Job can be recovered.")
                await repository.reset_for_recovery(job_id, job.session_count)
                await session.commit()
                job = await repository.get(job_id)
                assert job is not None
        if run is not None:
            if job is None:
                raise JobNotFoundError(f"Job {job_id} no longer exists.")
            await self.start_or_enqueue(job, override_prompt=instruction)
            return job
        return await _recover_active_job_impl(self, job_id, instruction=instruction)

    async def _rollback_recovery(self, job_id: str, snapshot: RecoverySnapshot) -> None:
        """Restore job state after a failed recovery attempt."""
        await _rollback_recovery_impl(self, job_id, snapshot)

    async def _start_job(
        self, job: Job, override_prompt: str | None = None, resume_sdk_session_id: str | None = None
    ) -> None:
        if self._shutting_down:
            raise StateConflictError("The runtime is shutting down and cannot start another Job.")
        current = asyncio.current_task()
        async with self._execution_control.hold(job.id):
            if job.id in self._tasks or job.id in self._starting_tasks:
                return
            if current is None:
                raise RuntimeError("Job admission requires an owning asyncio task.")
            self._starting_tasks[job.id] = current
        recovery = self._legacy_recoveries.get(job.id)
        handoff = self._legacy_handoffs.get(job.id)
        try:
            await self._start_reserved_job(job, override_prompt, resume_sdk_session_id)
        finally:
            if self._starting_tasks.get(job.id) is current:
                self._starting_tasks.pop(job.id, None)
            if job.id not in self._tasks:
                self._agent_sessions.pop(job.id, None)
            self._queued_session_tokens.pop(job.id, None)
            if self._legacy_recoveries.get(job.id) is recovery:
                self._legacy_recoveries.pop(job.id, None)
            if self._legacy_handoffs.get(job.id) is handoff:
                self._legacy_handoffs.pop(job.id, None)

    async def _start_reserved_job(
        self, job: Job, override_prompt: str | None = None, resume_sdk_session_id: str | None = None
    ) -> None:
        """Create an asyncio task to execute the job."""
        IntegrationPolicy(self._config).require_enabled(job.sdk)
        if job.id in self._tasks:
            return  # Already running (in-memory guard)

        # DB-level compare-and-swap: prevents double-start if recovery and
        # an HTTP request race on the same job.  Only the winner proceeds.
        async with self._session_factory() as session:
            from backend.persistence.recipe_run_repo import RecipeRunRepository

            repo = JobRepository(session)
            claimed = await repo.claim_for_start(job.id)
            recipe_run = await RecipeRunRepository(session).get_for_job(job.id)
            await session.commit()
        if not claimed:
            log.warning("job_start_claim_lost", job_id=job.id)
            return

        agent_session = AgentSession()
        starting_task = asyncio.current_task()
        if starting_task is not None:
            self._starting_tasks[job.id] = starting_task
        async with self._execution_control.hold(job.id):
            self._agent_sessions[job.id] = agent_session

        # The DB CAS already set the state to running; publish the event
        # if the domain object's state hasn't caught up yet.
        if job.state != JobState.running:
            await self._publish_state_event(job.id, job.state, JobState.running)

        restored_handoff: PendingHandoff | None = None
        try:
            if recipe_run is not None and (not job.worktree_path or not Path(job.worktree_path).is_dir()):
                raise StateConflictError("Recipe execution requires the Job's existing prepared worktree.")
            session_config = build_session_config(
                job,
                self._config,
            )
            if override_prompt is not None:
                session_config = dataclass_replace(session_config, prompt=override_prompt)
            if resume_sdk_session_id is not None:
                session_config = dataclass_replace(session_config, resume_sdk_session_id=resume_sdk_session_id)
            if recipe_run is not None:
                async with self._session_factory() as session:
                    recovering = await RecipeRunRepository(session).has_attempts(recipe_run.id)
                self._recipe_contract_pins[job.id] = await self.prepare_execution_contract(
                    job.id,
                    recipe_run.id,
                    recovery=recovering,
                )

            if recipe_run is None:
                pending_intent = await self._prepare_legacy_execution_contract(job.id)
                if pending_intent is not None and job.id in self._legacy_recoveries:
                    if job.session_count != pending_intent.target_session_number:
                        raise StateConflictError("Pending admission summary numbering differs from its owning Job")
                    session_config = dataclass_replace(
                        session_config,
                        prompt=pending_intent.dispatch_prompt or pending_intent.instruction,
                        resume_sdk_session_id=pending_intent.resume_sdk_session_id,
                    )
                    if pending_intent.handoff_required:
                        from backend.services.runtime.handoff import PendingHandoff

                        assert pending_intent.source_session_number is not None
                        pin = self._job_contract_pins.get(job.id)
                        restored_handoff = PendingHandoff(
                            pin.admission_id if pin is not None else None,
                            pending_intent.instruction,
                            pending_intent.source_session_number,
                            pending_intent.target_session_number,
                        )
                        self._legacy_handoffs[job.id] = restored_handoff

            # Plan mode: wrap the prompt for the planning session
            if (
                recipe_run is None
                and job.mode == JobMode.plan
                and override_prompt is None
                and resume_sdk_session_id is None
            ):
                from backend.services.runtime.plan_mode import build_planning_prompt

                session_config = dataclass_replace(
                    session_config, prompt=build_planning_prompt(job), session_kind="planning"
                )

            # --- Wire action policy router (mandatory) ---
            # Must run BEFORE any sessions (preflight, sidecar, main) so that
            # all tool-permission checks have the approval plumbing present.
            await self._emit_setup_progress(job.id, "configuring_policy")
            await self._setup_action_policy(
                job.id,
                session_config,
                job.execution_workspace,
                job_preset=job.preset or "supervised",
                job_prompt=job.prompt or "",
                repo=job.execution_repo,
            )
            session_config = await self._complete_legacy_handoff(job, session_config)
            async with self._recipe_runner_slots:
                await self._activate_job_sidecars(job)

            await self._emit_setup_progress(job.id, "exploring_codebase")
            if recipe_run is None and self._preflight_curator is not None:
                async with self._session_factory() as session:
                    before_scout = await JobRepository(session).get(job.id)
                if before_scout and before_scout.state in TERMINAL_STATES:
                    self._agent_sessions.pop(job.id, None)
                    return

                async def scoped_scout() -> SessionConfig:
                    async with self._recipe_runner_slots:
                        return await self._run_preflight_curator(job, session_config)

                scout_task = asyncio.create_task(scoped_scout())
                self._scout_tasks[job.id] = scout_task
                try:
                    session_config = await scout_task
                finally:
                    self._scout_tasks.pop(job.id, None)

            # If the job was canceled during preflight, don't start the main session.
            async with self._session_factory() as session:
                _job_check = await JobRepository(session).get(job.id)
            if _job_check and _job_check.state in TERMINAL_STATES:
                log.info("job_start_aborted_after_preflight", job_id=job.id, state=_job_check.state)
                self._agent_sessions.pop(job.id, None)
                self._starting_tasks.pop(job.id, None)
                return

            await self._emit_setup_progress(job.id, "starting_agent")

            async with self._session_factory() as session:
                current = await JobRepository(session).get(job.id)
            if self._shutting_down or (current and current.state in TERMINAL_STATES):
                self._agent_sessions.pop(job.id, None)
                return

            task = asyncio.create_task(
                self._run_job_guarded(job.id, agent_session, session_config, session_number=job.session_count),
                name=f"job-{job.id}",
            )
        except asyncio.CancelledError:
            if restored_handoff is not None and self._legacy_handoffs.get(job.id) is restored_handoff:
                self._legacy_handoffs.pop(job.id, None)
            self._recipe_contract_pins.pop(job.id, None)
            self._agent_sessions.pop(job.id, None)
            self._starting_tasks.pop(job.id, None)
            if self._approval_service is not None:
                await self._approval_service.cleanup_job(job.id, preserve_pending=self._shutting_down)
            if self._sidecar_sessions is not None:
                self._sidecar_sessions.close_job(job.id)
            raise
        except Exception:
            # Task creation failed after the DB CAS set state to running.
            # Revert to the pre-claim state so the job isn't orphaned.
            if restored_handoff is not None and self._legacy_handoffs.get(job.id) is restored_handoff:
                self._legacy_handoffs.pop(job.id, None)
            self._agent_sessions.pop(job.id, None)
            self._recipe_contract_pins.pop(job.id, None)
            self._starting_tasks.pop(job.id, None)
            log.error("job_start_task_creation_failed", job_id=job.id, exc_info=True)
            async with self._session_factory() as session:
                repo = JobRepository(session)
                current = await repo.get(job.id)
                if current is not None and current.state == JobState.running:
                    await repo.update_state(job.id, job.state, datetime.now(UTC))
                await session.commit()
            raise
        self._tasks[job.id] = task
        self._starting_tasks.pop(job.id, None)
        # Cache per-job behavior toggles
        if job.enable_stall_detection is False:
            self._stall_detection_disabled.add(job.id)
        if job.enable_plan_tracking is False and self._trail_service is not None:
            self._trail_service.disable_plan_tracking(job.id)
        # Pre-register prompt for echo suppression so the SDK user.message
        # echo of the initial prompt is discarded (shown via the synthetic entry).
        self._echo_suppress.setdefault(job.id, set()).add(session_config.prompt)
        log.info("job_started", job_id=job.id)

    async def _run_job_guarded(
        self,
        job_id: str,
        agent_session: AgentSession,
        config: SessionConfig,
        session_number: int = 1,
    ) -> None:
        """Wrapper that guarantees ``_cleanup_job_state`` runs even when
        ``CancelledError`` hits before the inner try/except in ``_run_job``."""
        try:
            from backend.persistence.recipe_run_repo import RecipeRunRepository

            async with self._session_factory() as session:
                recipe_run = await RecipeRunRepository(session).get_for_job(job_id)
            if recipe_run is not None:
                from backend.services.recipe.executor import RecipeExecutor

                job = await self._get_job(job_id)
                if job is None:
                    raise JobNotFoundError(f"Job {job_id} no longer exists.")
                try:
                    if job.id not in self._recipe_contract_pins:
                        raise StateConflictError("Recipe execution has no prepared admission contract.")
                    controller = RecipeExecutor(
                        self,
                        job,
                        config,
                        contract_pin=self._recipe_contract_pins[job.id],
                    )
                    self._recipe_controllers[job_id] = controller
                    try:
                        await controller.execute()
                    finally:
                        if self._recipe_controllers.get(job_id) is controller:
                            self._recipe_controllers.pop(job_id, None)
                except Exception as exc:
                    log.error("recipe_execution_failed", job_id=job_id, exc_info=True)
                    await self._fail_job(job_id, str(exc))
            else:
                await self._recipe_runner_slots.acquire()
                self._legacy_runner_slots.add(job_id)
                await self._run_job(job_id, agent_session, config, session_number=session_number)
        except asyncio.CancelledError:
            if self._shutting_down:
                log.info("shutdown_task_cancelled", job_id=job_id)
            else:
                await self._cancel_safety_net(job_id)
        finally:
            log.debug("_run_job_guarded_finally", job_id=job_id, in_tasks=job_id in self._tasks)
            # The inner _run_job finally handles cleanup in the normal case.
            # This catches the case where CancelledError hit during setup,
            # before the inner try was entered.
            if job_id in self._tasks:
                heartbeat = self._heartbeat_tasks.pop(job_id, None)
                if heartbeat:
                    heartbeat.cancel()
                await self._cleanup_job_state(job_id)

    async def _cancel_safety_net(self, job_id: str) -> None:
        """Last-resort cancel handler when CancelledError escapes ``_run_job``.

        Clears task-level cancellation, then attempts to transition the job to
        ``canceled`` in the DB so it doesn't stay stuck in ``running``.
        """
        log.info("job_canceled_safety_net", job_id=job_id)
        _cur = asyncio.current_task()
        if _cur is not None:
            _cur.uncancel()
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                current = await svc.get_job(job_id)
                if current and current.state not in TERMINAL_STATES:
                    await svc.transition_state(job_id, JobState.canceled)
                    await session.commit()
        except (Exception, asyncio.CancelledError):
            log.error("safety_net_cancel_failed", job_id=job_id, exc_info=True)

    async def _run_job(
        self,
        job_id: str,
        agent_session: AgentSession,
        config: SessionConfig,
        session_number: int = 1,
    ) -> None:
        """Execute the agent session, translate events, and handle completion."""
        import time

        self._last_activity[job_id] = time.monotonic()
        _job_wall_start = time.monotonic()  # captured here so adapter cleanup can't erase it
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id),
            name=f"heartbeat-{job_id}",
        )
        self._heartbeat_tasks[job_id] = heartbeat_task

        # Plan tracking is now handled entirely by TrailService's EventBus
        # subscriber (handle_event) — no explicit start_tracking call needed.

        # Start telemetry tracking — init OTEL spans and SQLite summary row.
        from backend.services.analytics import telemetry as tel

        tel.start_job_span(job_id, sdk=config.sdk, model=config.model or "")

        asyncio.create_task(self._telemetry.init_telemetry_row(job_id, config), name=f"telemetry-init-{job_id[:8]}")

        # Emit environment_setup phase
        self._resolve_adapter(config.sdk).set_execution_phase(job_id, ExecutionPhase.environment_setup)
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.execution_phase_changed,
                payload={"phase": ExecutionPhase.environment_setup},
            )
        )

        # Resolve worktree_path and base_ref for diff calculations
        worktree_path: str | None = None
        base_ref: str | None = None
        post_conflict_merge_requested = False
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                job = await svc.get_job(job_id)
            if job is not None:
                worktree_path = job.execution_workspace
                base_ref = job.execution_base_ref
                post_conflict_merge_requested = job.merge_status == GitMergeOutcome.conflict
        except DBAPIError:
            log.warning("diff_job_lookup_failed", job_id=job_id, exc_info=True)

        if worktree_path and self._step_tracker is not None:
            self._step_tracker.register_worktree(job_id, worktree_path)

        session_id: str | None = None
        error_reason: str | None = None
        try:
            # Emit agent_reasoning phase before main session execution
            self._resolve_adapter(config.sdk).set_execution_phase(job_id, ExecutionPhase.agent_reasoning)
            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=EventKind.execution_phase_changed,
                    payload={"phase": ExecutionPhase.agent_reasoning},
                )
            )

            result = await self._execute_session_attempt(
                job_id,
                agent_session,
                config,
                worktree_path,
                base_ref,
                session_number=session_number,
            )
            session_id = result.session_id
            error_reason = result.error_reason

            # Resume fallback: first attempt errored without progress on a resumed session
            if error_reason and config.resume_sdk_session_id and not result.made_progress:
                result = await self._attempt_resume_fallback(
                    job_id,
                    config,
                    worktree_path,
                    base_ref,
                    session_number=session_number,
                )
                session_id = result.session_id
                error_reason = result.error_reason

            # Model downgrade (from either attempt): finish diff, move to review with note, skip verify
            if result.downgrade is not None:
                await self._handle_model_downgrade(job_id, result.downgrade, worktree_path, base_ref)
                return

            if error_reason:
                # An error event was received during execution — finalize diff before failing
                log.warning("job_error_reason_detected", job_id=job_id, error_reason=error_reason)
                await self._finalize_diff_safe(job_id, worktree_path, base_ref)
                await self._fail_job(job_id, error_reason)
                return

            # --- Plan mode: planning session completed → approval gate ---
            # job.mode is persisted: "plan" = planning phase, "plan_implementing" = implementation phase.
            if job is not None and job.mode == JobMode.plan:
                plan_result = await self._handle_plan_session_completed(
                    job_id,
                    job,
                    agent_session,
                    config,
                    worktree_path,
                    base_ref,
                    session_number,
                )
                if plan_result is not None:
                    return  # plan mode handled the rest (implementation or re-plan)

            await self._handle_successful_completion(
                job_id,
                config,
                session_id,
                worktree_path,
                base_ref,
                post_conflict_merge_requested,
                session_number,
            )
        except asyncio.CancelledError:
            if self._shutting_down:
                # Server is shutting down — leave job state as-is so
                # recover_on_startup picks it back up on next launch.
                log.info("job_interrupted_by_shutdown", job_id=job_id)
                await self._finalize_diff_safe(job_id, worktree_path, base_ref)
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await agent_session.abort()
            else:
                log.info("job_canceled_by_operator", job_id=job_id)
                await self._handle_job_canceled(job_id, agent_session, worktree_path, base_ref)
        except Exception as exc:
            log.error("job_execution_failed", job_id=job_id, exc_info=True)
            # Finalize diff so changes are preserved even for crashed jobs
            await self._finalize_diff_safe(job_id, worktree_path, base_ref)
            await self._fail_job(job_id, f"Execution error: {exc}")
        finally:
            await self._telemetry.finalize_job_telemetry(job_id, _job_wall_start, config)
            heartbeat_task.cancel()
            self._heartbeat_tasks.pop(job_id, None)
            if self._trail_service is not None:
                self._trail_service.stop_tracking(job_id)
                persisted = await self._get_job(job_id)
                succeeded = persisted is not None and persisted.state in (JobState.completed, JobState.review)
                await self._trail_service.finalize(job_id, succeeded=succeeded)
            await self._cleanup_job_state(job_id)

    async def _init_telemetry_row(self, job_id: str, config: SessionConfig) -> None:
        await self._telemetry.init_telemetry_row(job_id, config)

    async def _handle_model_downgrade(
        self,
        job_id: str,
        downgrade: tuple[str, str],
        worktree_path: str | None,
        base_ref: str | None,
    ) -> None:
        requested, actual = downgrade
        await self._finalize_diff_safe(job_id, worktree_path, base_ref)
        reason = f"Model downgraded: requested {requested} but received {actual}"
        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            await svc.transition_state(job_id, JobState.review, failure_reason=reason)
            job_repo = JobRepository(session)
            await job_repo.update_resolution(job_id, Resolution.unresolved)
            await session.commit()

        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.job_review,
                payload={
                    "resolution": Resolution.unresolved,
                    "model_downgraded": True,
                    "requested_model": requested,
                    "actual_model": actual,
                },
            )
        )
        log.info("job_moved_to_review_model_downgrade", job_id=job_id)

    async def _handle_successful_completion(
        self,
        job_id: str,
        config: SessionConfig,
        session_id: str | None,
        worktree_path: str | None,
        base_ref: str | None,
        post_conflict_merge_requested: bool,
        session_number: int,
    ) -> JobState:
        # Final diff snapshot before resolution
        await self._finalize_diff_safe(job_id, worktree_path, base_ref)

        # Run optional verify / self-review follow-up turns
        await self._run_verify_review(
            job_id, config, session_id, worktree_path, base_ref, session_number=session_number
        )

        final_resolution = Resolution.unresolved
        final_pr_url: str | None = None
        final_merge_status: str | None = None
        resolution_event = None

        # Strategy completed normally → review
        #
        # Commit the state transition BEFORE running merge resolution.
        # Merge operations open their own sessions to persist merge_status
        # and publish events — if the outer session is still uncommitted
        # SQLite will deadlock on the jobs table write lock.
        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            await svc.transition_state(job_id, JobState.review)
            if not post_conflict_merge_requested or self._merge_service is None:
                job_repo = JobRepository(session)
                await job_repo.update_resolution(job_id, final_resolution, pr_url=None)
                if post_conflict_merge_requested and self._merge_service is None:
                    log.warning("post_conflict_merge_unavailable", job_id=job_id)
            await session.commit()

        # Merge resolution runs in its own session(s) — no lock contention.
        if post_conflict_merge_requested and self._merge_service is not None:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                current_job = await svc.get_job(job_id)
                if current_job is None:
                    raise JobNotFoundError(f"Job {job_id} not found before post-conflict merge")

                log.info("job_attempting_post_conflict_merge", job_id=job_id)
                resolved, final_pr_url, _, _ = await svc.execute_resolve(
                    job=current_job,
                    action="merge",
                    merge_service=self._merge_service,
                )
                final_resolution = resolved
                resolution_event = svc.build_job_resolved_event(
                    job_id,
                    resolved,
                    pr_url=final_pr_url,
                )
                await session.commit()

        if resolution_event is not None:
            await self._event_bus.publish(resolution_event)

        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            updated_job = await svc.get_job(job_id)
        if updated_job is not None:
            final_merge_status = updated_job.merge_status
            final_pr_url = updated_job.pr_url

        if final_resolution == Resolution.unresolved:
            log.info("job_awaiting_review", job_id=job_id)
        else:
            log.info(
                "job_completed_with_resolution",
                job_id=job_id,
                resolution=final_resolution,
                merge_status=final_merge_status,
            )

        # Determine final state — execute_resolve may have already
        # transitioned review → completed for successful merges.
        final_state = JobState.review
        if final_resolution in (Resolution.merged, Resolution.pr_created, Resolution.discarded):
            final_state = JobState.completed
        final_event_kind = EventKind.job_completed if final_state == JobState.completed else EventKind.job_review

        await self._set_step_terminal_state(job_id, final_state)
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=final_event_kind,
                payload={
                    "resolution": final_resolution,
                    "merge_status": final_merge_status,
                    "pr_url": final_pr_url,
                },
            )
        )
        log.info(
            final_event_kind.value,
            job_id=job_id,
            resolution=final_resolution,
            merge_status=final_merge_status,
        )
        return final_state

    async def _finalize_job_telemetry(self, job_id: str, wall_start: float, config: SessionConfig) -> None:
        await self._telemetry.finalize_job_telemetry(job_id, wall_start, config)

    async def _set_step_terminal_state(self, job_id: str, outcome: str) -> None:
        """Forward terminal outcome to the event processor (enricher + title flush)."""
        if self._event_processor is not None:
            await self._event_processor.on_job_terminal(job_id, outcome)
        elif self._step_tracker is not None:
            # Fallback if event processor not wired (shouldn't happen in prod)
            await self._step_tracker.on_job_terminal(job_id, outcome)

    async def _setup_action_policy(
        self,
        job_id: str,
        config: SessionConfig,
        worktree_path: str | None,
        job_preset: str = "supervised",
        job_prompt: str = "",
        repo: str | None = None,
    ) -> None:
        """Load action policy from DB and wire the PolicyRouter into the adapter.

        The policy router is mandatory — if setup fails, the exception propagates
        and the job will fail.  There is no legacy fallback path.
        """
        from backend.persistence.policy_repo import PolicyRepository
        from backend.services.action_policy.batcher import ApprovalBatcher
        from backend.services.action_policy.checkpoint_service import CheckpointService
        from backend.services.action_policy.classifier import Preset, RepoPolicy
        from backend.services.action_policy.monitor import MonitorSession
        from backend.services.action_policy.router import PolicyRouter

        if self._governance_decider is None and self._standalone_governance is None:
            raise RuntimeError("governance decider not wired — set_governance() must run at startup")

        async with self._session_factory() as session:
            policy_repo = PolicyRepository(session)
            db_config = await policy_repo.get_config()
            mcp_configs_list = await policy_repo.list_mcp_configs()

        # Build MCP config lookup: name → server config dict
        mcp_configs = {c["name"]: c for c in mcp_configs_list}

        # Per-job preset overrides the global policy config preset
        effective_preset = job_preset

        # Build in-memory policy object. The retired path/action/cost rule tables
        # are gone — governance classifies natively; CodePlane keeps only the
        # preset (which selects the TraceForge profile) + batch window + MCP hints.
        policy = RepoPolicy(
            preset=Preset(effective_preset),
            batch_window_seconds=db_config["batch_window_seconds"],
            mcp_configs=mcp_configs,
        )

        if self._standalone_governance is not None:
            pin = self._recipe_contract_pins.get(job_id) or self._job_contract_pins.get(job_id)
            if pin is None:
                raise StateConflictError("Standalone policy requires the exact durable admission contract")
            standalone = self._standalone_governance.for_job(pin, policy)
            adapter = self._resolve_adapter(config.sdk)
            adapter.set_policy_router(standalone, policy, job_id, worktree_path or "")  # type: ignore[attr-defined]
            if self._preflight_curator is not None and self._preflight_curator.adapter is not adapter:
                self._preflight_curator.adapter.set_policy_router(  # type: ignore[attr-defined]
                    standalone,
                    policy,
                    job_id,
                    worktree_path or "",
                )
            self._policy_routers[job_id] = standalone
            self._repo_policies[job_id] = policy
            return

        # Create router components
        if self._git_service is None:
            return
        checkpoint_svc = CheckpointService(self._git_service)
        batcher = ApprovalBatcher(
            event_bus=self._event_bus,
            batch_window_seconds=db_config["batch_window_seconds"],
        )

        # Bind this job to its preset so the shared decider's decision (read) and
        # accrual (write) paths select the right preset pipeline.
        assert self._governance_decider is not None
        self._governance_decider.register_job(job_id, Preset(effective_preset))

        # Create monitor for non-locked presets
        monitor: MonitorSession | None = None
        if effective_preset != "locked" and worktree_path:
            from backend.persistence.trail_repo import TrailNodeRepository

            trail_repo = TrailNodeRepository(self._session_factory)
            from backend.services.adapters.agent_adapter import UtilityCompletionError
            from backend.services.completers.lightweight_completer import LightweightCompleter

            try:
                provider = self._adapter_registry.get_completion_provider(
                    self._config.runtime.utility_sdk,
                )
            except (IntegrationDisabledError, UtilityCompletionError) as exc:
                log.warning("policy_utility_provider_unavailable", job_id=job_id, reason=str(exc))
            else:
                completer = LightweightCompleter(provider, model=self._config.runtime.utility_model)
                monitor = MonitorSession(
                    job_id=job_id,
                    job_prompt=job_prompt,
                    worktree=worktree_path,
                    repo=repo,
                    completer=completer,
                    trail_repo=trail_repo,
                )

        router = PolicyRouter(
            checkpoint_service=checkpoint_svc,
            governance=self._governance_decider,
            batcher=batcher,
            monitor=monitor,
        )

        # Wire into adapter
        adapter = self._resolve_adapter(config.sdk)
        adapter.set_policy_router(router, policy, job_id, worktree_path or "")  # type: ignore[attr-defined]
        if self._preflight_curator is not None and self._preflight_curator.adapter is not adapter:
            self._preflight_curator.adapter.set_policy_router(router, policy, job_id, worktree_path or "")  # type: ignore[attr-defined]
        self._policy_routers[job_id] = router
        self._repo_policies[job_id] = policy
        self._policy_batchers[job_id] = batcher

        log.info(
            "action_policy_configured",
            job_id=job_id,
            preset=effective_preset,
        )

    async def _on_policy_settings_changed(self, event: SessionEvent) -> None:
        """Reload action policy for all running jobs when settings change."""
        if event.kind != EventKind.policy_settings_changed:
            return

        job_ids = list(self._policy_routers.keys())
        if not job_ids:
            return

        log.info("policy_settings_changed_reloading", job_count=len(job_ids))

        try:
            from backend.persistence.job_repo import JobRepository
            from backend.persistence.policy_repo import PolicyRepository
            from backend.services.action_policy.classifier import Preset, RepoPolicy

            async with self._session_factory() as session:
                repo = PolicyRepository(session)
                db_config = await repo.get_config()
                mcp_configs_list = await repo.list_mcp_configs()

                # Look up per-job presets
                job_repo = JobRepository(session)
                job_presets: dict[str, str] = {}
                for jid in job_ids:
                    job = await job_repo.get(jid)
                    if job is not None:
                        job_presets[jid] = job.preset

            mcp_configs = {c["name"]: c for c in mcp_configs_list}

            # Global governance settings (USD ceilings / budget config) may have
            # changed → refresh the per-preset ceilings then rebuild the pipelines
            # once, atomically, over the same durable store. A concurrent
            # decide/observe never sees a half-built set (the swap is a single
            # locked rebind).
            if self._governance_decider is not None:
                from backend.services.action_policy.governance import load_usd_ceilings

                self._governance_decider.set_usd_ceilings(await load_usd_ceilings(self._session_factory))
                self._governance_decider.rebuild()

            for job_id in job_ids:
                if job_id not in self._policy_routers:
                    continue  # job finished between iteration start and now

                # Use per-job preset, fall back to global
                effective_preset = job_presets.get(job_id, db_config["preset"])

                new_policy = RepoPolicy(
                    preset=Preset(effective_preset),
                    batch_window_seconds=db_config["batch_window_seconds"],
                    mcp_configs=mcp_configs,
                )
                self._repo_policies[job_id] = new_policy

                # Re-bind the job's preset on the shared decider (it may have
                # changed), so subsequent decisions select the right pipeline.
                if self._governance_decider is not None:
                    self._governance_decider.register_job(job_id, Preset(effective_preset))

                # Disable/enable monitor based on preset change
                router = self._policy_routers[job_id]
                if effective_preset == "locked" and router._monitor is not None:
                    log.info("monitor_disabled_preset_locked", job_id=job_id)
                    router._monitor = None

                # Update batcher window if changed
                batcher = self._policy_batchers.get(job_id)
                if batcher is not None and hasattr(batcher, "set_batch_window"):
                    batcher.set_batch_window(db_config["batch_window_seconds"])
                # Update policy in all registered adapters (only one will have the job)
                for adapter in self._adapter_registry._adapters.values():
                    if hasattr(adapter, "update_repo_policy"):
                        adapter.update_repo_policy(job_id, new_policy)

            log.info("policy_reloaded_for_jobs", job_count=len(job_ids))
        except Exception:
            log.warning("policy_reload_failed", exc_info=True)

    async def _cleanup_job_state(self, job_id: str) -> None:
        """Remove all per-job in-memory state and trigger post-job hooks."""
        if job_id in self._legacy_runner_slots:
            self._legacy_runner_slots.remove(job_id)
            self._recipe_runner_slots.release()
        self._queued_session_tokens.pop(job_id, None)
        self._job_contract_pins.pop(job_id, None)
        self._legacy_recoveries.pop(job_id, None)
        self._legacy_handoffs.pop(job_id, None)
        self._recipe_contract_pins.pop(job_id, None)
        # Last-resort guard: if the job is still non-terminal after all error
        # handlers have run, force it to failed so it doesn't stay stuck.
        await self._ensure_terminal_state(job_id)
        await self._stop_attempt_executions(job_id)
        await self._finalize_artifacts_safe(job_id)

        if self._trail_service is not None:
            self._trail_service.cleanup(job_id)
        if self._step_tracker is not None:
            self._step_tracker.cleanup(job_id)
        if self._event_processor is not None:
            self._event_processor.cleanup(job_id)
        if self._telemetry_subscriber is not None:
            self._telemetry_subscriber.cleanup(job_id)
        if self._governance_subscriber is not None:
            self._governance_subscriber.cleanup(job_id)
        self._tasks.pop(job_id, None)
        async with self._execution_control.hold(job_id):
            self._agent_sessions.pop(job_id, None)
        self._last_activity.pop(job_id, None)
        self._active_tool.pop(job_id, None)
        self._stall_check_pending.discard(job_id)
        self._last_stall_check.pop(job_id, None)
        self._stall_detection_disabled.discard(job_id)
        self._stall_check_consecutive_failures.pop(job_id, None)
        self._waiting_for_approval.discard(job_id)
        self._session_ids.pop(job_id, None)
        # Clean up action policy router state
        router = self._policy_routers.pop(job_id, None)
        self._repo_policies.pop(job_id, None)
        if router is not None:
            router.cleanup_job(job_id)
        # Unbind the job from the shared governance decider so a finished job's
        # preset no longer resolves. Accrued durable state is TTL-boxed and
        # session-keyed, so it need not be revoked here.
        if self._governance_decider is not None:
            self._governance_decider.unregister_job(job_id)
        self._policy_batchers.pop(job_id, None)
        # Clean up adapter-side policy state (survives session cleanup for retries)
        for adapter in self._adapter_registry._adapters.values():
            if hasattr(adapter, "cleanup_job_policy"):
                adapter.cleanup_job_policy(job_id)
        self._echo_suppress.pop(job_id, None)
        self._active_gates.pop(job_id, None)
        self._pending_starts.pop(job_id, None)
        self._queued_override_prompts.pop(job_id, None)
        self._queued_resume_session_ids.pop(job_id, None)
        if self._sidecar_dispatcher is not None:
            try:
                await self._sidecar_dispatcher.run_postflight(job_id)
            except Exception:
                log.warning("sidecar_postflight_failed", job_id=job_id, exc_info=True)
            try:
                await self._sidecar_dispatcher.deactivate(job_id)
            except Exception:
                log.warning("sidecar_dispatcher_deactivate_failed", job_id=job_id, exc_info=True)
        if self._sidecar_sessions is not None:
            try:
                self._sidecar_sessions.close_job(job_id)
            except (OSError, RuntimeError):
                log.warning("sidecar_session_close_failed", job_id=job_id, exc_info=True)
        if self._approval_service is not None:
            await self._approval_service.cleanup_job(job_id, preserve_pending=self._shutting_down)
        if self._diff_service is not None:
            self._diff_service.cleanup(job_id)
        await self._dequeue_next()

    async def _ensure_terminal_state(self, job_id: str) -> None:
        """Ensure the job is not stuck in an in-flight state.  Called as a
        last-resort safety net during cleanup so that no job is ever
        permanently stuck in 'running' or 'waiting_for_approval'.

        'review' is intentionally excluded — it is a valid resting state
        where the agent has finished and the job awaits operator action.

        During server shutdown, jobs are intentionally left as-is so that
        ``recover_on_startup`` can resume them on the next launch.
        """
        if self._shutting_down:
            return
        # Only force-fail jobs that are truly in-flight.  'review' and
        # terminal states are fine.
        stuck_states = frozenset({JobState.running, JobState.waiting_for_approval})
        # Clear any pending task-level cancellation so the DB transition
        # below is not immediately interrupted.
        _cur = asyncio.current_task()
        if _cur is not None:
            _cur.uncancel()
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                job = await svc.get_job(job_id)
                if job is not None and job.state in stuck_states:
                    log.error(
                        "ensure_terminal_state_forcing_failure",
                        job_id=job_id,
                        current_state=str(job.state),
                    )
                    await svc.transition_state(
                        job_id,
                        JobState.failed,
                        failure_reason="Job cleanup: forced to failed (previous state transitions failed)",
                    )
                    await session.commit()
                    await self._set_step_terminal_state(job_id, JobState.failed)
                    await self._event_bus.publish(
                        new_event(
                            session_id=job_id,
                            timestamp=datetime.now(UTC),
                            kind=EventKind.job_failed,
                            payload={"reason": "Job cleanup: previous error handlers failed to transition state"},
                        )
                    )
        except (Exception, asyncio.CancelledError):
            log.error("ensure_terminal_state_failed", job_id=job_id, exc_info=True)

    async def _handle_approval_request(
        self,
        job_id: str,
        domain_event: SessionEvent,
        rejection_message: str,
    ) -> ApprovalResolution:
        """Handle an approval_requested event: transition state, wait for operator, return resolution."""
        import time

        if self._approval_service is None:
            raise ServiceInitError("approval_service must be set before handling approvals")

        async with self._session_factory() as sess:
            svc = self._make_job_service(sess)
            await svc.transition_state(job_id, JobState.waiting_for_approval)
            await sess.commit()

        self._waiting_for_approval.add(job_id)

        await self._event_bus.publish(domain_event)

        approval_id = str(domain_event.payload.get("approval_id", ""))
        resolution = await self._approval_service.wait_for_resolution(approval_id)

        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.approval_resolved,
                payload={
                    "approval_id": approval_id,
                    "resolution": resolution,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        )

        self._waiting_for_approval.discard(job_id)

        if resolution == ApprovalResolution.rejected:
            # Leave job in waiting_for_approval — the caller will fail it
            # via _fail_job which handles the waiting_for_approval → failed
            # transition.  Do NOT transition to running first.
            return resolution

        async with self._session_factory() as sess:
            svc = self._make_job_service(sess)
            await svc.transition_state(job_id, JobState.running)
            await sess.commit()
        await self._publish_state_event(job_id, JobState.waiting_for_approval, JobState.running)
        self._last_activity[job_id] = time.monotonic()

        return resolution

    async def _attempt_resume_fallback(
        self,
        job_id: str,
        config: SessionConfig,
        worktree_path: str | None,
        base_ref: str | None,
        session_number: int = 1,
    ) -> SessionAttemptResult:
        """Try a fresh session after a failed resume."""
        return await _attempt_resume_fallback_impl(
            self,
            job_id,
            config,
            worktree_path,
            base_ref,
            session_number=session_number,
        )

    async def _handle_job_canceled(
        self,
        job_id: str,
        agent_session: AgentSession,
        worktree_path: str | None,
        base_ref: str | None,
    ) -> None:
        """Process cancellation: finalize diff, abort agent, transition state."""
        try:
            await self._finalize_diff_safe(job_id, worktree_path, base_ref)
        except (Exception, asyncio.CancelledError):
            log.warning("cancel_diff_finalize_failed", job_id=job_id, exc_info=True)
        try:
            await agent_session.abort()
        except (Exception, asyncio.CancelledError):
            log.warning("agent_abort_failed", job_id=job_id, exc_info=True)
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                current = await svc.get_job(job_id)
                if current and current.state not in TERMINAL_STATES:
                    await svc.transition_state(job_id, JobState.canceled)
                    await session.commit()
                    await self._set_step_terminal_state(job_id, JobState.canceled)
                    await self._event_bus.publish(
                        new_event(
                            session_id=job_id,
                            timestamp=datetime.now(UTC),
                            kind=EventKind.job_canceled,
                            payload={"reason": "operator_cancel"},
                        )
                    )
                else:
                    await session.commit()
        except (Exception, asyncio.CancelledError):
            log.warning("job_cancel_transition_failed", job_id=job_id, exc_info=True)

    # ------------------------------------------------------------------
    # External (imported) session pipeline
    # ------------------------------------------------------------------

    async def register_external_session(
        self,
        job_id: str,
        worktree_path: str,
        base_ref: str,
    ) -> None:
        """Register an externally-managed session for full pipeline processing.

        Gives imported sessions the same infrastructure as managed sessions:
        sidecar session binding, heartbeat loop (with stall detection), step
        tracker registration, and diff service worktree registration.

        Called by watchers after job creation or re-attachment.
        """
        job = await self._get_job(job_id)
        if job is not None and job.observation_only:
            raise StateConflictError("Observation-only history cannot attach execution infrastructure")
        self._last_activity[job_id] = time.monotonic()
        self._external_generations[job_id] = uuid.uuid4().hex

        # Open sidecar sessions — use configured list or fall back to built-in defaults.
        if self._sidecar_sessions is not None:
            from backend.models.domain import SIDECAR_ARBITER, SIDECAR_ENRICHER, SIDECAR_PLANNER

            names = self._config.runtime.cli_sidecars
            if names is None:
                names = [SIDECAR_ARBITER, SIDECAR_PLANNER, SIDECAR_ENRICHER]
            try:
                for name in names:
                    self._sidecar_sessions.open(job_id, name)
            except Exception:
                log.warning("external_sidecar_session_setup_failed", job_id=job_id, exc_info=True)

        # Step tracker
        if worktree_path and self._step_tracker is not None:
            self._step_tracker.register_worktree(job_id, worktree_path, managed=False)

        # Diff service
        # (EventProcessor registered worktrees for diff triggers; we do the
        # same via _process_agent_event which checks _diff_service directly.)

        # Start heartbeat → stall detection, health reporting
        if job_id not in self._heartbeat_tasks:
            task = asyncio.create_task(
                self._heartbeat_loop(job_id),
                name=f"heartbeat-ext-{job_id[:8]}",
            )
            self._heartbeat_tasks[job_id] = task
        log.debug("external_session_registered", job_id=job_id)

    async def finalize_external_session(
        self,
        job_id: str,
        *,
        worktree_path: str | None = None,
        base_ref: str | None = None,
        error_reason: str | None = None,
    ) -> None:
        """Clean up an externally-managed session through the main pipeline.

        Mirrors ``_cleanup_job_state`` for the subset of state that external
        sessions use: heartbeat, sidecar session, step tracker, trail service,
        diff service, stall detection, and trail snapshot.

        The watcher remains responsible for DB state transitions and publishing
        ``job_state_changed`` events (it is the authority on session liveness).
        """
        # Cancel heartbeat
        heartbeat = self._heartbeat_tasks.pop(job_id, None)
        if heartbeat:
            heartbeat.cancel()

        # Step tracker + enricher + title pipeline terminal notification
        outcome = "failed" if error_reason else "review"
        if self._event_processor is not None:
            await self._event_processor.on_job_terminal(job_id, outcome)
        elif self._step_tracker is not None:
            await self._step_tracker.on_job_terminal(job_id, outcome)

        # Sweep any steps still stuck in "running" in the DB
        # (handles steps that lost in-memory tracking after restarts)
        try:
            from backend.persistence.step_repo import StepRepository

            step_repo = StepRepository(self._session_factory)
            status = "completed" if not error_reason else "failed"
            closed = await step_repo.close_running_by_job(job_id, status=status, completed_at=datetime.now(UTC))
            if closed:
                log.info("finalize_swept_stuck_steps", job_id=job_id, count=closed)
        except Exception:
            log.warning("finalize_step_sweep_failed", job_id=job_id, exc_info=True)

        # Final diff snapshot — capture the worktree end-state before cleanup
        await self._finalize_diff_safe(job_id, worktree_path, base_ref)

        # TrailService finalize + cleanup
        if self._trail_service is not None:
            self._trail_service.stop_tracking(job_id)
            await self._trail_service.finalize(job_id, succeeded=error_reason is None)
        await self._finalize_artifacts_safe(job_id)
        if self._trail_service is not None:
            self._trail_service.cleanup(job_id)

        # Retry title + generate description while sidecar is still alive
        await self._finalize_naming(job_id)

        # Sidecar session cleanup (metrics snapshot + pool return)
        if self._sidecar_dispatcher is not None:
            try:
                await self._sidecar_dispatcher.run_postflight(job_id)
            except Exception:
                log.warning("sidecar_postflight_failed", job_id=job_id, exc_info=True)
            try:
                await self._sidecar_dispatcher.deactivate(job_id)
            except Exception:
                log.warning("sidecar_dispatcher_deactivate_failed", job_id=job_id, exc_info=True)
        if self._sidecar_sessions is not None:
            try:
                self._sidecar_sessions.close_job(job_id)
            except (OSError, RuntimeError):
                log.warning("sidecar_session_close_failed", job_id=job_id, exc_info=True)

        # Step tracker + diff cleanup
        if self._step_tracker is not None:
            self._step_tracker.cleanup(job_id)
        if self._diff_service is not None:
            self._diff_service.cleanup(job_id)
        if self._event_processor is not None:
            self._event_processor.cleanup(job_id)
        if self._telemetry_subscriber is not None:
            self._telemetry_subscriber.cleanup(job_id)

        # Clean up in-memory state
        self._last_activity.pop(job_id, None)
        self._active_tool.pop(job_id, None)
        self._stall_check_pending.discard(job_id)
        self._last_stall_check.pop(job_id, None)
        self._stall_check_consecutive_failures.pop(job_id, None)

        log.debug("external_session_finalized", job_id=job_id, outcome=outcome)
        self._external_generations.pop(job_id, None)

    # ------------------------------------------------------------------
    # Shared event processing
    # ------------------------------------------------------------------

    async def _process_agent_event(
        self,
        job_id: str,
        session_event: SessionEvent,
        agent_session: AgentSession | None,
        rejection_message: str,
    ) -> tuple[EventAction, str | None]:
        """Thin managed pre-step for a TF-native session event.

        Handles the managed-only concerns that must run BEFORE the shared
        ``EventProcessor`` funnel: heartbeat active-tool tracking, SDK echo
        suppression, and synchronous approval blocking.  Diff triggering,
        turn_id synthesis, and step/trail annotation are the funnel's job.

        Returns ``(action, error_reason)``:

        * **skip** – event consumed internally (approval handled, echo dropped);
          caller should ``continue``.
        * **abort** – caller should ``break``; *error_reason* explains why.
        * **publish** – caller should forward the event to the EventProcessor.
          *error_reason* is set when the event signals a failure but the loop
          should keep draining.
        """
        self._last_activity[job_id] = time.monotonic()
        kind = session_event.kind
        payload = session_event.payload

        # Track active tool call for heartbeat reporting (TF-native kinds).
        if kind == EventKind.tool_call_started:
            tool_name = str(payload.get("tool_name", payload.get("content", "")))
            tool_args = _stringify_tool_args(payload.get("arguments"))[:500]
            self._active_tool[job_id] = (tool_name, datetime.now(UTC).isoformat(), tool_args)
            # Reset stall tracking for the new tool call
            self._last_stall_check.pop(job_id, None)
        elif kind == EventKind.tool_call_completed:
            self._active_tool.pop(job_id, None)
            self._last_stall_check.pop(job_id, None)

        # Suppress SDK echoes of operator messages already published locally.
        if kind in TRANSCRIPT_KINDS and job_id in self._echo_suppress:
            content = str(payload.get("content", ""))
            if content in self._echo_suppress[job_id]:
                self._echo_suppress[job_id].discard(content)
                return EventAction.skip, None

        # Handle approval requests (managed sessions only — external/imported
        # sessions handle their own approvals and just publish for UI visibility).
        if kind == EventKind.approval_requested and self._approval_service is not None and agent_session is not None:
            resolution = await self._handle_approval_request(job_id, session_event, rejection_message)
            if resolution == ApprovalResolution.rejected:
                return EventAction.abort, rejection_message
            return EventAction.skip, None

        error_reason: str | None = None
        if kind == EventKind.job_failed:
            error_reason = str(payload.get("message", "Agent error"))

        return EventAction.publish, error_reason

    async def _execute_session_attempt(
        self,
        job_id: str,
        agent_session: AgentSession,
        config: SessionConfig,
        worktree_path: str | None,
        base_ref: str | None,
        session_number: int = 1,
    ) -> SessionAttemptResult:
        session_id: str | None = None
        error_reason: str | None = None
        made_progress = False
        downgrade: tuple[str, str] | None = None

        async for session_event in agent_session.execute(
            dataclass_replace(config, execution_epoch=self._execution_epoch),
            self._resolve_adapter(config.sdk),
        ):
            made_progress = made_progress or _session_event_counts_as_resume_progress(session_event)

            action, evt_error = await self._process_agent_event(
                job_id,
                session_event,
                agent_session,
                "Approval rejected by operator",
            )

            if action == EventAction.skip:
                continue
            if action == EventAction.abort:
                error_reason = evt_error
                break

            if evt_error:
                error_reason = evt_error
                log.warning("agent_error_event", job_id=job_id, error_reason=error_reason)

            # Session ID for return value + persistence
            if session_id is None and agent_session.session_id:
                session_id = agent_session.session_id
                self._session_ids[job_id] = session_id
                await self._persist_sdk_session_id(job_id, session_id)

            # Model downgrade: publish event, abort session, signal caller
            if session_event.kind == EventKind.model_downgraded and session_event.metadata.attempt_id is None:
                requested = str(session_event.payload.get("requested_model", ""))
                actual = str(session_event.payload.get("actual_model", ""))
                log.warning(
                    "model_downgrade_detected",
                    job_id=job_id,
                    requested=requested,
                    actual=actual,
                )
                await self._event_bus.publish(session_event)
                try:
                    await agent_session.abort()
                except Exception:
                    log.warning("agent_abort_on_downgrade_failed", job_id=job_id, exc_info=True)
                downgrade = (requested, actual)
                break

            # Tag log lines with the current session number so callers can filter
            # by session when a job has been resumed one or more times.
            if session_event.kind == EventKind.log_line_emitted:
                session_event.payload.setdefault("session_number", session_number)

            # Forward to the one funnel — diff triggering, turn_id synthesis,
            # step/trail annotation, and EventBus publishing all happen inside
            # the EventProcessor (shared with the imported ingest sources).
            if self._event_processor is not None:
                await self._event_processor.process_event(job_id, session_event, worktree_path, base_ref)
            else:
                await self._event_bus.publish(session_event)

        return SessionAttemptResult(
            session_id=session_id,
            error_reason=error_reason,
            made_progress=made_progress,
            downgrade=downgrade,
        )

    async def _run_followup_turn(
        self,
        job_id: str,
        prompt: str,
        base_config: SessionConfig,
        resume_session_id: str | None,
        worktree_path: str | None,
        base_ref: str | None,
        session_number: int = 1,
    ) -> tuple[str | None, str | None]:
        """Run a single follow-up agent turn (verify or self-review)."""
        return await _run_followup_turn_impl(
            self,
            job_id,
            prompt,
            base_config,
            resume_session_id,
            worktree_path,
            base_ref,
            session_number=session_number,
        )

    async def _run_verify_review(
        self,
        job_id: str,
        base_config: SessionConfig,
        session_id: str | None,
        worktree_path: str | None,
        base_ref: str | None,
        session_number: int = 1,
    ) -> None:
        """Run optional verify and self-review turns after the main agent session."""
        await _run_verify_review_impl(
            self,
            job_id,
            base_config,
            session_id,
            worktree_path,
            base_ref,
            session_number=session_number,
        )

    async def _heartbeat_loop(self, job_id: str) -> None:
        """Emit periodic heartbeats for session health display and stall detection."""
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)

                last = self._last_activity.get(job_id)
                if last is None:
                    return

                session_id = self._session_ids.get(job_id, "")
                now = datetime.now(UTC)
                last_activity_at = now - __import__("datetime").timedelta(seconds=time.monotonic() - last)

                payload: dict[str, Any] = {
                    "job_id": job_id,
                    "session_id": session_id,
                    "timestamp": now.isoformat(),
                    "last_activity_at": last_activity_at.isoformat(),
                }
                active = self._active_tool.get(job_id)
                if active:
                    payload["active_tool_name"] = active[0]
                    payload["active_tool_since"] = active[1]

                await self._event_bus.publish(
                    new_event(session_id=job_id, timestamp=now, kind=EventKind.session_heartbeat, payload=payload)
                )

                # --- Stall detection via sidecar session ---
                await self._check_stall(job_id)

        except asyncio.CancelledError:
            log.debug("heartbeat_loop_cancelled", job_id=job_id)

    async def _check_general_inactivity(self, job_id: str) -> None:
        """Detect stalls that happen *between* tool calls.

        ``_check_stall`` only runs its checks while a tool call is being
        tracked in ``_active_tool``. If the agent/SDK subprocess itself
        hangs (e.g. waiting on the LLM API, or a stuck pipe) with no tool
        call in flight, that hang is otherwise invisible to the whole
        stall-detection subsystem — heartbeats keep publishing, but nothing
        ever notices ``last_activity_at`` has stopped advancing. There is no
        tool-specific context to ask the arbiter about here, so once
        inactivity crosses the threshold we escalate directly.
        """
        last = self._last_activity.get(job_id)
        if job_id in self._waiting_for_approval:
            return  # legitimately paused for a human, not stalled
        if last is None:
            return
        inactive_s = time.monotonic() - last
        if inactive_s < _STALL_NO_TOOL_INACTIVITY_THRESHOLD_S:
            return
        last_check = self._last_stall_check.get(job_id, 0.0)
        if (time.monotonic() - last_check) < _STALL_RECHECK_INTERVAL_S:
            return
        self._last_stall_check[job_id] = time.monotonic()

        elapsed_human = f"{int(inactive_s // 60)}m{int(inactive_s % 60)}s"
        log.warning(
            "stall_detected_general_inactivity",
            job_id=job_id,
            elapsed=elapsed_human,
        )
        try:
            await self._handle_stall_interrupt(
                job_id,
                tool_name="(no active tool)",
                elapsed=elapsed_human,
                reason=(
                    f"the agent session has shown no activity for {elapsed_human} and is not inside a tracked tool call"
                ),
            )
        except Exception:
            log.error("stall_general_inactivity_interrupt_raised", job_id=job_id, exc_info=True)
            raise

    async def _check_stall(self, job_id: str) -> None:
        """Ask the sidecar session whether the active tool is stuck."""
        if job_id in self._stall_detection_disabled:
            return
        active = self._active_tool.get(job_id)
        if not active:
            await self._check_general_inactivity(job_id)
            return
        if self._sidecar_sessions is None:
            return
        if job_id in self._stall_check_pending:
            return  # already checking
        if job_id in self._waiting_for_approval:
            return  # tool is paused waiting for human, not stalled

        tool_name, started_iso, tool_args = active
        # Calculate how long the tool has been running
        from datetime import datetime as _dt

        try:
            started = _dt.fromisoformat(started_iso)
        except (ValueError, TypeError):
            return
        elapsed_s = (datetime.now(UTC) - started).total_seconds()

        if elapsed_s < _STALL_CHECK_THRESHOLD_S:
            return

        # Respect recheck interval
        last_check = self._last_stall_check.get(job_id, 0.0)
        if (time.monotonic() - last_check) < _STALL_RECHECK_INTERVAL_S:
            return

        # Ask the arbiter sidecar
        from backend.models.domain import SIDECAR_ARBITER

        sidecar = self._sidecar_sessions.get(job_id, SIDECAR_ARBITER)
        if sidecar is None:
            return

        self._stall_check_pending.add(job_id)
        try:
            elapsed_human = f"{int(elapsed_s // 60)}m{int(elapsed_s % 60)}s"
            prompt = _STALL_ARBITER_PROMPT.format(
                tool_name=tool_name,
                elapsed=elapsed_human,
                tool_args=tool_args[:300],
            )
            response = await sidecar.complete(prompt, timeout=15.0)
            self._last_stall_check[job_id] = time.monotonic()

            # Parse response
            import json

            try:
                verdict = json.loads(response.strip())
            except (json.JSONDecodeError, ValueError):
                log.debug("stall_check_unparseable", job_id=job_id, response=response[:200])
                await self._on_stall_check_undetermined(job_id, tool_name, elapsed_human)
                return

            # A parsed verdict — successful check, regardless of the action —
            # means the arbiter is healthy again.
            self._stall_check_consecutive_failures.pop(job_id, None)

            action = verdict.get("action", "wait")
            reason = verdict.get("reason", "")

            if action == "interrupt":
                log.info(
                    "stall_detected_interrupting",
                    job_id=job_id,
                    tool_name=tool_name,
                    elapsed=elapsed_human,
                    reason=reason,
                )
                await self._handle_stall_interrupt(job_id, tool_name, elapsed_human, reason)
            else:
                log.debug(
                    "stall_check_wait",
                    job_id=job_id,
                    tool_name=tool_name,
                    elapsed=elapsed_human,
                    reason=reason,
                )
        except (TimeoutError, OSError, RuntimeError):
            log.debug("stall_check_failed", job_id=job_id, exc_info=True)
            await self._on_stall_check_undetermined(job_id, tool_name, elapsed_human)
        finally:
            self._stall_check_pending.discard(job_id)

    async def _on_stall_check_undetermined(self, job_id: str, tool_name: str, elapsed_human: str) -> None:
        """Record an arbiter failure/unparseable-response and escalate if it recurs.

        The arbiter is a soft signal: if it can't produce a verdict we normally
        just wait and retry next interval. But if it *keeps* failing, silently
        doing nothing means a genuinely stuck tool call can sit forever with no
        operator-visible signal (no failure, no interrupt, nothing) — the exact
        failure mode this stall detector exists to prevent. After enough
        consecutive undetermined checks, fall back to the same interrupt/
        re-prompt path used for a confirmed stall, so the job never depends on
        the arbiter being reliable in order to make forward progress.
        """
        failures = self._stall_check_consecutive_failures.get(job_id, 0) + 1
        self._stall_check_consecutive_failures[job_id] = failures
        if failures < _STALL_CHECK_MAX_CONSECUTIVE_FAILURES:
            return

        log.warning(
            "stall_check_repeatedly_undetermined_escalating",
            job_id=job_id,
            tool_name=tool_name,
            elapsed=elapsed_human,
            consecutive_failures=failures,
        )
        self._stall_check_consecutive_failures.pop(job_id, None)
        try:
            await self._handle_stall_interrupt(
                job_id,
                tool_name,
                elapsed_human,
                reason=(
                    f"stall detector itself failed {failures} times in a row and could not "
                    "determine whether this tool call is stuck"
                ),
            )
        except Exception:
            log.error("stall_escalation_interrupt_raised", job_id=job_id, exc_info=True)
            raise

    async def _handle_stall_interrupt(self, job_id: str, tool_name: str, elapsed: str, reason: str) -> None:
        """Interrupt the stalled tool and re-prompt the agent."""
        # Publish stall event for UI visibility
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.stall_detected,
                payload={
                    "job_id": job_id,
                    "tool_name": tool_name,
                    "elapsed": elapsed,
                    "reason": reason,
                },
            )
        )

        # Interrupt the running tool
        interrupted = await self.interrupt(job_id)
        if not interrupted:
            return

        # Re-prompt the agent with context
        message = (
            f"Your `{tool_name}` tool call was interrupted after being idle for {elapsed}. "
            f"Reason: {reason}. "
            f"Retry the command, try an alternative approach, or report the failure."
        )
        await self.send_message(job_id, message)

    @property
    def execution_epoch(self) -> str:
        return self._execution_epoch

    async def _require_attempt_job(self, job_id: str, attempt_id: str) -> Job:
        job = await self._get_job(job_id)
        if job is None or job.observation_only or job.state != JobState.running or self._shutting_down:
            raise AttemptBindingConflictError("Attempt admission requires a live running Job")
        if self._active_gates.get(job_id) or job_id in self._waiting_for_approval:
            raise AttemptBindingConflictError("Job execution is gated")
        if self._approval_service is not None and await self._approval_service.has_blocking_approvals(
            job_id,
            attempt_id=attempt_id,
        ):
            raise AttemptBindingConflictError("Job has a pending approval")
        return job

    async def authorize_attempt_command(
        self,
        job_id: str,
        attempt_id: str,
        expected_execution: str,
        argv: list[str],
        workspace: str,
    ) -> None:
        import json

        from backend.persistence.recipe_run_repo import RecipeRunRepository
        from backend.services.action_policy.session_policy import PermissionInvocation
        from backend.services.action_policy.standalone_router import StandalonePolicyRouter
        from backend.services.auth.permission_policy import PermissionRequest

        controller = self._recipe_controllers.get(job_id)
        owned = controller.owned_attempt(attempt_id) if controller else None
        if owned is None or owned.token != expected_execution or owned.task is not asyncio.current_task():
            raise StateConflictError("Command admission requires the actual owning recipe task token")
        async with self._session_factory() as session:
            repository = RecipeRunRepository(session)
            attempt = await repository.get_attempt(attempt_id)
            step = await repository.get_step(owned.step_id)
            run = await repository.get(owned.run_id)
        if (
            attempt is None
            or step is None
            or run is None
            or run.job_id != job_id
            or attempt.step_instance_id != step.id
            or step.run_id != run.id
            or step.current_attempt_id != attempt_id
            or attempt.result is not None
            or attempt.runner.kind != "command"
            or attempt.runner.argv != argv
            or attempt.workspace.path != workspace
        ):
            raise StateConflictError("Command arguments or owning attempt changed before policy")
        await self._require_attempt_job(job_id, attempt_id)
        router = self._policy_routers.get(job_id)
        if not isinstance(router, StandalonePolicyRouter):
            raise StateConflictError("Exact command authorization requires standalone admission policy")

        async def current() -> bool:
            now = controller.owned_attempt(attempt_id) if controller else None
            if now is not owned or now.task.cancelling():
                return False
            async with self._session_factory() as session:
                repository = RecipeRunRepository(session)
                latest = await repository.get(owned.run_id)
                current_step = await repository.get_step(owned.step_id)
            return bool(
                latest is not None
                and latest.cancel_requested_at is None
                and latest.resolved_at is None
                and current_step is not None
                and current_step.current_attempt_id == attempt_id
            )

        command = ""
        dialect = None
        executable = Path(argv[0]).name.lower()
        if executable in {"sh", "bash", "pwsh", "powershell"}:
            for index, value in enumerate(argv[:-1]):
                if value.lower() in {"-c", "-command"}:
                    command, dialect = argv[index + 1], "powershell" if executable in {"pwsh", "powershell"} else "bash"
                    break
        metadata = EventMetadata(
            source_framework="codeplane.recipe",
            run_id=owned.run_id,
            recipe_run_id=run.id,
            recipe_revision=run.snapshot.revision,
            step_instance_id=step.id,
            attempt_id=attempt_id,
            execution_epoch=self.execution_epoch,
            execution_token=owned.token,
        )
        permitted = await router.authorize_permission(
            PermissionInvocation(
                job_id=job_id,
                session_id=owned.run_id,
                provider="codeplane.command",
                metadata=metadata,
                workspace=workspace,
                protected_paths=(),
                request=PermissionRequest(kind="shell", workspace_path=workspace, full_command_text=command or None),
                tool_name=argv[0],
                arguments_json=json.dumps({"argv": argv}, ensure_ascii=False),
                is_current=current,
                dialect=dialect,
                tool_call_id=owned.token,
            )
        )
        if not permitted or not await current():
            raise StateConflictError("The exact command invocation was not authorized")

    @contextlib.asynccontextmanager
    async def open_attempt_execution(
        self, job_id: str, attempt_id: str, config: SessionConfig
    ) -> AsyncIterator[AttemptExecution]:
        """Own an inert SDK session from creation through registration and cleanup."""
        async with self._execution_control.hold(job_id):
            await self._require_attempt_job(job_id, attempt_id)
            adapter = self._adapter_registry.get_adapter(config.sdk)
        deferred = dataclass_replace(
            config,
            job_id=job_id,
            attempt_id=attempt_id,
            execution_epoch=self._execution_epoch,
            defer_start=True,
        )
        session_id: str | None = None
        token: str | None = None
        try:
            session_id = await adapter.create_session(deferred)
            context = await self.register_attempt_execution(job_id, attempt_id, adapter, session_id, sdk=config.sdk)
            token = context.execution_token
            assert token is not None
            yield AttemptExecution(adapter, session_id, token)
        finally:
            if session_id is not None:
                if token is None:
                    async with asyncio.timeout(30):
                        await adapter.abort_session(session_id)
                else:
                    binding = self._attempt_bindings.get(job_id, attempt_id)
                    if binding is not None and binding.token == token:
                        await self.cancel_attempt_execution(job_id, attempt_id, token)

    async def register_attempt_execution(
        self,
        job_id: str,
        attempt_id: str,
        adapter: AgentAdapterInterface,
        session_id: str,
        *,
        sdk: str | None = None,
    ) -> CurrentExecutionContext:
        """Register an already-created session without replacing the Job's primary."""
        async with self._execution_control.hold(job_id):
            job = await self._require_attempt_job(job_id, attempt_id)
            provider = IntegrationPolicy(self._config).require_enabled(sdk or job.sdk)
            if adapter is not self._adapter_registry.get_adapter(provider):
                raise AttemptBindingConflictError("Attempt adapter is not the admitted provider binding")
            primary = self._agent_sessions.get(job_id)
            if primary is not None and primary.session_id == session_id:
                raise AttemptBindingConflictError("The primary execution cannot also belong to an attempt")
            binding = self._attempt_bindings.register(job_id, attempt_id, adapter, session_id, provider)
            return CurrentExecutionContext(job_id, binding.token)

    def configure_execution_contracts(
        self,
        *,
        available: Mapping[str, ExecutionContract],
        selected_for_new: str,
    ) -> None:
        """Trusted composition selects available implementations separately from new admission."""
        from backend.persistence.execution_contract_repo import ExecutionContractRepository
        from backend.services.runtime.execution_contracts import ExecutionContracts

        self._execution_contracts = ExecutionContracts(
            ExecutionContractRepository(self._session_factory),
            available=available,
            selected_for_new=selected_for_new,
        )

    async def prepare_execution_contract(
        self,
        job_id: str,
        admission_id: str,
        *,
        recovery: bool,
    ) -> ExecutionContractPin | None:
        """Persist fresh admission or retrieve recovery provenance before governed invocation."""
        if self._execution_contracts is None:
            raise RuntimeError("Execution contract implementations have not been configured")
        if recovery:
            return await self._execution_contracts.recover(job_id, admission_id)
        return await self._execution_contracts.fresh(job_id, admission_id)

    async def _prepare_legacy_execution_contract(self, job_id: str) -> LegacyStartIntent | None:
        from backend.persistence.database import serialized_write
        from backend.persistence.execution_admission_repo import ExecutionAdmissionRepository
        from backend.persistence.recipe_run_repo import RecipeRunRepository

        async with self._session_factory() as session:
            if await RecipeRunRepository(session).get_for_job(job_id) is not None:
                return None
            admissions = ExecutionAdmissionRepository(session)
            admission = await admissions.current(job_id)
            unstarted = admission is not None and await admissions.is_unstarted(admission)
        if admission is None:
            if self._execution_contracts is not None:
                raise StateConflictError("Legacy execution admission is unrecorded; explicit continuation is required")
            return None
        if self._execution_contracts is not None:
            pin = await self.prepare_execution_contract(
                job_id,
                admission.id,
                recovery=job_id in self._legacy_recoveries and not unstarted,
            )
            if pin is None:
                raise StateConflictError("Recovery cannot create a missing historical execution contract")
            self._job_contract_pins[job_id] = pin
        async with serialized_write(self._session_factory) as session:
            await ExecutionAdmissionRepository(session).mark_started(
                admission,
                contract_recorded=self._execution_contracts is not None,
            )
        return admission.start_intent if unstarted else None

    async def _complete_legacy_handoff(self, job: Job, config: SessionConfig) -> SessionConfig:
        from backend.services.adapters.completion_context import CompletionContext, completion_context

        pending = self._legacy_handoffs.pop(job.id, None)
        if pending is None:
            return config
        pin = self._job_contract_pins.get(job.id)
        if (
            pin is None
            or pin.admission_id != pending.admission_id
            or job.session_count != pending.target_session_number
        ):
            raise StateConflictError("Deferred handoff does not belong to the pinned admission")
        source_job = dataclass_replace(job, session_count=pending.source_session_number)
        with completion_context(
            CompletionContext(
                job_id=job.id,
                execution_epoch=self._execution_epoch,
                execution_contract=pin,
            )
        ):
            async with self._session_factory() as session:
                prompt = await self._build_resume_handoff_prompt_for_job(
                    session,
                    source_job,
                    pending.instruction,
                    pending.target_session_number,
                )
        return dataclass_replace(config, prompt=prompt)

    async def bind_attempt_contract(
        self,
        pin: ExecutionContractPin,
        attempt_id: str,
        expected_execution: str,
    ) -> None:
        if self._execution_contracts is None:
            raise RuntimeError("Execution contract implementations have not been configured")
        async with self._execution_control.hold(pin.job_id):
            await self._require_attempt_job(pin.job_id, attempt_id)
            binding = self._attempt_bindings.get(pin.job_id, attempt_id)
            if binding is None or binding.token != expected_execution or binding.cancel_requested:
                raise AttemptBindingConflictError("Contract binding requires the current execution token")
            await self._execution_contracts.bind_handle(
                pin,
                sdk=str(binding.sdk),
                session_id=binding.session_id,
                live_epoch=self._execution_epoch,
                execution_token=binding.token,
                attempt_id=attempt_id,
            )

    async def attach_attempt_policy(self, job_id: str, attempt_id: str, expected_execution: str) -> None:
        """Attach current Job policy to the exact live provider session without re-registering budgets."""
        from backend.services.adapters.contracts import SessionPolicyAdapter

        async with self._execution_control.hold(job_id):
            await self._require_attempt_job(job_id, attempt_id)
            binding = self._attempt_bindings.get(job_id, attempt_id)
            if binding is None or binding.token != expected_execution or binding.cancel_requested:
                raise AttemptBindingConflictError("Attempt policy attachment requires a current execution token")
            router = self._policy_routers.get(job_id)
            policy = self._repo_policies.get(job_id)
            if router is None or policy is None:
                raise AttemptBindingConflictError("Job policy has not been configured")
            if not isinstance(binding.adapter, SessionPolicyAdapter):
                raise AttemptBindingConflictError("The selected execution adapter cannot attach Job policy")
            binding.adapter.attach_attempt_policy(
                binding.session_id,
                job_id,
                attempt_id,
                self._execution_epoch,
                router,
                policy,
                execution_token=binding.token,
            )

    async def unregister_attempt_execution(self, job_id: str, attempt_id: str, expected_execution: str) -> None:
        """Forget only the exact completed/stopped session supplied by its owner."""
        async with self._execution_control.hold(job_id):
            self._attempt_bindings.unregister(job_id, attempt_id, expected_execution)

    async def cancel_attempt_execution(self, job_id: str, attempt_id: str, expected_execution: str) -> None:
        """Retain cancel-requested evidence until the real adapter confirms stop."""
        async with self._execution_control.hold(job_id):
            binding = self._attempt_bindings.request_cancel(job_id, attempt_id, expected_execution)
            try:
                binding.adapter.pause_tools(binding.session_id)
            except (OSError, RuntimeError):
                log.error("attempt_pause_unconfirmed", job_id=job_id, attempt_id=attempt_id, exc_info=True)
        # The session owner may finalize and unregister while abort drains callbacks.
        async with asyncio.timeout(30):
            await binding.adapter.abort_session(binding.session_id)
        async with self._execution_control.hold(job_id):
            current = self._attempt_bindings.get(job_id, attempt_id)
            if current is not None and current.token == expected_execution:
                self._attempt_bindings.unregister(job_id, attempt_id, expected_execution)

    async def _stop_attempt_executions(self, job_id: str) -> None:
        # Fence every sibling before awaiting any potentially slow transport.
        async with self._execution_control.hold(job_id):
            bindings = self._attempt_bindings.for_job(job_id)
            for binding in bindings:
                current = self._attempt_bindings.get(job_id, binding.attempt_id)
                if current is None or current.token != binding.token:
                    continue
                self._attempt_bindings.request_cancel(job_id, binding.attempt_id, binding.token)
            for binding in bindings:
                try:
                    binding.adapter.pause_tools(binding.session_id)
                except (OSError, RuntimeError):
                    log.error("attempt_pause_unconfirmed", job_id=job_id, attempt_id=binding.attempt_id, exc_info=True)
        for binding in bindings:
            try:
                await self.cancel_attempt_execution(job_id, binding.attempt_id, binding.token)
            except (OSError, RuntimeError, TimeoutError):
                log.error("attempt_stop_unconfirmed", job_id=job_id, attempt_id=binding.attempt_id, exc_info=True)

    async def get_current_execution_context(
        self, job_id: str, *, attempt_id: str | None = None
    ) -> CurrentExecutionContext:
        async with self._execution_control.hold(job_id):
            job = await self._get_job(job_id)
            return CurrentExecutionContext(job_id, await self._execution_token(job, attempt_id=attempt_id))

    async def _execution_token(self, job: Job | None, *, attempt_id: str | None = None) -> str | None:
        import hashlib

        if job is None or job.observation_only or job.state != JobState.running:
            return None
        if attempt_id is not None:
            binding = self._attempt_bindings.get(job.id, attempt_id)
            return binding.token if binding is not None and not binding.cancel_requested else None
        if job.source == JobSource.managed:
            handle = self._agent_sessions.get(job.id)
            if handle is None or not handle.session_id:
                return None
            owned_token = getattr(handle, "execution_token", None)
            if isinstance(owned_token, str) and owned_token:
                return owned_token
            identity = f"{id(handle)}:{handle.session_id}"
        elif job.source == JobSource.copilot_cli:
            # Publication and permission markers do not identify a live CLI execution.
            # Until a supported live identity signal exists, do not certify a target
            # for strict preview/confirmation. Ordinary relay admission is separate.
            return None
        else:
            generation = self._external_generations.get(job.id)
            if not generation or self._ingest_service is None or not await self._ingest_service.confirms_live(job):
                return None
            identity = f"{generation}:{job.external_session_id}"
        return hashlib.sha256(
            f"{self._execution_epoch}:{job.id}:{job.source}:{job.session_count}:{identity}".encode()
        ).hexdigest()

    async def dispatch_current_execution(
        self, job_id: str, expected_execution: str, content: str, *, attempt_id: str | None = None
    ) -> CurrentExecutionDispatchResult:
        async with self._execution_control.hold(job_id):
            job = await self._get_job(job_id)
            token = await self._execution_token(job, attempt_id=attempt_id)
            reason = None
            if job is None or job.state != JobState.running:
                reason = "not_running"
            elif token is None:
                reason = "no_live_execution"
            elif token != expected_execution:
                reason = "stale_execution"
            elif self._approval_service is not None and await self._approval_service.has_blocking_approvals(
                job_id,
                attempt_id=attempt_id,
            ):
                reason = "approval_pending"
            elif self._active_gates.get(job_id) or job_id in self._waiting_for_approval:
                reason = "gate_active"
            if reason:
                return CurrentExecutionDispatchResult(job_id, token, "conflict", reason)
            assert job is not None
            binding = self._attempt_bindings.get(job_id, attempt_id) if attempt_id is not None else None
            target_session_id = (
                binding.session_id
                if binding is not None
                else self._agent_sessions[job_id].session_id
                if job.source == JobSource.managed
                else job.external_session_id
            )
            try:
                IntegrationPolicy(self._config).require_enabled(binding.sdk if binding is not None else job.sdk)
                async with asyncio.timeout(30):
                    if binding is not None:
                        await binding.adapter.send_message(binding.session_id, content)
                    elif job.source == JobSource.managed:
                        await self._agent_sessions[job_id].send_message(content)
                    else:
                        assert self._ingest_service is not None
                        await self._ingest_service.send_operator_message(job_id, content)
            except IntegrationDisabledError:
                return CurrentExecutionDispatchResult(job_id, token, "failed", "integration_disabled")
            except NativeDeliveryError as exc:
                return CurrentExecutionDispatchResult(
                    job_id,
                    token,
                    "uncertain" if exc.uncertain else "failed",
                    "delivery_unknown" if exc.uncertain else "delivery_failed",
                )
            except (TimeoutError, ConnectionError, OSError):
                return CurrentExecutionDispatchResult(job_id, token, "uncertain", "delivery_unknown")
        receipt_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "content": (
                f"Coordinated operator admission receipt {receipt_id}\n"
                "Instruction admitted to the message channel. Execution unconfirmed.\n\n"
                f"{content}"
            ),
            "operator_content": content,
            "dispatch": "coordinated",
            "admission_status": "accepted",
            "execution_status": "unconfirmed",
            "target_session_id": target_session_id,
        }
        if attempt_id is not None:
            payload["attempt_id"] = attempt_id
        message = new_event(
            job_id,
            EventKind.message_user,
            payload,
            event_id=receipt_id,
            metadata=EventMetadata(
                attempt_id=attempt_id,
                execution_epoch=self._execution_epoch,
                execution_token=expected_execution,
                run_id=target_session_id,
                source_framework="codeplane",
                namespace=("control", "admission-receipt"),
            ),
        )
        from backend.persistence.database import serialized_write
        from backend.persistence.event_repo import EventRepository

        try:
            async with serialized_write(self._session_factory) as session:
                await EventRepository(session).append(message)
        except DBAPIError:
            log.error(
                "coordinated_admission_receipt_persist_failed",
                job_id=job_id,
                receipt_id=message.id,
                exc_info=True,
            )
            return CurrentExecutionDispatchResult(job_id, token, "uncertain", "receipt_persistence_failed")
        try:
            await self._event_bus.publish(message)
        except (DBAPIError, OSError, RuntimeError):
            log.error(
                "coordinated_admission_receipt_publish_failed",
                job_id=job_id,
                receipt_id=message.id,
                exc_info=True,
            )
        return CurrentExecutionDispatchResult(job_id, token, "accepted", message_event_id=message.id)

    async def send_operator_message(self, job_id: str, content: str) -> bool:
        job = await self._get_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} does not exist")
        if job.observation_only:
            raise StateConflictError("Observation-only history has no instruction channel")
        IntegrationPolicy(self._config).require_enabled(job.sdk)
        if job.source == JobSource.managed:
            return await self.send_message(job_id, content)
        async with self._execution_control.hold(job_id):
            if self._ingest_service is None:
                raise NativeDeliveryError("Native steering is unavailable")
            await self._ingest_service.send_operator_message(job_id, content)
            receipt_id = str(uuid.uuid4())
            receipt = new_event(
                job_id,
                EventKind.message_user,
                {
                    "content": (
                        f"Native operator admission receipt {receipt_id}\n"
                        "Instruction admitted to the message channel. Execution unconfirmed.\n\n"
                        f"{content}"
                    ),
                    "operator_content": content,
                    "admission_status": "accepted",
                    "execution_status": "unconfirmed",
                    "source": str(job.source),
                    "external_session_id": job.external_session_id,
                },
                event_id=receipt_id,
            )
            from backend.persistence.database import serialized_write
            from backend.persistence.event_repo import EventRepository

            # Event fan-out can queue persistence retries, so commit before publishing.
            try:
                async with serialized_write(self._session_factory) as session:
                    await EventRepository(session).append(receipt)
            except DBAPIError as exc:
                log.error(
                    "native_operator_receipt_persist_failed",
                    job_id=job_id,
                    receipt_id=receipt_id,
                    error_type=type(exc).__name__,
                )
                raise NativeDeliveryError(
                    "Native admission succeeded but its durable receipt could not be stored",
                    uncertain=True,
                ) from None
        await self._event_bus.publish(receipt)
        return True

    async def cancel_job(self, job_id: str) -> Job:
        async with self._execution_control.hold(job_id):
            job = await self._get_job(job_id)
            if job is None:
                raise JobNotFoundError(f"Job {job_id} does not exist")
            if job.source != JobSource.managed:
                if self._ingest_service is None:
                    raise NativeDeliveryError("Native steering is unavailable")
                await self._ingest_service.abort_session(job_id)
            async with self._session_factory() as session:
                canceled = await self._make_job_service(session).cancel_job(job_id)
                await session.commit()
        if job.source == JobSource.managed:
            await self.cancel(job_id)
        return canceled

    async def cancel(self, job_id: str) -> None:
        """Cancel a running job by cancelling its asyncio task.

        State transitions for non-running jobs (e.g. queued) are handled
        by the service layer (JobService.cancel_job). This method only
        interacts with in-memory runtime tasks.
        """
        self._queued_session_tokens.pop(job_id, None)
        self._legacy_recoveries.pop(job_id, None)
        self._legacy_handoffs.pop(job_id, None)
        task = self._tasks.get(job_id)
        starting = self._starting_tasks.get(job_id)
        if starting is not None and starting is not asyncio.current_task():
            starting.cancel()
        if task is not None:
            task.cancel()
            log.info("job_cancel_requested", job_id=job_id)
        scout_task = self._scout_tasks.get(job_id)
        if scout_task is not None:
            scout_task.cancel()
        await self._stop_attempt_executions(job_id)
        if starting is not None and starting is not asyncio.current_task():
            return
        if task is None:
            # No task yet — job may still be in preflight curation.
            # Abort the agent session so the preflight curator's stream ends.
            agent_session = self._agent_sessions.get(job_id)
            if agent_session is not None:
                with contextlib.suppress(Exception):
                    await agent_session.abort()
                log.info("job_cancel_preflight_aborted", job_id=job_id)
            else:
                log.info("job_cancel_no_running_task", job_id=job_id)

    async def interrupt(self, job_id: str) -> bool:
        """Interrupt the agent's current turn without destroying the session.

        Sends SIGINT-equivalent to the SDK subprocess: the currently running
        shell command is killed, but the session stays alive and the agent can
        recover or be given a new instruction.

        Returns True if an active session was found and interrupted.
        """
        agent_session = self._agent_sessions.get(job_id)
        if agent_session is None:
            log.info("job_interrupt_no_session", job_id=job_id)
            return False
        await agent_session.interrupt()
        log.info("job_interrupted", job_id=job_id)
        return True

    # ------------------------------------------------------------------
    # Observer terminal bridge
    # ------------------------------------------------------------------

    async def send_message(self, job_id: str, message: str) -> bool:
        """Send an operator message to a running job.

        Publishes the transcript event locally for immediate UI feedback and
        suppresses the SDK echo to avoid showing the message twice.

        Legacy non-recipe Jobs may resume when their primary session is absent.
        Recipe messages require the selected attempt's exact execution handle.
        """
        from backend.persistence.recipe_run_repo import RecipeRunRepository

        async with self._session_factory() as session:
            recipe_run = await RecipeRunRepository(session).get_for_job(job_id)
        if recipe_run is not None:
            raise StateConflictError(
                "Recipe Jobs require a selected attempt and its current execution token. "
                "Use explicit Resume to continue a finished run."
            )
        agent_session = self._agent_sessions.get(job_id)
        if agent_session is None:
            job = await self._get_job(job_id)
            if job is not None and job.source != JobSource.managed:
                return await self.send_operator_message(job_id, message)
            return await self._resume_orphaned(job_id, message)
        # Lift any tool block from a previous pause before sending.
        agent_session.resume_tools()
        now = datetime.now(UTC)
        await agent_session.send_message(message)
        # Publish immediately so the operator message appears in the transcript
        # without waiting for the SDK to echo it back.
        operator_event = new_event(
            session_id=job_id,
            timestamp=now,
            kind=EventKind.message_user,
            payload={
                "job_id": job_id,
                "seq": 0,
                "timestamp": now.isoformat(),
                "content": message,
            },
        )
        if self._step_tracker is not None:
            await self._step_tracker.on_transcript_event(job_id, operator_event)
            current = self._step_tracker.current_step(job_id)
            if current:
                operator_event.payload["step_id"] = current.step_id
                operator_event.payload["step_number"] = current.step_number
        await self._event_bus.publish(operator_event)
        # Suppress the SDK echo so the same content is not published twice.
        self._echo_suppress.setdefault(job_id, set()).add(message)
        return True

    async def inject_sidecar_message(self, job_id: str, message: str) -> bool:
        """Inject a sidecar message into a running agent session.

        Unlike ``send_message`` (operator path), this does NOT publish a
        transcript event (the sidecar dispatcher already emits
        sidecar_transcript / sidecar_agent_message events for UI visibility)
        and does NOT resume paused tools.
        """
        agent_session = self._agent_sessions.get(job_id)
        if agent_session is None:
            log.warning("inject_sidecar_message_no_session", job_id=job_id)
            return False
        await agent_session.send_message(message)
        # Suppress the SDK echo — the sidecar events already provide UI visibility.
        self._echo_suppress.setdefault(job_id, set()).add(message)
        return True

    async def resolve_policy_batch(
        self,
        job_id: str,
        batch_id: str,
        resolution: str,
        approved_ids: list[str] | None = None,
        trust_grant_id: str | None = None,
    ) -> bool:
        """Resolve a pending action policy batch.

        Called by the operator via the approvals API. The resolution
        unblocks the batcher which unblocks the SDK permission callback.

        Returns True if the batch was found and resolved.
        """
        from backend.services.action_policy.batcher import BatchResolution

        batcher = self._policy_batchers.get(job_id)
        if batcher is None:
            return False
        res = BatchResolution(resolution)
        approved_set = set(approved_ids) if approved_ids else None
        resolved = batcher.resolve_batch(batch_id, res, approved_set, trust_grant_id)

        if resolved:
            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=EventKind.batch_approval_resolved,
                    payload={
                        "batch_id": batch_id,
                        "resolution": resolution,
                    },
                )
            )

        return bool(resolved)

    async def trust_job_policy(self, job_id: str) -> bool:
        """Create a blanket trust grant for a job so all future actions auto-approve.

        Also resolves any currently pending batch for the job.
        Returns True if the trust grant was created (job is governed).
        """
        from backend.services.action_policy.batcher import BatchResolution

        if self._governance_decider is None or not self._governance_decider.is_registered(job_id):
            return False

        # Blanket session trust: waive every NON-security-critical gate for a long
        # TTL. Security-critical §18.2 gates are never waived (enforced inside the
        # decider), so a "trust this session" action can't bypass a hard gate.
        self._governance_decider.grant_session_trust(job_id, ttl_seconds=86_400.0, reason="operator trusted session")

        # Also resolve any pending batch so the currently-blocked action proceeds
        batcher = self._policy_batchers.get(job_id)
        if batcher:
            for batch in batcher.get_pending_batches(job_id):
                batcher.resolve_batch(batch.id, BatchResolution.approved)

        return True

    async def _resume_orphaned(self, job_id: str, message: str) -> bool:
        """Auto-resume a job that has no live agent session."""
        return await _resume_orphaned_impl(self, job_id, message)

    async def pause_job(self, job_id: str) -> bool:
        """Forcefully pause a running agent. Returns True if sent.

        Immediately blocks all tool execution for the session so the agent
        cannot take further actions, interrupts the current turn (on SDKs
        that support it), and sends a follow-up message instructing the
        agent to wait.  The pause message is never shown in the transcript.
        """
        _pause_msg = (
            "Please stop what you are doing right now and wait. "
            "Do not take any further actions until the operator sends a follow-up message."
        )
        agent_session = self._agent_sessions.get(job_id)
        if agent_session is None:
            log.warning("pause_job_no_session", job_id=job_id)
            return False
        # Block all tool calls immediately so the agent cannot act.
        agent_session.pause_tools()
        # Interrupt the current turn so the agent stops immediately.
        try:
            await agent_session.interrupt()
        except Exception:
            log.warning("pause_interrupt_failed", job_id=job_id, exc_info=True)
        # Pre-register the echo suppression before sending so the SDK echo
        # (if any) is discarded and never appears in the transcript.
        self._echo_suppress.setdefault(job_id, set()).add(_pause_msg)
        await agent_session.send_message(_pause_msg)
        log.info("job_pause_requested", job_id=job_id)
        return True

    async def handle_sidecar_gate(self, job_id: str, sidecar_name: str, verdict: str, reason: str) -> None:
        async with self._execution_control.hold(job_id):
            await self._handle_sidecar_gate(job_id, sidecar_name, verdict, reason)

    async def _handle_sidecar_gate(
        self,
        job_id: str,
        sidecar_name: str,
        verdict: str,
        reason: str,
    ) -> None:
        """Handle a sidecar gate verdict — pause or resume the agent.

        Called by the dispatcher's gate handler callback.  For managed
        sessions this pauses/resumes tools directly.  For imported CLI
        sessions it queues an operator message (soft gate).
        """
        if verdict in ("approve", "pass", "ok"):
            # Resume if previously gated.
            self._active_gates.get(job_id, {}).pop(sidecar_name, None)
            agent_session = self._agent_sessions.get(job_id)
            if agent_session is not None and not self._active_gates.get(job_id):
                agent_session.resume_tools()
                if self._sidecar_dispatcher is not None:
                    self._sidecar_dispatcher.set_gated(job_id, gated=False)
            log.info("sidecar_gate_resumed", job_id=job_id, sidecar=sidecar_name)
            return

        if verdict not in ("reject", "hold", "deny", "block"):
            log.warning("sidecar_gate_unknown_verdict", job_id=job_id, verdict=verdict)
            return

        # Record the active gate.
        self._active_gates.setdefault(job_id, {})[sidecar_name] = reason or verdict

        # Check if this is an imported session (soft gate via messaging).
        job = await self._get_job(job_id)
        if job and job.source != JobSource.managed:
            # Soft gate — deliver via the CLI channel (Stop hook / Steer API).
            gate_msg = (
                f"[GATE:{sidecar_name}] {reason}" if reason else f"[GATE:{sidecar_name}] Action blocked by sidecar."
            )
            if self._ingest_service is not None:
                await self._ingest_service.send_operator_message(job_id, gate_msg)
            else:
                log.warning("sidecar_gate_no_ingest", job_id=job_id, sidecar=sidecar_name)
            log.info("sidecar_gate_soft", job_id=job_id, sidecar=sidecar_name, source=job.source)
            return

        # Hard gate — pause tools on the managed session.
        agent_session = self._agent_sessions.get(job_id)
        if agent_session is None:
            log.warning("sidecar_gate_no_session", job_id=job_id, sidecar=sidecar_name)
            return

        agent_session.pause_tools()
        if self._sidecar_dispatcher is not None:
            self._sidecar_dispatcher.set_gated(job_id, gated=True)
        log.info("sidecar_gate_paused", job_id=job_id, sidecar=sidecar_name, reason=reason)

    async def resolve_sidecar_gate(self, job_id: str, action: str, message: str | None = None) -> None:
        async with self._execution_control.hold(job_id):
            await self._resolve_sidecar_gate(job_id, action, message)

    async def _resolve_sidecar_gate(
        self,
        job_id: str,
        action: str,
        message: str | None = None,
    ) -> None:
        """Operator resolution of a sidecar gate.

        ``action='approve'`` resumes tools (and optionally sends a message).
        ``action='reject'`` keeps tools paused (message is still delivered
        without resuming tools).
        """
        gates = self._active_gates.get(job_id)
        if not gates:
            log.warning("resolve_gate_no_active_gate", job_id=job_id, action=action)
            return

        if action == "approve":
            self._active_gates.pop(job_id, None)
            # send_message resumes tools internally — correct for approve.
            if message:
                await self.send_message(job_id, message)
            else:
                agent_session = self._agent_sessions.get(job_id)
                if agent_session is not None:
                    agent_session.resume_tools()
            if self._sidecar_dispatcher is not None:
                self._sidecar_dispatcher.set_gated(job_id, gated=False)
            log.info("sidecar_gate_resolved_approve", job_id=job_id)
        else:
            # Reject — deliver message WITHOUT resuming tools.
            if message:
                agent_session = self._agent_sessions.get(job_id)
                if agent_session is not None:
                    await agent_session.send_message(message)
            log.info("sidecar_gate_resolved_reject", job_id=job_id)

    async def _dequeue_next(self) -> None:
        """Start the next queued job if capacity allows."""
        if self._shutting_down:
            return
        async with self._dequeue_lock:
            if self.running_count >= self.max_concurrent:
                return
            try:
                if self._pending_starts:
                    job_id, (override_prompt, resume_sdk_session_id) = next(iter(self._pending_starts.items()))
                    self._pending_starts.pop(job_id, None)
                    async with self._session_factory() as session:
                        job = await JobRepository(session).get(job_id)
                    if job is not None:
                        await self._start_job(
                            job,
                            override_prompt=override_prompt,
                            resume_sdk_session_id=resume_sdk_session_id,
                        )
                    return

                async with self._session_factory() as session:
                    svc = self._make_job_service(session)
                    queued_jobs = await svc.list_jobs(state=JobState.queued, limit=1)
                    jobs, _, _ = queued_jobs
                if jobs:
                    job = jobs[0]
                    override_prompt = self._queued_override_prompts.pop(job.id, None)
                    resume_sdk_session_id = self._queued_resume_session_ids.pop(job.id, None)
                    await self._start_job(
                        job,
                        override_prompt=override_prompt,
                        resume_sdk_session_id=resume_sdk_session_id,
                    )
            except Exception:
                log.error("dequeue_failed", exc_info=True)

    async def _fail_job(self, job_id: str, reason: str) -> None:
        """Transition a job to failed state and publish the event.

        The DB transition is run inside ``asyncio.shield`` so that a
        pending task-level cancellation (e.g. from anyio cancel-scope
        teardown) cannot interrupt the write.
        """

        async def _do_fail() -> None:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.get_job(job_id)
                await svc.transition_state(job_id, JobState.failed, failure_reason=reason)
                await session.commit()

        try:
            await asyncio.shield(_do_fail())
            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=EventKind.job_failed,
                    payload={"reason": reason},
                )
            )
        except (Exception, asyncio.CancelledError):
            log.error("fail_job_transition_failed", job_id=job_id, exc_info=True)

    async def _persist_sdk_session_id(self, job_id: str, sdk_session_id: str) -> None:
        """Persist the Copilot SDK session ID so resume_job() can reconnect to it later."""
        try:
            async with self._session_factory() as session:
                job_repo = JobRepository(session)
                await job_repo.update_sdk_session_id(job_id, sdk_session_id)
                await session.commit()
        except DBAPIError:
            log.warning("persist_sdk_session_id_failed", job_id=job_id, exc_info=True)

    async def _clear_sdk_session_id(self, job_id: str) -> None:
        """Clear a stale Copilot SDK session ID so resume falls back cleanly."""
        try:
            async with self._session_factory() as session:
                job_repo = JobRepository(session)
                await job_repo.update_sdk_session_id(job_id, None)
                await session.commit()
        except DBAPIError:
            log.warning("clear_sdk_session_id_failed", job_id=job_id, exc_info=True)

    async def _load_handoff_context_for_job(
        self,
        session: AsyncSession,
        job: Job,
    ) -> tuple[str | None, list[str]]:
        return await load_handoff_context_for_job(session, self._session_factory, job, self._summarization_service)

    async def _build_resume_handoff_prompt_for_job(
        self,
        session: AsyncSession,
        job: Job,
        instruction: str,
        session_number: int,
    ) -> str:
        return await build_resume_handoff_prompt_for_job(
            session, self._session_factory, job, instruction, session_number, self._summarization_service
        )

    async def _build_followup_handoff_prompt_for_job(
        self,
        session: AsyncSession,
        job: Job,
        instruction: str,
    ) -> str:
        return await build_followup_handoff_prompt_for_job(
            session, self._session_factory, job, instruction, self._summarization_service
        )

    async def _build_resume_handoff_prompt(self, job_id: str, instruction: str) -> str:
        """Build the opaque handoff prompt used when native resume is unavailable."""

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)
            if job is None:
                raise JobNotFoundError(f"Job {job_id} does not exist.")
            return await self._build_resume_handoff_prompt_for_job(session, job, instruction, job.session_count)

    async def create_followup_job(self, job_id: str, instruction: str) -> Job:
        """Create and start a new follow-up job with parent-job handoff context."""
        return await _create_followup_job_impl(self, job_id, instruction)

    async def resume_job(self, job_id: str, instruction: str | None = None) -> Job:
        """Resume a terminal or review job in-place."""
        from backend.persistence.recipe_run_repo import RecipeRunRepository
        from backend.services.recipe.continuation import resume_recipe_job

        async with self._session_factory() as session:
            run = await RecipeRunRepository(session).get_for_job(job_id)
        if run is not None:
            job, _ = await resume_recipe_job(self, job_id, instruction, expected_run_id=run.id)
            return job
        return await _resume_job_impl(self, job_id, instruction)

    async def resume_recipe_run(
        self,
        job_id: str,
        instruction: str | None,
        *,
        expected_run_id: str,
        start_request_key: str | None = None,
        selection: RecipeSelection | None = None,
    ) -> tuple[Job, RecipeRunResponse]:
        from backend.services.recipe.continuation import resume_recipe_job

        return await resume_recipe_job(
            self,
            job_id,
            instruction,
            expected_run_id=expected_run_id,
            start_request_key=start_request_key,
            selection=selection,
        )

    async def _cleanup_job_worktree(self, job: Job) -> None:
        """Remove the secondary worktree for a finished job (failed/canceled).

        The main worktree (where worktree_path == repo) is never removed.
        """
        import contextlib

        worktree_path = job.worktree_path
        if job.observation_only or not worktree_path or worktree_path == job.repo:
            return  # main worktree — leave it alone
        from backend.services.git.git_service import GitError, GitService

        git = GitService(self._config)
        with contextlib.suppress(GitError, OSError):
            await git.remove_worktree(job.execution_repo, worktree_path)
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

        if not REF_PATTERN.match(job.branch):
            log.warning("pr_creation_invalid_branch", job_id=job_id)
            return None
        if job.observation_only or not job.base_ref or not REF_PATTERN.match(job.base_ref):
            log.warning("pr_creation_invalid_base_ref", job_id=job_id)
            return None

        adapter = await self._platform_registry.get_adapter(job.execution_repo)
        pr_result = await adapter.create_pr(
            cwd=job.worktree_path,
            head=job.branch,
            base=job.base_ref,
            title=f"[CodePlane] {job.prompt[:80]}",
            body=f"Automated PR created by CodePlane for job `{job_id}`.",
        )
        if pr_result.ok:
            log.info("pr_created", job_id=job_id, pr_url=pr_result.url, platform=adapter.name)
            return pr_result.url
        log.warning("pr_creation_failed", job_id=job_id, platform=adapter.name, error=pr_result.error)
        return None

    async def _publish_state_event(self, job_id: str, previous_state: str | None, new_state: str) -> None:
        """Publish a job state change event."""
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.job_state_changed,
                payload={
                    "previous_state": previous_state,
                    "new_state": new_state,
                },
            )
        )

    async def _emit_setup_progress(self, job_id: str, step: str) -> None:
        """Emit a job_setup_progress event so the frontend can show sub-step text."""
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.job_setup_progress,
                payload={"step": step},
            )
        )

    async def recover_on_startup(self) -> None:
        """Recover from a previous crash by restarting active jobs and re-enqueueing queued ones."""
        # Restore in-memory futures for approvals that survived the restart
        # so that recovered jobs in waiting_for_approval can be unblocked.
        if self._approval_service is not None:
            await self._approval_service.recover_pending_approvals()

        orphaned_jobs: list[tuple[Job, JobState]] = []
        preparing_jobs: list[Job] = []
        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            # Recover jobs that were already in progress before the backend restart.
            # Skip discovered CLI sessions (copilot_cli / claude_cli) — their
            # watchers handle re-attach or finalization on startup.
            for state in (JobState.running, JobState.waiting_for_approval):
                jobs = await svc.list_all_jobs(state=state)
                orphaned_jobs.extend((job, state) for job in jobs if job.source == JobSource.managed)

            # Re-enqueue queued jobs
            queued_jobs = await svc.list_all_jobs(state=JobState.queued)

            # Re-run setup for jobs that were mid-preparation when we crashed
            preparing = await svc.list_all_jobs(state=JobState.preparing)
            preparing_jobs.extend(preparing)

        for job in preparing_jobs:
            try:
                IntegrationPolicy(self._config).require_enabled(job.sdk)
            except IntegrationDisabledError as exc:
                await self._fail_job(job.id, str(exc))
                continue
            log.info("recovering_preparing_job", job_id=job.id)
            self._legacy_recoveries[job.id] = object()
            asyncio.create_task(self.setup_and_start(job), name=f"recover-setup-{job.id}")

        recoverable = orphaned_jobs

        if recoverable:
            log.info("recovering_orphaned_jobs", count=len(recoverable))
            # Recover sequentially to avoid exhausting the connection pool
            # and deadlocking against the global write lock.
            for job, state in recoverable:
                log.debug("recovering_orphaned_job", job_id=job.id, state=state)
                try:
                    from backend.services.runtime.verify import recover_interrupted_verification

                    await recover_interrupted_verification(self, job.id)
                    IntegrationPolicy(self._config).require_enabled(job.sdk)
                    if self._approval_service is not None:
                        await self._approval_service.retire_execution_requests(
                            job.id, "Superseded by execution recovery after server restart"
                        )
                    await self._recover_active_job(job.id)
                except Exception as exc:
                    log.error("orphaned_job_recovery_failed", job_id=job.id, exc_info=True)
                    await self._fail_job(job.id, f"Recovery failed: {exc}")

        await self._artifact_finalizer.recover_eligible()

        for job in queued_jobs:
            if job.source != JobSource.managed:
                continue
            try:
                self._legacy_recoveries[job.id] = object()
                await self.start_or_enqueue(job)
            except IntegrationDisabledError as exc:
                await self._fail_job(job.id, str(exc))

    async def shutdown(self) -> None:
        """Gracefully shut down all running jobs.

        Jobs are left in their current state (running / waiting_for_approval)
        so that ``recover_on_startup`` can pick them up on the next launch
        instead of marking them as canceled (which confused users).
        """
        self._shutting_down = True
        for starting_task in tuple(self._starting_tasks.values()):
            if starting_task is not asyncio.current_task():
                starting_task.cancel()
        for job_id in list(self._tasks):
            task = self._tasks.get(job_id)
            if task is not None:
                task.cancel()
                log.info("shutdown_task_cancelled", job_id=job_id)
        for job_id, scout_task in list(self._scout_tasks.items()):
            scout_task.cancel()
            log.info("shutdown_scout_cancelled", job_id=job_id)
        for binding in self._attempt_bindings.all():
            self._attempt_bindings.request_cancel(binding.job_id, binding.attempt_id, binding.token)
            try:
                binding.adapter.pause_tools(binding.session_id)
            except (OSError, RuntimeError):
                log.error(
                    "attempt_pause_unconfirmed", job_id=binding.job_id, attempt_id=binding.attempt_id, exc_info=True
                )
        for job_id in {binding.job_id for binding in self._attempt_bindings.all()}:
            await self._stop_attempt_executions(job_id)
        # Wait briefly for tasks to complete
        tasks = list(set(self._tasks.values()) | set(self._starting_tasks.values()) | set(self._scout_tasks.values()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._queued_session_tokens.clear()

    # -- Preflight context curation ------------------------------------------

    # -- Plan mode ------------------------------------------------------------

    async def _handle_plan_session_completed(
        self,
        job_id: str,
        job: Job,
        agent_session: AgentSession,
        config: SessionConfig,
        worktree_path: str | None,
        base_ref: str | None,
        session_number: int,
    ) -> JobState | None:
        """Handle completion of a plan-mode planning session.

        Captures plan steps produced by manage_todo_list, raises a synthetic
        approval gate, and either starts the implementation session (approved)
        or re-plans (rejected).  Returns the final job state, or None if the
        job should fall through to normal completion handling (no plan produced).
        """
        from backend.services.runtime.plan_mode import (
            build_implementation_handoff,
            build_replan_prompt,
            format_plan_text,
        )

        # Retrieve plan steps captured by TrailService during the planning session
        plan_steps: list[dict[str, str]] = []
        if self._trail_service is not None:
            plan_steps = self._trail_service.get_plan_steps(job_id)

        if not plan_steps:
            log.warning("plan_mode.no_plan_produced", job_id=job_id)
            await self._fail_job(job_id, "Planning session ended without producing a plan")
            return JobState.failed

        plan_text = format_plan_text(plan_steps)
        log.info("plan_mode.plan_captured", job_id=job_id, step_count=len(plan_steps))

        # Capture the curated context from the planning session so it can be
        # injected into the implementation session without re-running preflight.
        curated_context = config.memory_context or ""

        # Raise synthetic approval gate for operator review
        if self._approval_service is None:
            raise ServiceInitError("approval_service required for plan mode")

        approval = await self._approval_service.create_request(
            job_id=job_id,
            description=(
                f"Agent proposed a {len(plan_steps)}-step plan. Review and approve to proceed with implementation."
            ),
            proposed_action="execute_plan",
            requires_explicit_approval=True,
        )

        # Transition to waiting_for_approval and publish event
        async with self._session_factory() as sess:
            svc = self._make_job_service(sess)
            await svc.transition_state(job_id, JobState.waiting_for_approval)
            await sess.commit()
        self._waiting_for_approval.add(job_id)

        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.approval_requested,
                payload={
                    "approval_id": approval.id,
                    "description": approval.description,
                    "proposed_action": "execute_plan",
                    "requires_explicit_approval": True,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        )

        # Await operator decision
        resolution = await self._approval_service.wait_for_resolution(approval.id)
        # Fetch the full approval to read operator notes (if any)
        resolved_approval = await self._approval_service.get(approval.id)
        operator_notes = (resolved_approval.notes if resolved_approval else None) or ""

        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.approval_resolved,
                payload={
                    "approval_id": approval.id,
                    "resolution": resolution,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        )

        self._waiting_for_approval.discard(job_id)
        self._last_activity[job_id] = time.monotonic()

        # -- Rejection → re-plan loop (iterative, not recursive) ----------
        max_replan_iterations = 5
        replan_count = 0
        while resolution == ApprovalResolution.rejected:
            replan_count += 1
            if replan_count > max_replan_iterations:
                log.warning("plan_mode.max_replans_exceeded", job_id=job_id, count=replan_count)
                await self._fail_job(job_id, f"Plan rejected {max_replan_iterations} times — giving up")
                return JobState.failed

            log.info("plan_mode.plan_rejected", job_id=job_id)

            try:
                # Transition back to running for the re-plan session
                async with self._session_factory() as sess:
                    svc = self._make_job_service(sess)
                    await svc.transition_state(job_id, JobState.running)
                    await sess.commit()
                await self._publish_state_event(job_id, JobState.waiting_for_approval, JobState.running)

                feedback = operator_notes or "The operator rejected the plan without specific feedback."
                replan_prompt = build_replan_prompt(job, plan_text, feedback)

                replan_config = dataclass_replace(config, prompt=replan_prompt)
                new_agent_session = AgentSession()
                self._agent_sessions[job_id] = new_agent_session
                result = await self._execute_session_attempt(
                    job_id,
                    new_agent_session,
                    replan_config,
                    worktree_path,
                    base_ref,
                    session_number=session_number,
                )
                if result.error_reason:
                    await self._finalize_diff_safe(job_id, worktree_path, base_ref)
                    await self._fail_job(job_id, result.error_reason)
                    return JobState.failed

                # Re-read plan steps from the re-plan session
                plan_steps = []
                if self._trail_service is not None:
                    plan_steps = self._trail_service.get_plan_steps(job_id)

                if not plan_steps:
                    log.warning("plan_mode.replan_no_plan_produced", job_id=job_id)
                    await self._fail_job(job_id, "Re-planning session ended without producing a plan")
                    return JobState.failed

                plan_text = format_plan_text(plan_steps)
                config = replan_config

                # Raise a new approval gate for the revised plan
                approval = await self._approval_service.create_request(
                    job_id=job_id,
                    description=f"Agent revised the plan ({len(plan_steps)} steps). Review and approve to proceed.",
                    proposed_action="execute_plan",
                    requires_explicit_approval=True,
                )

                async with self._session_factory() as sess:
                    svc = self._make_job_service(sess)
                    await svc.transition_state(job_id, JobState.waiting_for_approval)
                    await sess.commit()
                self._waiting_for_approval.add(job_id)

                await self._event_bus.publish(
                    new_event(
                        session_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=EventKind.approval_requested,
                        payload={
                            "approval_id": approval.id,
                            "description": approval.description,
                            "proposed_action": "execute_plan",
                            "requires_explicit_approval": True,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    )
                )

                resolution = await self._approval_service.wait_for_resolution(approval.id)
                resolved_approval = await self._approval_service.get(approval.id)
                operator_notes = (resolved_approval.notes if resolved_approval else None) or ""

                await self._event_bus.publish(
                    new_event(
                        session_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=EventKind.approval_resolved,
                        payload={
                            "approval_id": approval.id,
                            "resolution": resolution,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    )
                )

                self._waiting_for_approval.discard(job_id)
                self._last_activity[job_id] = time.monotonic()

            except asyncio.CancelledError:
                raise  # Let cancellation propagate to _run_job's handler
            except Exception:
                log.error("plan_mode.replan_failed", job_id=job_id, exc_info=True)
                await self._finalize_diff_safe(job_id, worktree_path, base_ref)
                await self._fail_job(job_id, "Re-planning failed unexpectedly")
                return JobState.failed

        # Approved — start the implementation session
        log.info("plan_mode.plan_approved", job_id=job_id, step_count=len(plan_steps))

        # Persist phase transition so crash recovery knows this is the implementation phase.
        # Both state and mode update in one transaction to avoid a window where
        # mode=plan + state=running could cause recovery to replay the planning phase.
        from backend.persistence.database import serialized_write

        async with serialized_write(self._session_factory) as sess:
            job_repo = JobRepository(sess)
            # Guard against a cancel that slipped in while we processed the approval
            current_job = await job_repo.get(job_id)
            if current_job is None or current_job.state == JobState.canceled:
                log.info("plan_mode.canceled_before_implementation", job_id=job_id)
                return JobState.canceled
            svc = self._make_job_service(sess)
            await svc.transition_state(job_id, JobState.running)
            await job_repo.update_mode(job_id, JobMode.plan_implementing)
        await self._publish_state_event(job_id, JobState.waiting_for_approval, JobState.running)
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.job_mode_changed,
                payload={
                    "previous_mode": JobMode.plan,
                    "new_mode": JobMode.plan_implementing,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        )

        # Build implementation handoff prompt
        impl_prompt = build_implementation_handoff(
            job,
            plan_text,
            curated_context=curated_context,
            operator_notes=operator_notes,
        )

        # Fresh session config — planning tokens are gone
        impl_config = dataclass_replace(
            config,
            prompt=impl_prompt,
            resume_sdk_session_id=None,  # fresh session, no reuse
            memory_context=curated_context or None,
        )

        new_agent_session = AgentSession()
        self._agent_sessions[job_id] = new_agent_session
        result = await self._execute_session_attempt(
            job_id,
            new_agent_session,
            impl_config,
            worktree_path,
            base_ref,
            session_number=session_number + 1,
        )

        if result.error_reason:
            await self._finalize_diff_safe(job_id, worktree_path, base_ref)
            await self._fail_job(job_id, result.error_reason)
            return JobState.failed

        # Implementation session completed — normal completion flow
        final_state = await self._handle_successful_completion(
            job_id,
            impl_config,
            result.session_id,
            worktree_path,
            base_ref,
            post_conflict_merge_requested=False,
            session_number=session_number + 1,
        )
        return final_state

    # -- Preflight context curation -------------------------------------------

    async def _run_preflight_curator(
        self,
        job: Job,
        session_config: SessionConfig,
        worktree_override: str | None = None,
    ) -> SessionConfig:
        """Run the preflight curator agent to produce curated context.

        The utility-model Scout explores the job worktree with read/search
        tools and produces a brief for the main agent's system prompt.
        Returns *session_config* with ``memory_context`` populated (or
        unchanged on failure).
        """
        from backend.models.secondary_session import EntryKind, SecondarySessionKind, SecondarySessionStatus
        from backend.persistence.secondary_session_repo import SecondarySessionRepository
        from backend.services.tools.preflight_curator import PreflightReport

        worktree_path = worktree_override or job.worktree_path

        if self._preflight_curator is None:
            log.debug("preflight_curator.not_configured", job_id=job.id)
            return session_config

        session_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        repo = SecondarySessionRepository(self._session_factory)
        seq_counter = 0
        usage_report = PreflightReport()

        try:
            # Persist + emit started
            await repo.create_session(
                session_id=session_id,
                job_id=job.id,
                kind=SecondarySessionKind.preflight.value,
                name="Preflight Scout",
                icon="search",
                started_at=started_at,
            )
            await self._event_bus.publish(
                new_event(
                    session_id=job.id,
                    timestamp=started_at,
                    kind=EventKind.secondary_session_started,
                    payload={
                        "session_id": session_id,
                        "kind": SecondarySessionKind.preflight.value,
                        "name": "Preflight Scout",
                        "icon": "search",
                    },
                )
            )

            async def _on_tool_call(tc: PreflightToolCall) -> None:
                nonlocal seq_counter
                seq_counter += 1
                now = datetime.now(UTC)

                # Enrich via the shared pipeline (same as main transcript)
                from backend.services.events.event_enricher import build_tool_call_payload

                enriched = build_tool_call_payload(
                    tool_name=tc.tool_name,
                    tool_args=tc.tool_args,
                    result_text=tc.result_text,
                    sdk_success=tc.success,
                    turn_id=None,
                    duration_ms=tc.duration_ms,
                )

                entry_payload = {
                    "seq": seq_counter,
                    "kind": EntryKind.tool_call.value,
                    "content": tc.result_text[:500] if tc.result_text else "",
                    "tool_name": tc.tool_name,
                    "tool_args": tc.tool_args,
                    "duration_ms": tc.duration_ms,
                    "tool_result": enriched.get("tool_result"),
                    "tool_display": enriched.get("tool_display"),
                    "tool_display_full": enriched.get("tool_display_full"),
                    "tool_success": enriched.get("tool_success"),
                    "tool_issue": enriched.get("tool_issue"),
                    "tool_visibility": enriched.get("tool_visibility"),
                }
                await repo.add_entry(
                    session_id=session_id,
                    seq=seq_counter,
                    timestamp=now,
                    kind=EntryKind.tool_call.value,
                    content=tc.result_text[:500] if tc.result_text else "",
                    tool_name=tc.tool_name,
                    tool_args=tc.tool_args,
                    duration_ms=tc.duration_ms,
                    tool_result=enriched.get("tool_result"),
                    tool_display=enriched.get("tool_display"),
                    tool_display_full=enriched.get("tool_display_full"),
                    tool_success=enriched.get("tool_success"),
                    tool_issue=enriched.get("tool_issue"),
                    tool_visibility=enriched.get("tool_visibility"),
                )
                await self._event_bus.publish(
                    new_event(
                        session_id=job.id,
                        timestamp=now,
                        kind=EventKind.secondary_session_entry,
                        payload={"session_id": session_id, "entry": entry_payload},
                    )
                )

            async def _on_reasoning(text: str) -> None:
                nonlocal seq_counter
                seq_counter += 1
                now = datetime.now(UTC)
                entry_payload = {
                    "seq": seq_counter,
                    "kind": EntryKind.reasoning.value,
                    "content": text,
                }
                await repo.add_entry(
                    session_id=session_id,
                    seq=seq_counter,
                    timestamp=now,
                    kind=EntryKind.reasoning.value,
                    content=text,
                )
                await self._event_bus.publish(
                    new_event(
                        session_id=job.id,
                        timestamp=now,
                        kind=EventKind.secondary_session_entry,
                        payload={"session_id": session_id, "entry": entry_payload},
                    )
                )

            async def _on_usage(observed: PreflightReport) -> None:
                nonlocal usage_report
                usage_report = observed

            if not worktree_path:
                raise ValueError("Scout requires a job worktree")
            report = await self._preflight_curator.curate(
                task=session_config.prompt,
                repo=str(job.repo),
                worktree=worktree_path,
                job_id=job.id,
                on_tool_call=_on_tool_call,
                on_reasoning=_on_reasoning,
                on_usage=_on_usage,
            )

            # Emit completed
            completed_at = datetime.now(UTC)
            await repo.complete_session(
                session_id,
                status=SecondarySessionStatus.completed.value,
                completed_at=completed_at,
                output=report.brief or None,
                input_tokens=report.input_tokens,
                output_tokens=report.output_tokens,
                cost_usd=report.cost_usd,
                metadata={"model": report.model, "requested_model": self._config.runtime.utility_model},
            )
            await self._event_bus.publish(
                new_event(
                    session_id=job.id,
                    timestamp=completed_at,
                    kind=EventKind.secondary_session_completed,
                    payload={
                        "session_id": session_id,
                        "status": SecondarySessionStatus.completed.value,
                        "output": report.brief or None,
                        "input_tokens": report.input_tokens,
                        "output_tokens": report.output_tokens,
                        "cost_usd": report.cost_usd,
                    },
                )
            )

            if report.brief:
                log.info(
                    "preflight_curator.curated",
                    job_id=job.id,
                    curated_len=len(report.brief),
                    tool_call_count=len(report.tool_calls),
                )
                # Emit context_handoff so the frontend can show what was passed
                await self._event_bus.publish(
                    new_event(
                        session_id=job.id,
                        timestamp=completed_at,
                        kind=EventKind.context_handoff,
                        payload={
                            "source": "preflight",
                            "source_session_id": session_id,
                            "summary": f"Briefed agent on repository structure ({len(report.tool_calls)} explorations)",
                            "content": report.brief,
                        },
                    )
                )
                return dataclass_replace(session_config, memory_context=report.brief)
            log.debug("preflight_curator.nothing_relevant", job_id=job.id)
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                log.info("preflight_curator.curation_cancelled", job_id=job.id)
            else:
                log.warning("preflight_curator.curation_failed", job_id=job.id, exc_info=True)
            failed_report = usage_report
            # Mark session failed if it was created
            try:
                await repo.complete_session(
                    session_id,
                    status=(
                        SecondarySessionStatus.timeout.value
                        if isinstance(exc, TimeoutError)
                        else SecondarySessionStatus.failed.value
                    ),
                    completed_at=datetime.now(UTC),
                    input_tokens=failed_report.input_tokens,
                    output_tokens=failed_report.output_tokens,
                    cost_usd=failed_report.cost_usd,
                    metadata={"model": failed_report.model, "requested_model": self._config.runtime.utility_model},
                )
                await self._event_bus.publish(
                    new_event(
                        session_id=job.id,
                        timestamp=datetime.now(UTC),
                        kind=EventKind.secondary_session_completed,
                        payload={
                            "session_id": session_id,
                            "status": (
                                SecondarySessionStatus.timeout.value
                                if isinstance(exc, TimeoutError)
                                else SecondarySessionStatus.failed.value
                            ),
                            "input_tokens": failed_report.input_tokens,
                            "output_tokens": failed_report.output_tokens,
                            "cost_usd": failed_report.cost_usd,
                        },
                    )
                )
            except Exception:
                log.warning("preflight_curator.failure_persist_failed", job_id=job.id, exc_info=True)
            if isinstance(exc, asyncio.CancelledError):
                raise

        return session_config

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down
