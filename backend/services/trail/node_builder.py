"""Trail node builder — event → deterministic trail node creation."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select as sa_select
from sqlalchemy.exc import DBAPIError

from backend.models.db import JobRow, TrailNodeRow
from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent
from backend.services.tool_formatters import format_tool_display
from backend.services.trail.models import (
    MESSAGE_SIGNAL_BUFFER_SIZE,
    Activity,
    ActivityStep,
    PlanStep,
    TrailJobState,
    make_node_id,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.persistence.trail_repo import TrailNodeRepository
    from backend.services.trail.plan_manager import PlanManager

log = structlog.get_logger()


_HANDLED_EVENT_KINDS = TRANSCRIPT_KINDS | {
    EventKind.job_state_changed,
    EventKind.session_resumed,
    EventKind.step_completed,
    EventKind.step_started,
    EventKind.execution_phase_changed,
    EventKind.approval_requested,
    EventKind.job_completed,
    EventKind.job_failed,
    EventKind.job_canceled,
    EventKind.job_review,
}


# ---------------------------------------------------------------------------
# Snippet extraction (shared with StoryService — pure function)
# ---------------------------------------------------------------------------


def _extract_snippet(tool_args_json: str | None, tool_name: str | None) -> str:
    """Extract a compact code snippet from tool_args_json.

    Shows old→new for replacements, first lines for creates/inserts.
    """
    if not tool_args_json:
        return ""
    try:
        args = json.loads(tool_args_json) if isinstance(tool_args_json, str) else tool_args_json
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(args, dict):
        return ""

    max_lines = 8

    old_str = str(args.get("old_str", "") or args.get("oldString", "") or args.get("old_string", "") or "")
    new_str = str(args.get("new_str", "") or args.get("newString", "") or args.get("new_string", "") or "")
    if old_str or new_str:
        old_lines = old_str.strip().splitlines()[:max_lines]
        new_lines = new_str.strip().splitlines()[:max_lines]
        parts: list[str] = []
        for line in old_lines:
            parts.append(f"- {line}")
        for line in new_lines:
            parts.append(f"+ {line}")
        return "\n".join(parts)

    content = str(args.get("file_text", "") or args.get("content", ""))
    if content:
        lines = [ln for ln in content.strip().splitlines() if ln.strip()][:max_lines]
        return "\n".join(f"+ {ln}" for ln in lines)

    new_text = str(args.get("new_text", "") or args.get("newText", "") or "")
    if new_text:
        lines = new_text.strip().splitlines()[:max_lines]
        return "\n".join(f"+ {ln}" for ln in lines)

    return ""


def classify_step(payload: dict[str, Any]) -> str:
    """Assign node kind from structured step/event data. No LLM."""
    files_written = payload.get("files_written") or []
    files_read = payload.get("files_read") or []
    start_sha = payload.get("start_sha")
    end_sha = payload.get("end_sha")

    if files_written:
        return "modify"
    if start_sha and end_sha and start_sha != end_sha:
        return "modify"
    if files_read:
        return "explore"
    return "shell"


class TrailNodeBuilder:
    """Builds deterministic trail nodes from domain events."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        job_state: dict[str, TrailJobState],
        repo: TrailNodeRepository,
        plan_manager: PlanManager | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._job_state = job_state
        self._repo = repo
        self._plan_manager = plan_manager
        # Background tasks for non-blocking plan classification
        self._background_tasks: set[asyncio.Task[None]] = set()
        # Per-job locks to serialize event processing within a job.
        # Prevents race between _on_session_resumed (async state load) and
        # concurrent _on_step_completed calls that would early-return on missing state.
        self._job_locks: dict[str, asyncio.Lock] = {}

    async def flush_background_tasks(self) -> None:
        """Await all pending background enrichment tasks.  Used by tests and shutdown."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    async def handle_event(self, event: SessionEvent) -> None:
        """Domain event subscriber — builds deterministic trail nodes."""
        job_id = event.session_id
        if not job_id:
            return
        # Terminal handling waits for enrichment that publishes ignored plan events.
        # Those events must not re-enter the same per-job mutation lock.
        if event.kind not in _HANDLED_EVENT_KINDS:
            return

        # Serialize event processing per job to prevent races between
        # _on_session_resumed (which does async state loading) and concurrent
        # events that would early-return on missing state.
        lock = self._job_locks.get(job_id)
        if lock is None:
            lock = asyncio.Lock()
            self._job_locks[job_id] = lock

        async with lock:
            try:
                if event.kind == EventKind.job_state_changed:
                    new_state = (event.payload or {}).get("new_state")
                    if new_state == "running" and event.session_id not in self._job_state:
                        await self._on_job_started(event)
                elif event.kind == EventKind.session_resumed:
                    await self._on_session_resumed(event)
                elif event.kind == EventKind.step_completed:
                    await self._on_step_completed(event)
                elif event.kind == EventKind.step_started:
                    self._on_step_started(event)
                elif event.kind == EventKind.execution_phase_changed:
                    await self._on_phase_changed(event)
                elif event.kind in TRANSCRIPT_KINDS:
                    await self._on_transcript_updated(event)
                elif event.kind == EventKind.approval_requested:
                    await self._on_approval_requested(event)
                elif event.kind in (
                    EventKind.job_completed,
                    EventKind.job_failed,
                    EventKind.job_canceled,
                    EventKind.job_review,
                ):
                    await self._on_job_terminal(event)
            except Exception:  # Safety-net: protect event loop from unexpected failures
                log.warning("trail_event_error", event_kind=event.kind, job_id=event.session_id, exc_info=True)

    async def _on_session_resumed(self, event: SessionEvent) -> None:
        """Rehydrate trail state when a job session resumes.

        §13.5: Try loading from persisted snapshot first (lossless).
        Fall back to reconstruction from trail nodes (lossy).
        """
        job_id = event.session_id
        assert job_id is not None
        if job_id in self._job_state:
            return

        # §13.5: Try snapshot-based recovery first
        state = await self._load_snapshot(job_id)
        if state is not None:
            # Ensure seq counter is at least as high as persisted nodes
            max_seq = await self._repo.max_seq(job_id)
            if state.next_seq <= max_seq:
                state.next_seq = max_seq + 1
            self._job_state[job_id] = state
            log.info(
                "trail_state_restored_from_snapshot",
                job_id=job_id,
                seq=state.next_seq,
                plan_steps=len(state.plan_steps),
                activities=len(state.activities),
            )
            return

        # Lossy fallback: reconstruct from trail nodes + events
        state = TrailJobState()

        # Restore seq counter from persisted nodes
        max_seq = await self._repo.max_seq(job_id)
        state.next_seq = max_seq + 1

        # Restore goal and prompt from persisted goal node
        goal_nodes = await self._repo.get_by_job(job_id, kinds=["goal"], limit=1)
        if goal_nodes:
            state.active_goal_id = goal_nodes[0].id
            state.job_prompt = goal_nodes[0].intent or ""

        # Restore plan steps from persisted PlanStepUpdated events
        from backend.persistence.event_repo import EventRepository

        async with self._session_factory() as session:
            event_repo = EventRepository(session)
            # Agents produce ~5-20 plan steps with ~3 state transitions each
            # (pending → active → completed), yielding 15-60 events in practice.
            # 200 provides ~3× headroom over observed maximums.
            plan_events = await event_repo.list_by_job(
                job_id,
                [EventKind.plan_step_updated],
                limit=200,
            )
        if plan_events:
            latest_by_id: dict[str, SessionEvent] = {}
            for ev in plan_events:
                p = ev.payload
                ps_id = p.get("plan_step_id")
                if ps_id:
                    latest_by_id[ps_id] = ev
            steps: list[PlanStep] = []
            for ps_id, ev in latest_by_id.items():
                p = ev.payload
                ps = PlanStep(
                    plan_step_id=ps_id,
                    label=str(p.get("label", "")),
                    status=str(p.get("status", "pending")),
                    order=p.get("order", 0) or 0,
                    summary=p.get("summary"),
                    tool_count=p.get("tool_count", 0) or 0,
                    files_written=p.get("files_written") or [],
                    duration_ms=p.get("duration_ms", 0) or 0,
                    start_sha=p.get("start_sha"),
                    end_sha=p.get("end_sha"),
                )
                steps.append(ps)
            steps.sort(key=lambda s: s.order)
            state.plan_steps = steps
            state.plan_established = bool(steps)
            state.active_idx = next((i for i, s in enumerate(steps) if s.status == "active"), -1)

        # Restore activity timeline from persisted trail nodes with titles.
        # Typical agent sessions produce 50-200 tool invocations (shell, file
        # edits, searches); 500 provides 2.5-10× headroom over observed runs.
        work_nodes = await self._repo.get_by_job(
            job_id,
            kinds=["shell", "modify", "explore"],
            limit=500,
        )
        for node in work_nodes:
            if node.turn_id and node.title and node.activity_id:
                act = next(
                    (a for a in state.activities if a.activity_id == node.activity_id),
                    None,
                )
                if act is None:
                    act = Activity(
                        activity_id=node.activity_id,
                        label=node.activity_label or node.plan_item_label or "Task",
                        status="active",
                    )
                    state.activities.append(act)
                state.activity_steps.append(
                    ActivityStep(
                        turn_id=node.turn_id,
                        title=node.title,
                        activity_id=node.activity_id,
                    )
                )

        self._job_state[job_id] = state
        log.info(
            "trail_state_rehydrated",
            job_id=job_id,
            seq=state.next_seq,
            plan_steps=len(state.plan_steps),
            activities=len(state.activities),
        )

    async def _on_job_started(self, event: SessionEvent) -> None:
        """Create the goal node for a new job."""
        job_id = event.session_id
        assert job_id is not None
        state = TrailJobState()
        self._job_state[job_id] = state

        node_id = make_node_id()
        seq = state.next_seq
        state.next_seq += 1

        # Fetch prompt from the job row
        prompt = ""
        try:
            async with self._session_factory() as session:
                result = await session.execute(sa_select(JobRow).where(JobRow.id == job_id))
                row = result.scalar_one_or_none()
                if row:
                    prompt = row.prompt or ""
        except DBAPIError:
            log.warning("trail_goal_prompt_fetch_failed", job_id=job_id, exc_info=True)

        state.job_prompt = prompt

        node = TrailNodeRow(
            id=node_id,
            job_id=job_id,
            seq=seq,
            anchor_seq=seq,
            parent_id=None,
            kind="goal",
            deterministic_kind="goal",
            phase=state.current_phase,
            timestamp=event.timestamp,
            enrichment="complete",
            intent=prompt or None,
            step_id=None,
            span_ids=None,
            turn_id=None,
            files=None,
            start_sha=None,
            end_sha=None,
        )
        state.active_goal_id = node_id
        await self._repo.create(node)
        log.debug("trail_goal_created", job_id=job_id, node_id=node_id)

    def _on_step_started(self, event: SessionEvent) -> None:
        """Track the currently active step for approval anchoring."""
        job_id = event.session_id
        assert job_id is not None
        state = self._job_state.get(job_id)
        if state:
            state.active_step_id = event.payload.get("step_id")

    async def _on_step_completed(self, event: SessionEvent) -> None:
        """Create a deterministic trail node from step completion data."""
        job_id = event.session_id
        assert job_id is not None
        state = self._job_state.get(job_id)
        if not state:
            return

        payload = event.payload
        if payload.get("status") == "canceled":
            return

        kind = classify_step(payload)
        step_id = payload.get("step_id")
        turn_id = payload.get("turn_id")

        files_read = payload.get("files_read") or []
        files_written = payload.get("files_written") or []
        all_files = list(dict.fromkeys(files_written + files_read))

        agent_message = payload.get("agent_message")
        preceding_context = payload.get("preceding_context")
        tool_names = payload.get("tool_names") or []
        tool_count = payload.get("tool_count") or 0
        duration_ms = payload.get("duration_ms") or 0

        node_id = make_node_id()
        seq = state.next_seq
        state.next_seq += 1

        node = TrailNodeRow(
            id=node_id,
            job_id=job_id,
            seq=seq,
            anchor_seq=seq,
            parent_id=state.active_goal_id,
            kind=kind,
            deterministic_kind=kind,
            phase=state.current_phase,
            timestamp=event.timestamp,
            enrichment="pending",
            step_id=step_id,
            turn_id=turn_id,
            files=json.dumps(all_files, ensure_ascii=False) if all_files else None,
            start_sha=payload.get("start_sha"),
            end_sha=payload.get("end_sha"),
            preceding_context=preceding_context,
            agent_message=agent_message,
            tool_names=json.dumps(tool_names, ensure_ascii=False) if tool_names else None,
            tool_count=tool_count,
            duration_ms=duration_ms,
            diff_additions=payload.get("diff_additions"),
            diff_deletions=payload.get("diff_deletions"),
        )
        await self._repo.create(node)
        log.debug(
            "trail_step_node_created",
            job_id=job_id,
            node_id=node_id,
            kind=kind,
            step_id=step_id,
        )

        # Create write sub-nodes for modify steps (§13.1)
        if kind == "modify" and turn_id:
            await self._create_write_sub_nodes(
                job_id=job_id,
                parent_node_id=node_id,
                anchor_seq=seq,
                turn_id=turn_id,
                step_id=step_id,
                phase=state.current_phase,
                timestamp=event.timestamp,
                state=state,
            )

        # Emit any pending events waiting for this step
        if state.pending_events:
            pending = state.pending_events[:]
            state.pending_events.clear()
            for pending_event in pending:
                await self._emit_pending_event(pending_event, state, anchor_seq=seq)

        # --- Plan classification + title + SSE (fire-and-forget, §13.4) ---
        # Spawn as a background task so LLM calls for title generation and plan
        # classification don't block the event bus (and therefore the agent event
        # processing loop).  The trail node is already persisted above.
        task = asyncio.create_task(
            self._classify_and_snapshot(job_id, node_id, payload, state),
            name=f"trail-enrich-{job_id}-{node_id[:8]}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _create_write_sub_nodes(
        self,
        *,
        job_id: str,
        parent_node_id: str,
        anchor_seq: int,
        turn_id: str,
        step_id: str | None,
        phase: str | None,
        timestamp: datetime,
        state: TrailJobState,
    ) -> None:
        """Create write sub-nodes from file_write telemetry spans (§13.1).

        One ``write`` node per ``file_write`` span, as children of the parent
        ``modify`` node.  Carries per-file granularity data that downstream
        consumers (StoryService, MotivationService, SummarizationService) need.
        """
        from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository

        try:
            async with self._session_factory() as session:
                spans_repo = TelemetrySpansRepository(session)
                spans = await spans_repo.file_write_spans_for_step(
                    job_id=job_id,
                    turn_id=turn_id,
                )

            if not spans:
                return

            write_nodes: list[TrailNodeRow] = []
            for span in spans:
                wn_id = make_node_id()
                seq = state.next_seq
                state.next_seq += 1

                file_path = span.get("tool_target") or ""
                snippet = _extract_snippet(
                    span.get("tool_args_json"),
                    span.get("name"),
                )

                write_nodes.append(
                    TrailNodeRow(
                        id=wn_id,
                        job_id=job_id,
                        seq=seq,
                        anchor_seq=anchor_seq,
                        parent_id=parent_node_id,
                        kind="write",
                        deterministic_kind="write",
                        phase=phase,
                        timestamp=timestamp,
                        enrichment="complete",
                        step_id=step_id,
                        turn_id=turn_id,
                        files=json.dumps([file_path], ensure_ascii=False) if file_path else None,
                        tool_name=span.get("name"),
                        snippet=snippet or None,
                        is_retry=bool(span.get("is_retry")) if span.get("is_retry") is not None else None,
                        error_kind=span.get("error_kind"),
                        write_summary=span.get("motivation_summary"),
                        edit_motivations=span.get("edit_motivations"),
                        preceding_context=span.get("preceding_context"),
                    )
                )

            if write_nodes:
                await self._repo.create_many(write_nodes)
                log.debug(
                    "trail_write_sub_nodes_created",
                    job_id=job_id,
                    parent_id=parent_node_id,
                    count=len(write_nodes),
                )

        except (DBAPIError, OSError):
            # Write sub-node creation is best-effort — don't break the hot path
            log.warning(
                "trail_write_sub_nodes_failed",
                job_id=job_id,
                parent_id=parent_node_id,
                exc_info=True,
            )

    async def _classify_and_snapshot(
        self,
        job_id: str,
        node_id: str,
        payload: dict[str, Any],
        state: TrailJobState,
    ) -> None:
        """Background wrapper: classify + emit title, then save snapshot."""
        await self._classify_and_emit(job_id, node_id, payload)
        # §13.5: Periodic snapshot (every step_completed is a natural checkpoint)
        await self._save_snapshot(job_id, state)

    async def _classify_and_emit(
        self,
        job_id: str,
        node_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Classify turn to plan item, generate title, emit SSE events."""
        try:
            await self._classify_and_emit_inner(job_id, node_id, payload)
        except Exception:  # Safety-net: fire-and-forget task must not propagate
            log.warning(
                "classify_and_emit_failed",
                job_id=job_id,
                node_id=node_id,
                exc_info=True,
            )
            try:
                await self._repo.update_enrichment(node_id, enrichment="pending")
            except DBAPIError:
                log.warning("enrichment_status_update_failed", node_id=node_id, exc_info=True)

    async def _classify_and_emit_inner(
        self,
        job_id: str,
        node_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Inner implementation of classify-and-emit.

        Turn summaries are now produced by the TraceForge title pipeline
        (boundary + ONNX model inference → TitleUpdate → turn_summary events)
        wired in EventProcessor.  This method only handles plan classification.
        """
        state = self._job_state.get(job_id)
        if not state:
            return

        # Delegate plan classification to PlanManager.
        if self._plan_manager:
            try:
                await self._plan_manager.classify_turn(job_id, payload)
            except Exception:
                log.warning("classify_turn_failed", job_id=job_id, node_id=node_id, exc_info=True)

    async def _emit_pending_event(
        self,
        event: SessionEvent,
        state: TrailJobState,
        anchor_seq: int,
    ) -> None:
        """Emit a deferred event (e.g. approval_requested before step_completed)."""
        job_id = event.session_id
        assert job_id is not None
        node_id = make_node_id()
        seq = state.next_seq
        state.next_seq += 1

        node = TrailNodeRow(
            id=node_id,
            job_id=job_id,
            seq=seq,
            anchor_seq=anchor_seq,
            parent_id=state.active_goal_id,
            kind="request",
            deterministic_kind="request",
            phase=state.current_phase,
            timestamp=event.timestamp,
            enrichment="complete",
            intent=event.payload.get("description"),
            step_id=state.active_step_id,
        )
        await self._repo.create(node)
        log.debug("trail_request_node_created", job_id=job_id, node_id=node_id)

    async def _on_transcript_updated(self, event: SessionEvent) -> None:
        """Create or update trail nodes for transcript messages.

        Handles three transcript kinds:
        - message.user: Creates request nodes (operator messages).
        - tool.call.completed: Updates matching write sub-nodes with tool metadata
          (tool_display, tool_intent, tool_success) per §13.3.
        - message.assistant: Updates step node agent_message if not already set.
        """
        job_id = event.session_id
        assert job_id is not None
        state = self._job_state.get(job_id)
        if not state:
            return

        if event.kind == EventKind.message_user:
            await self._handle_operator_transcript(event, state)
        elif event.kind in (EventKind.tool_call_completed, EventKind.tool_call_failed):
            await self._handle_tool_call_transcript(event, state)
        elif event.kind == EventKind.message_assistant:
            await self._handle_assistant_transcript(event, state)

    async def _handle_operator_transcript(
        self,
        event: SessionEvent,
        state: TrailJobState,
    ) -> None:
        """Create a request node for an operator/user transcript message."""
        job_id = event.session_id
        assert job_id is not None
        payload = event.payload or {}
        content = str(payload.get("content", "")).strip()
        if not content:
            return

        # §13.7: Track operator messages for activity boundary detection
        state.recent_messages.append(f"[operator] {content}")
        if len(state.recent_messages) > MESSAGE_SIGNAL_BUFFER_SIZE:
            state.recent_messages = state.recent_messages[-MESSAGE_SIGNAL_BUFFER_SIZE:]

        node_id = make_node_id()
        seq = state.next_seq
        state.next_seq += 1

        node = TrailNodeRow(
            id=node_id,
            job_id=job_id,
            seq=seq,
            anchor_seq=seq,
            parent_id=state.active_goal_id,
            kind="request",
            deterministic_kind="request",
            phase=state.current_phase,
            timestamp=event.timestamp,
            enrichment="complete",
            agent_message=content,
            step_id=state.active_step_id,
        )
        await self._repo.create(node)
        log.debug("trail_operator_message_created", job_id=job_id, node_id=node_id)

    async def _handle_tool_call_transcript(
        self,
        event: SessionEvent,
        state: TrailJobState,
    ) -> None:
        """Update matching write sub-node with tool metadata (§13.3).

        When a tool_call transcript event arrives, find the write sub-node
        matching (job_id, turn_id, tool_name) and populate tool_display,
        tool_intent, tool_success. Skips report_intent (frontend-only).
        """
        payload = event.payload or {}
        tool_name = payload.get("tool_name", "")
        if not tool_name or tool_name == "report_intent":
            return

        turn_id = payload.get("turn_id")
        if not turn_id:
            return
        if not event.session_id:
            return

        tool_display = event.metadata.tool_display or None
        if tool_display is None:
            arguments = payload.get("arguments")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments, ensure_ascii=False)
            elif arguments is not None and not isinstance(arguments, str):
                arguments = str(arguments)
            tool_display = format_tool_display(str(tool_name), arguments)
        motivation = getattr(event.metadata, "motivation", None)
        tool_intent = getattr(motivation, "intent", None) or None
        tool_success = False if event.kind == EventKind.tool_call_failed else payload.get("success")
        if tool_success is not None:
            tool_success = bool(tool_success)

        updated = await self._repo.update_tool_metadata(
            event.session_id,
            turn_id,
            tool_name,
            tool_display=tool_display,
            tool_intent=tool_intent,
            tool_success=tool_success,
        )
        if updated:
            log.debug(
                "trail_tool_metadata_updated",
                job_id=event.session_id,
                turn_id=turn_id,
                tool_name=tool_name,
            )

    async def _handle_assistant_transcript(
        self,
        event: SessionEvent,
        state: TrailJobState,
    ) -> None:
        """Track assistant message content for activity boundary detection.

        The primary agent_message is set via step_completed. This handler
        tracks assistant messages for the recent_messages buffer used by
        §13.7 activity boundary detection.
        """
        payload = event.payload or {}
        content = str(payload.get("content", "")).strip()
        if not content:
            return

        # §13.7: Track for activity boundary detection
        # Truncate long messages to keep the buffer manageable
        summary = content[:200] if len(content) > 200 else content
        state.recent_messages.append(f"[assistant] {summary}")
        if len(state.recent_messages) > MESSAGE_SIGNAL_BUFFER_SIZE:
            state.recent_messages = state.recent_messages[-MESSAGE_SIGNAL_BUFFER_SIZE:]

    async def _on_phase_changed(self, event: SessionEvent) -> None:
        """Create a summarize node for execution phase transitions."""
        job_id = event.session_id
        assert job_id is not None
        state = self._job_state.get(job_id)
        if not state:
            return

        phase = str(event.payload.get("phase", "unknown"))
        state.current_phase = phase

        node_id = make_node_id()
        seq = state.next_seq
        state.next_seq += 1

        node = TrailNodeRow(
            id=node_id,
            job_id=job_id,
            seq=seq,
            anchor_seq=seq,
            parent_id=state.active_goal_id,
            kind="summarize",
            deterministic_kind="summarize",
            phase=phase,
            timestamp=event.timestamp,
            enrichment="complete",
            intent=f"Phase: {phase}",
        )
        await self._repo.create(node)
        log.debug("trail_summarize_created", job_id=job_id, phase=phase)

    async def _on_approval_requested(self, event: SessionEvent) -> None:
        """Create a request node or defer if step hasn't completed yet."""
        job_id = event.session_id
        assert job_id is not None
        state = self._job_state.get(job_id)
        if not state:
            return

        if state.active_step_id:
            state.pending_events.append(event)
            log.debug("trail_request_deferred", job_id=job_id)
        else:
            node_id = make_node_id()
            seq = state.next_seq
            state.next_seq += 1

            node = TrailNodeRow(
                id=node_id,
                job_id=job_id,
                seq=seq,
                anchor_seq=seq,
                parent_id=state.active_goal_id,
                kind="request",
                deterministic_kind="request",
                phase=state.current_phase,
                timestamp=event.timestamp,
                enrichment="complete",
                intent=event.payload.get("description"),
            )
            await self._repo.create(node)
            log.debug("trail_request_created", job_id=job_id, node_id=node_id)

    async def _on_job_terminal(self, event: SessionEvent) -> None:
        """Create a terminal summarize node and clean up."""
        job_id = event.session_id
        assert job_id is not None
        state = self._job_state.get(job_id)
        if not state:
            return

        # Flush pending background tasks for this job before cleanup.
        # Without this, fast event streams (e.g. tailed JSONL) can reach
        # terminal before title-generation tasks run — they'd find job_state
        # deleted and silently skip activity tracking.
        job_tasks = [t for t in self._background_tasks if t.get_name().startswith(f"trail-enrich-{job_id}")]
        if job_tasks:
            await asyncio.gather(*job_tasks, return_exceptions=True)

        node_id = make_node_id()
        seq = state.next_seq
        state.next_seq += 1

        status = "completed" if event.kind == EventKind.job_completed else "failed"
        if event.kind == EventKind.job_canceled:
            status = "canceled"
        elif event.kind == EventKind.job_review:
            status = "ready for review"

        node = TrailNodeRow(
            id=node_id,
            job_id=job_id,
            seq=seq,
            anchor_seq=seq,
            parent_id=state.active_goal_id,
            kind="summarize",
            deterministic_kind="summarize",
            phase="terminal",
            timestamp=event.timestamp,
            enrichment="complete",
            intent=f"Job {status}",
        )
        await self._repo.create(node)

        # §13.5: Save final snapshot before cleanup
        await self._save_snapshot(job_id, state)

        del self._job_state[job_id]
        self._job_locks.pop(job_id, None)
        log.debug("trail_job_terminal", job_id=job_id, status=status)

    # ------------------------------------------------------------------
    # §13.5: Snapshot persistence
    # ------------------------------------------------------------------

    async def _save_snapshot(self, job_id: str, state: TrailJobState) -> None:
        """Persist TrailJobState snapshot to jobs.trail_state_snapshot."""
        try:
            snapshot_json = json.dumps(state.to_snapshot(), ensure_ascii=False)
            from sqlalchemy import update as sa_update

            async with self._session_factory() as session:
                stmt = sa_update(JobRow).where(JobRow.id == job_id).values(trail_state_snapshot=snapshot_json)
                await session.execute(stmt)
                await session.commit()
        except Exception:
            log.debug("trail_snapshot_save_failed", job_id=job_id, exc_info=True)

    async def _load_snapshot(self, job_id: str) -> TrailJobState | None:
        """Load TrailJobState from persisted snapshot, if available."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(sa_select(JobRow.trail_state_snapshot).where(JobRow.id == job_id))
                raw = result.scalar_one_or_none()
                if not raw:
                    return None
                data = json.loads(raw)
                return TrailJobState.from_snapshot(data)
        except Exception:
            log.debug("trail_snapshot_load_failed", job_id=job_id, exc_info=True)
            return None
