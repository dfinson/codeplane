"""Plan-step orchestration — unified plan-item-as-step system.

Plan items are the user-visible steps.  SDK turns are invisible
implementation details bucketed into plan items by the sister session.

Sources of plan items:
1. Native: ``manage_todo_list`` / ``TodoWrite`` tool calls
2. Inferred: sister session generates plan from first agent message

On each SDK turn boundary (step_completed), the sister session
classifies which plan item the turn belongs to and generates a
1-2 sentence summary for that item.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.events import DomainEvent, DomainEventKind

if TYPE_CHECKING:
    from backend.services.event_bus import EventBus
    from backend.services.sister_session import SisterSession, SisterSessionManager

log = structlog.get_logger()

_MSG_MAX = 300
_TOOL_INTENT_MAX = 80


# ---------------------------------------------------------------------------
# Plan step model (in-memory)
# ---------------------------------------------------------------------------


@dataclass
class PlanStep:
    plan_step_id: str
    label: str
    summary: str | None = None
    status: str = "pending"  # pending | active | done | failed | skipped
    tool_count: int = 0
    files_written: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int = 0
    start_sha: str | None = None
    end_sha: str | None = None

    def to_event_payload(self) -> dict[str, Any]:
        return {
            "plan_step_id": self.plan_step_id,
            "label": self.label,
            "summary": self.summary,
            "status": self.status,
            "tool_count": self.tool_count,
            "files_written": self.files_written[:20] if self.files_written else [],
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms or None,
            "start_sha": self.start_sha,
            "end_sha": self.end_sha,
        }


def _make_plan_step_id() -> str:
    return f"ps-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Sister session prompts
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """\
You manage a plan for a coding task.  Given the current plan items and the \
latest completed work, determine:

1. Which plan item the work belongs to (by index, 1-based)
2. An updated 1-2 sentence summary for that item
3. Whether the item's status should change

Current plan:
{plan_block}

Latest completed work:
- Agent message: {agent_msg}
- Tools used: {tools}
- Tool intents: {intents}

Respond with JSON only:
{{"assign_to": <index>, "summary": "<1-2 sentence summary>", "status": "<active|done>"}}

RULES:
- assign_to is the 1-based index of the plan item this work belongs to.
- If the work clearly finishes this item, set status to "done".
- If work is ongoing, keep status as "active".
- Summary should describe what was specifically done in 1-2 sentences.
- Be concrete: mention files, functions, endpoints, not abstractions.
"""

_INFER_PLAN_PROMPT = """\
A coding agent just started working on this task.  Based on the task \
description and the agent's first message, infer a plan of 3-6 steps.

Task: {task}

Agent's first message:
{first_msg}

Respond with JSON only:
{{"items": ["Step 1 label", "Step 2 label", ...]}}

RULES:
- 3-6 items total.
- Each label: 3-8 words, imperative mood, concrete.
- Cover the full task arc from start to finish.
- Be specific: mention files, components, endpoints where possible.
"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ProgressTrackingService:
    """Orchestrates plan steps for active jobs.

    Plan items are the user-visible "steps".  SDK turns are assigned to
    plan items by the sister session.
    """

    def __init__(
        self,
        sister_sessions: SisterSessionManager,
        event_bus: EventBus,
    ) -> None:
        self._sister_sessions = sister_sessions
        self._event_bus = event_bus

        # Per-job plan steps (ordered list)
        self._plan_steps: dict[str, list[PlanStep]] = {}
        # Active plan step index per job
        self._active_idx: dict[str, int] = {}
        # Plan established? (native todo or inferred)
        self._plan_established: dict[str, bool] = {}
        # Jobs receiving native plan data
        self._native_plan_active: set[str] = set()
        # Transcript context buffers
        self._recent_messages: dict[str, list[str]] = {}
        self._recent_tool_intents: dict[str, list[str]] = {}
        self._recent_tool_names: dict[str, list[str]] = {}
        # Job task prompts (for plan inference)
        self._job_prompts: dict[str, str] = {}

    # -- Lifecycle -----------------------------------------------------------

    def start_tracking(self, job_id: str, prompt: str = "") -> None:
        self._plan_steps[job_id] = []
        self._active_idx[job_id] = -1
        self._plan_established[job_id] = False
        self._recent_messages[job_id] = []
        self._recent_tool_intents[job_id] = []
        self._recent_tool_names[job_id] = []
        self._job_prompts[job_id] = prompt

    def stop_tracking(self, job_id: str) -> None:
        pass

    def cleanup(self, job_id: str) -> None:
        for store in (
            self._plan_steps, self._active_idx, self._plan_established,
            self._recent_messages, self._recent_tool_intents,
            self._recent_tool_names, self._job_prompts,
        ):
            store.pop(job_id, None)  # type: ignore[arg-type]
        self._native_plan_active.discard(job_id)

    # -- Data ingestion ------------------------------------------------------

    async def feed_transcript(
        self,
        job_id: str,
        role: str,
        content: str,
        tool_intent: str = "",
    ) -> None:
        if role == "agent" and content:
            buf = self._recent_messages.get(job_id)
            if buf is not None:
                buf.append(content[:_MSG_MAX])
                if len(buf) > 5:
                    self._recent_messages[job_id] = buf[-5:]

                # Eagerly infer plan on first agent message so steps appear
                # immediately instead of waiting for the first step_completed.
                if len(buf) == 1 and not self._plan_established.get(job_id, False):
                    await self._try_early_plan(job_id)

        if role == "tool_call":
            if tool_intent:
                ibuf = self._recent_tool_intents.get(job_id)
                if ibuf is not None:
                    ibuf.append(tool_intent[:_TOOL_INTENT_MAX])
                    if len(ibuf) > 10:
                        self._recent_tool_intents[job_id] = ibuf[-10:]

    async def _try_early_plan(self, job_id: str) -> None:
        """Infer plan from the first agent message without waiting for step_completed."""
        sister = self._sister_sessions.get(job_id)
        if sister is None:
            return
        try:
            await self._infer_plan(job_id, sister)
        except Exception:
            log.debug("early_plan_inference_failed", job_id=job_id, exc_info=True)

    def feed_tool_name(self, job_id: str, tool_name: str) -> None:
        buf = self._recent_tool_names.get(job_id)
        if buf is not None:
            if tool_name not in buf:
                buf.append(tool_name)
            if len(buf) > 10:
                self._recent_tool_names[job_id] = buf[-10:]

    # -- Native plan (manage_todo_list) --------------------------------------

    async def feed_native_plan(self, job_id: str, items: list[dict[str, str]]) -> None:
        """Create/update plan steps from the agent's native todo tool."""
        status_map = {
            "not-started": "pending",
            "in-progress": "active",
            "in_progress": "active",
            "completed": "done",
            "done": "done",
            "pending": "pending",
            "active": "active",
            "skipped": "skipped",
        }

        new_labels: list[tuple[str, str]] = []
        for item in items:
            label = str(
                item.get("title") or item.get("content") or item.get("label") or ""
            ).strip()
            if not label:
                continue
            raw_status = str(item.get("status", "pending")).strip().lower()
            status = status_map.get(raw_status, "pending")
            new_labels.append((label, status))

        if not new_labels:
            return

        self._native_plan_active.add(job_id)

        existing = self._plan_steps.get(job_id, [])
        existing_by_label = {s.label: s for s in existing}

        updated: list[PlanStep] = []
        now = datetime.now(UTC)

        for label, status in new_labels:
            ps = existing_by_label.get(label)
            if ps:
                if ps.status != status:
                    ps.status = status
                    if status == "active" and ps.started_at is None:
                        ps.started_at = now
                    elif status == "done" and ps.completed_at is None:
                        ps.completed_at = now
                updated.append(ps)
            else:
                ps = PlanStep(
                    plan_step_id=_make_plan_step_id(),
                    label=label,
                    status=status,
                    started_at=now if status == "active" else None,
                    completed_at=now if status == "done" else None,
                )
                updated.append(ps)

        self._plan_steps[job_id] = updated
        self._plan_established[job_id] = True

        self._active_idx[job_id] = next(
            (i for i, s in enumerate(updated) if s.status == "active"), -1
        )

        for ps in updated:
            await self._emit_plan_step(job_id, ps)

        # Update card headline from active step
        active_ps = next((s for s in updated if s.status == "active"), None)
        if active_ps:
            await self._emit_card_headline(job_id, active_ps)

    # -- Plan inference (no native plan) -------------------------------------

    async def _infer_plan(self, job_id: str, sister: SisterSession) -> None:
        task = self._job_prompts.get(job_id, "")
        msgs = self._recent_messages.get(job_id, [])
        first_msg = msgs[0] if msgs else ""

        if not task and not first_msg:
            return

        prompt = _INFER_PLAN_PROMPT.format(
            task=task[:500],
            first_msg=first_msg[:500],
        )

        try:
            raw = await sister.complete(prompt, timeout=15)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
                raw = raw.strip()

            parsed = json.loads(raw)
            labels = parsed.get("items", [])
            if not isinstance(labels, list) or not labels:
                return

            now = datetime.now(UTC)
            steps: list[PlanStep] = []
            for i, label in enumerate(labels[:8]):
                if not isinstance(label, str) or not label.strip():
                    continue
                steps.append(PlanStep(
                    plan_step_id=_make_plan_step_id(),
                    label=label.strip()[:60],
                    status="active" if i == 0 else "pending",
                    started_at=now if i == 0 else None,
                ))

            if steps:
                self._plan_steps[job_id] = steps
                self._active_idx[job_id] = 0
                self._plan_established[job_id] = True

                for ps in steps:
                    await self._emit_plan_step(job_id, ps)

        except Exception:
            log.debug("plan_inference_failed", job_id=job_id, exc_info=True)

    # -- Turn classification + summary generation ----------------------------

    async def on_turn_completed(
        self,
        job_id: str,
        turn_payload: dict[str, Any],
    ) -> None:
        """Called when an SDK turn (step_completed) fires."""
        sister = self._sister_sessions.get(job_id)
        if sister is None:
            return

        # If plan not established, infer one
        if not self._plan_established.get(job_id, False):
            await self._infer_plan(job_id, sister)

        steps = self._plan_steps.get(job_id, [])
        if not steps:
            now = datetime.now(UTC)
            catch_all = PlanStep(
                plan_step_id=_make_plan_step_id(),
                label="Working on task",
                status="active",
                started_at=now,
            )
            self._plan_steps[job_id] = [catch_all]
            self._active_idx[job_id] = 0
            self._plan_established[job_id] = True
            steps = [catch_all]
            await self._emit_plan_step(job_id, catch_all)

        tool_count = turn_payload.get("tool_count", 0)
        agent_msg = turn_payload.get("agent_message", "") or ""
        files_written = turn_payload.get("files_written", []) or []
        duration_ms = turn_payload.get("duration_ms", 0) or 0
        start_sha = turn_payload.get("start_sha")
        end_sha = turn_payload.get("end_sha")

        # Native plan: accumulate metrics on active step + generate summary
        if job_id in self._native_plan_active:
            active_idx = self._active_idx.get(job_id, 0)
            if 0 <= active_idx < len(steps):
                ps = steps[active_idx]
                ps.tool_count += tool_count
                ps.duration_ms += duration_ms
                for f in files_written:
                    if f not in ps.files_written:
                        ps.files_written.append(f)
                if start_sha and ps.start_sha is None:
                    ps.start_sha = start_sha
                if end_sha:
                    ps.end_sha = end_sha
                await self._generate_summary(job_id, sister, ps, agent_msg)
                await self._emit_plan_step(job_id, ps)
                await self._emit_card_headline(job_id, ps)
            return

        # Non-native: classify turn to a plan item
        await self._classify_and_update(
            job_id, sister, steps,
            agent_msg=agent_msg,
            tool_count=tool_count,
            files_written=files_written,
            duration_ms=duration_ms,
            start_sha=start_sha,
            end_sha=end_sha,
        )

    async def _classify_and_update(
        self,
        job_id: str,
        sister: SisterSession,
        steps: list[PlanStep],
        *,
        agent_msg: str,
        tool_count: int,
        files_written: list[str],
        duration_ms: int,
        start_sha: str | None,
        end_sha: str | None,
    ) -> None:
        plan_block = "\n".join(
            f"  {i + 1}. [{s.status}] {s.label}" + (f" -- {s.summary}" if s.summary else "")
            for i, s in enumerate(steps)
        )
        tools = ", ".join(self._recent_tool_names.get(job_id, [])[-6:])
        intents = "; ".join(self._recent_tool_intents.get(job_id, [])[-3:])

        prompt = _CLASSIFY_PROMPT.format(
            plan_block=plan_block,
            agent_msg=agent_msg[:300] if agent_msg else "(no message)",
            tools=tools or "(none)",
            intents=intents or "(none)",
        )

        try:
            raw = await sister.complete(prompt, timeout=15)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
                raw = raw.strip()

            parsed = json.loads(raw)
            assign_idx = int(parsed.get("assign_to", 1)) - 1
            summary = str(parsed.get("summary", ""))[:200]
            new_status = str(parsed.get("status", "active"))
            if new_status not in ("active", "done"):
                new_status = "active"

        except Exception:
            log.debug("turn_classification_failed", job_id=job_id, exc_info=True)
            assign_idx = self._active_idx.get(job_id, 0)
            summary = ""
            new_status = "active"

        assign_idx = max(0, min(assign_idx, len(steps) - 1))
        ps = steps[assign_idx]
        now = datetime.now(UTC)

        ps.tool_count += tool_count
        ps.duration_ms += duration_ms
        for f in files_written:
            if f not in ps.files_written:
                ps.files_written.append(f)
        if start_sha and ps.start_sha is None:
            ps.start_sha = start_sha
        if end_sha:
            ps.end_sha = end_sha
        if summary:
            ps.summary = summary

        if ps.status == "pending":
            ps.status = "active"
            ps.started_at = now
        if new_status == "done" and ps.status == "active":
            ps.status = "done"
            ps.completed_at = now
            next_idx = next(
                (i for i in range(assign_idx + 1, len(steps)) if steps[i].status == "pending"),
                -1,
            )
            if next_idx >= 0:
                steps[next_idx].status = "active"
                steps[next_idx].started_at = now
                self._active_idx[job_id] = next_idx
                await self._emit_plan_step(job_id, steps[next_idx])

        self._active_idx[job_id] = max(
            self._active_idx.get(job_id, 0), assign_idx
        )

        await self._emit_plan_step(job_id, ps)
        await self._emit_card_headline(job_id, ps)

    async def _generate_summary(
        self,
        job_id: str,
        sister: SisterSession,
        ps: PlanStep,
        agent_msg: str,
    ) -> None:
        intents = "; ".join(self._recent_tool_intents.get(job_id, [])[-3:])
        tools = ", ".join(self._recent_tool_names.get(job_id, [])[-6:])

        prompt = (
            f"Summarize this coding step in 1-2 sentences. Be specific.\n\n"
            f"Plan item: {ps.label}\n"
            f"Agent message: {agent_msg[:300]}\n"
            f"Tools: {tools}\n"
            f"Tool intents: {intents}\n\n"
            f"Summary:"
        )

        try:
            raw = await sister.complete(prompt, timeout=10)
            summary = raw.strip().strip('"')[:200]
            if summary:
                ps.summary = summary
        except Exception:
            log.debug("summary_generation_failed", job_id=job_id, exc_info=True)

    # -- Active plan step (for transcript tagging) ---------------------------

    def get_active_plan_step_id(self, job_id: str) -> str | None:
        steps = self._plan_steps.get(job_id, [])
        idx = self._active_idx.get(job_id, -1)
        if 0 <= idx < len(steps):
            return steps[idx].plan_step_id
        for s in reversed(steps):
            if s.status != "pending":
                return s.plan_step_id
        return steps[0].plan_step_id if steps else None

    # -- Finalization --------------------------------------------------------

    async def finalize(self, job_id: str, succeeded: bool) -> None:
        steps = self._plan_steps.get(job_id, [])
        if not steps:
            return

        now = datetime.now(UTC)
        for ps in steps:
            if ps.status == "active":
                ps.status = "done" if succeeded else "skipped"
                if ps.status == "done":
                    ps.completed_at = now
                await self._emit_plan_step(job_id, ps)
            # pending (never started) steps: silently drop — not emitted

    def get_plan_steps(self, job_id: str) -> list[dict[str, str]]:
        return [
            {"label": s.label, "status": s.status}
            for s in self._plan_steps.get(job_id, [])
        ]

    # -- Event emission helpers ----------------------------------------------

    async def _emit_plan_step(self, job_id: str, ps: PlanStep) -> None:
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.plan_step_updated,
                payload=ps.to_event_payload(),
            )
        )

    async def _emit_card_headline(self, job_id: str, ps: PlanStep) -> None:
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.progress_headline,
                payload={
                    "headline": ps.label,
                    "headline_past": ps.label,
                    "summary": ps.summary or "",
                },
            )
        )

    # Back-compat stubs for RuntimeService
    def set_terminal_state(self, job_id: str, outcome: str) -> None:
        pass

    async def finalize_plan_steps(self, job_id: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Event bus subscriber
# ---------------------------------------------------------------------------


class _ProgressSubscriber:
    """EventBus subscriber that dispatches events to ProgressTrackingService."""

    def __init__(self, service: ProgressTrackingService) -> None:
        self._svc = service

    async def __call__(self, event: DomainEvent) -> None:
        if event.kind == DomainEventKind.step_completed:
            if event.payload.get("status") == "canceled":
                return
            await self._svc.on_turn_completed(event.job_id, event.payload)
