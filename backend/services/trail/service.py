"""TrailService facade — thin orchestrator composing trail subsystem components."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from backend.config import TrailConfig
from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent, new_event
from backend.persistence.trail_repo import TrailNodeRepository
from backend.services.tools.parsing_utils import ensure_dict
from backend.services.trail.enricher import TrailEnricher
from backend.services.trail.node_builder import TrailNodeBuilder
from backend.services.trail.plan_manager import PlanManager
from backend.services.trail.query_service import TrailQueryService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.services.events.event_bus import EventBus
    from backend.services.sidecar.session import SidecarSessionManager
    from backend.services.trail.models import TrailJobState, TrailResponse, TrailSummary

log = structlog.get_logger()


def _plan_role_for_kind(kind: EventKind | str) -> str | None:
    if kind == EventKind.message_assistant:
        return "agent"
    if kind == EventKind.message_user:
        return "operator"
    if kind == EventKind.tool_call_completed:
        return "tool_call"
    return None


class TrailService:
    """Thin facade composing trail subsystem components.

    Single entry point for the rest of the application — delegates to
    TrailNodeBuilder, PlanManager, TrailEnricher, and TrailQueryService.

    Turn summaries are now produced by the TraceForge title pipeline
    (boundary + ONNX model inference → TitleUpdate → turn_summary events)
    wired in EventProcessor.  The previous LLM-based ActivityTracker +
    TitleGenerator path has been retired.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        sidecar_sessions: SidecarSessionManager | None = None,
        config: TrailConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._sidecar_sessions = sidecar_sessions
        self._config = config or TrailConfig()

        # Shared state
        self._job_state: dict[str, TrailJobState] = {}
        self._repo = TrailNodeRepository(session_factory)

        # Components
        self._plan_manager = PlanManager(
            event_bus=event_bus,
            job_state=self._job_state,
            sidecar_sessions=sidecar_sessions,
        )

        self._node_builder = TrailNodeBuilder(
            session_factory=session_factory,
            job_state=self._job_state,
            repo=self._repo,
            plan_manager=self._plan_manager,
        )

        self._enricher = TrailEnricher(
            session_factory=session_factory,
            event_bus=event_bus,
            sidecar_sessions=sidecar_sessions,
            config=self._config,
            job_state=self._job_state,
        )

        self._query = TrailQueryService(session_factory)

        # Per-job feature gates
        self._plan_tracking_disabled: set[str] = set()

    # ==================================================================
    # Feature gates
    # ==================================================================

    def disable_plan_tracking(self, job_id: str) -> None:
        """Opt a job out of plan inference and native-plan capture."""
        self._plan_tracking_disabled.add(job_id)

    def get_job_state(self, job_id: str) -> TrailJobState | None:
        """Return in-memory trail state for a job, or None if not tracked."""
        return self._job_state.get(job_id)

    # ==================================================================
    # Event handling (delegate to node builder + plan feed)
    # ==================================================================

    async def handle_event(self, event: SessionEvent) -> None:
        """SessionEvent subscriber — single entry point for all enrichment.

        Both managed (RuntimeService) and imported (IngestService) jobs
        publish the same SessionEvents to the EventBus.  All plan-feed,
        tool tracking, native-plan capture, and auto-naming is driven
        from here so both paths get identical treatment.
        """
        await self._node_builder.handle_event(event)

        if event.kind in TRANSCRIPT_KINDS:
            await self._on_transcript_event(event)

    async def _on_transcript_event(self, event: SessionEvent) -> None:
        """Feed plan manager and capture native plans from transcript events."""
        payload = event.payload or {}

        # Skip ephemeral streaming deltas — no plan value
        if event.kind == EventKind.message_delta:
            return

        job_id = event.session_id
        if not job_id:
            return
        content = str(payload.get("content", ""))
        motivation = getattr(event.metadata, "motivation", None)
        tool_intent = str(getattr(motivation, "intent", "") or "")
        plan_role = _plan_role_for_kind(event.kind)

        plan_disabled = job_id in self._plan_tracking_disabled
        if not plan_disabled and plan_role:
            await self._plan_manager.feed_transcript(job_id, plan_role, content, tool_intent)

        if event.kind == EventKind.tool_call_completed:
            tool_name = str(payload.get("tool_name", ""))
            if tool_name and not plan_disabled:
                await self._plan_manager.feed_tool_name(job_id, tool_name)
            # Native plan capture from the agent's own todo tool
            if not plan_disabled and tool_name in ("manage_todo_list", "TodoWrite"):
                await self._try_ingest_native_plan(job_id, payload)

        # Auto-generate a title for jobs that don't have one yet
        if event.kind == EventKind.message_assistant and content:
            await self._maybe_auto_title(job_id, content)

    async def _try_ingest_native_plan(
        self,
        job_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Extract plan steps from a manage_todo_list / TodoWrite tool call."""
        raw_args = payload.get("arguments")
        if not raw_args:
            return
        args = ensure_dict(raw_args)
        if args is None:
            return
        # Copilot: {"todoList": [...]}   Claude: {"todos": [...]}
        items = args.get("todoList") or args.get("todos") or []
        if not isinstance(items, list):
            return
        try:
            await self._plan_manager.feed_native_plan(job_id, items)
        except (ValueError, TypeError, KeyError):
            log.warning("native_plan_ingest_failed", job_id=job_id, exc_info=True)

    async def _maybe_auto_title(self, job_id: str, first_content: str) -> None:
        """Generate a job title from the first agent message if the job has none."""
        state = self._job_state.get(job_id)
        if not state:
            return
        # Only fire once per job
        if getattr(state, "_title_attempted", False):
            return
        state._title_attempted = True  # type: ignore[attr-defined]

        if not self._sidecar_sessions:
            return

        try:
            from sqlalchemy import select as sa_select

            from backend.models.db import JobRow

            async with self._session_factory() as session:
                title_val = (
                    await session.execute(sa_select(JobRow.title).where(JobRow.id == job_id))
                ).scalar_one_or_none()
                if title_val is not None:
                    # Already has a title — nothing to do
                    return

            # Generate title via one-shot sidecar session
            prompt = (
                "Given this agent's first message, generate a concise 3-8 word title "
                "for the coding task.  Respond with ONLY the title text, no quotes, "
                "no punctuation at the end.\n\n"
                f"Agent message:\n{first_content[:2000]}"
            )
            title = await self._sidecar_sessions.complete(prompt, timeout=10.0)
            title = str(title).strip().strip('"').strip("'")
            if not title or len(title) < 3:
                return

            # Persist and broadcast
            from backend.persistence.job_repo import JobRepository

            async with self._session_factory() as session:
                repo = JobRepository(session)
                await repo.update_title_and_branch(job_id, title=title)
                await session.commit()

            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=EventKind.job_title_updated,
                    payload={"title": title},
                )
            )
            log.info("trail_auto_title_generated", job_id=job_id, title=title)
        except Exception:
            log.debug("trail_auto_title_failed", job_id=job_id, exc_info=True)

    # ==================================================================
    # Data ingestion (delegate to plan manager)
    # ==================================================================

    async def start_tracking(self, job_id: str, prompt: str = "") -> None:
        """Initialize plan tracking for a job.

        NOTE: Kept for backward compatibility but largely redundant —
        ``_on_job_started`` already sets ``job_prompt`` from the DB row
        when the ``job_state_changed(running)`` event fires.
        """
        state = self._job_state.get(job_id)
        if state:
            state.job_prompt = prompt

    def stop_tracking(self, job_id: str) -> None:
        """No-op — cleanup happens in _on_job_terminal."""

    def cleanup(self, job_id: str) -> None:
        """Remove all in-memory state for a job."""
        self._job_state.pop(job_id, None)
        self._plan_tracking_disabled.discard(job_id)

    async def feed_transcript(
        self,
        job_id: str,
        role: str,
        content: str,
        tool_intent: str = "",
    ) -> None:
        """Buffer transcript data."""
        if job_id not in self._plan_tracking_disabled:
            await self._plan_manager.feed_transcript(job_id, role, content, tool_intent)

    async def feed_tool_name(self, job_id: str, tool_name: str) -> None:
        """Track tool usage."""
        if job_id not in self._plan_tracking_disabled:
            await self._plan_manager.feed_tool_name(job_id, tool_name)

    async def feed_native_plan(self, job_id: str, items: list[dict[str, str]]) -> None:
        """Create/update plan steps from the agent's native todo tool."""
        if job_id not in self._plan_tracking_disabled:
            await self._plan_manager.feed_native_plan(job_id, items)

    # ==================================================================
    # Plan queries (delegate to plan manager)
    # ==================================================================

    def get_active_plan_step_id(self, job_id: str) -> str | None:
        return self._plan_manager.get_active_plan_step_id(job_id)

    def get_plan_steps(self, job_id: str) -> list[dict[str, str]]:
        return self._plan_manager.get_plan_steps(job_id)

    # ==================================================================
    # Finalization (delegate to plan manager)
    # ==================================================================

    async def finalize(self, job_id: str, succeeded: bool) -> None:
        """Finalize plan steps on job completion."""
        if job_id not in self._plan_tracking_disabled:
            await self._plan_manager.finalize(job_id, succeeded)

    # ==================================================================
    # Enrichment drain (delegate to enricher)
    # ==================================================================

    async def drain_enrichment(self) -> int:
        return await self._enricher.drain_enrichment()

    async def drain_loop(self) -> None:
        await self._enricher.drain_loop()

    # ==================================================================
    # Query helpers (delegate to query service)
    # ==================================================================

    async def get_trail(
        self,
        job_id: str,
        *,
        kinds: list[str] | None = None,
        flat: bool = False,
        after_seq: int | None = None,
    ) -> TrailResponse:
        return await self._query.get_trail(
            job_id,
            kinds=kinds,
            flat=flat,
            after_seq=after_seq,
        )

    async def get_summary(self, job_id: str) -> TrailSummary:
        return await self._query.get_summary(job_id)
