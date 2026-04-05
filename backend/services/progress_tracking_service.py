"""Progress tracking — headline milestones and plan extraction for running jobs.

Event-driven: headlines and plan updates fire on step boundaries (step_completed)
rather than on timers.  Each job's sister session provides the LLM calls.

Extracted from ``RuntimeService`` to isolate the LLM-driven progress tracking
concern from the core job execution lifecycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import JobState
from backend.models.events import DomainEvent, DomainEventKind

if TYPE_CHECKING:
    from backend.services.event_bus import EventBus
    from backend.services.sister_session import SisterSession, SisterSessionManager

log = structlog.get_logger()


@dataclass
class HeadlineResult:
    """Result of a headline generation attempt."""
    emitted: bool = False
    headline: str = ""
    headline_past: str = ""
    replace_last: int = 0

# ---------------------------------------------------------------------------
# Truncation limits
# ---------------------------------------------------------------------------
_HEADLINE_MSG_MAX = 200
_PLAN_MSG_MAX = 400
_TOOL_INTENT_MAX = 80

# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------
_MILESTONE_PROMPT_PREFIX = (
    "You are maintaining a milestone timeline for a coding agent. "
    "Milestones mark distinct PHASES of work — not incremental progress. "
    "Good milestones: 'Setting up project', 'Implementing auth API', 'Writing tests'. "
    "Bad milestones: 'Reading file X', 'Editing line 42', 'Running search'. "
    "The timeline should read like a high-level summary of what the agent accomplished, "
    "not a log of individual actions.\n\n"
)

_MILESTONE_PROMPT_SUFFIX = (
    "\n\nRespond with JSON only — exactly one of:\n\n"
    '1. No meaningful phase change: {"defer": true}\n'
    '2. New milestone: {"present": "Implementing auth API", "past": "Implemented auth API", '
    '"summary": "Adding JWT token validation to /login and /refresh endpoints. '
    'Wiring up middleware to reject expired tokens."}\n'
    "3. Recent milestones were actually the same phase — consolidate the last N "
    'into one: {"replace_last": 2, "present": "Implementing auth system", '
    '"past": "Implemented auth system", '
    '"summary": "Built login/refresh endpoints with JWT validation and expiry middleware."}\n\n'
    "RULES:\n"
    "- STRONGLY prefer defer. Only emit when the agent has clearly moved to a "
    "different area of the codebase or a different kind of task.\n"
    "- If the new milestone is mostly the same subject as the latest one, either defer or use replace_last.\n"
    "- Avoid emitting adjacent milestones that only change the verb, tense, or wording.\n"
    "- Use replace_last to merge entries that say essentially the same thing "
    "(e.g. 'Updating auth routes' and 'Fixing auth middleware' → 'Implementing auth system').\n"
    "- Labels: 3-6 words, no articles, no period, present tense for 'present', past tense for 'past'.\n"
    "- 'summary': 1-3 SHORT sentences describing specifically what was/is being done. "
    "Be concrete — mention actual files, endpoints, functions, or components. "
    "BAD: 'Exploring authentication documentation'. "
    "GOOD: 'Adding JWT middleware to protect /api routes. Storing refresh tokens in Redis.'"
)

_PLAN_PROMPT_PREFIX = (
    "You are extracting a high-level execution plan from a coding agent's activity. "
    "The plan should show 3-7 steps the agent is working through, with status markers.\n\n"
)

_PLAN_PROMPT_SUFFIX = (
    "\n\nRespond with JSON only:\n"
    '{"steps": [{"label": "Step description", "status": "done|active|pending"}]}\n\n'
    "RULES:\n"
    "- 3-7 steps total. Each label: 3-8 words, no articles, no period.\n"
    "- Mark completed work as 'done', current work as 'active' (exactly one), "
    "future work as 'pending'.\n"
    "- Steps should cover the full task arc — from what's been done to what remains.\n"
    "- Be concrete: mention actual components, endpoints, files when possible.\n"
    "- PHASE TRANSITIONS: If all previous steps are 'done' but the agent is still "
    "actively working (running tests, validating, fixing follow-ups, etc.), generate "
    "a FRESH plan with new steps for the current phase. Do NOT repeat already-done "
    "steps. The new plan should only contain steps relevant to what the agent is "
    "doing now and what remains.\n"
    "- COMPLETION: If all previous steps are 'done' AND the recent activity shows the "
    "agent has finished (no new tasks, no active work, winding down), "
    'respond with {"steps": []} to clear the plan.\n'
    '- If you can\'t determine a plan from the activity, respond: {"steps": []}\n'
)

# ---------------------------------------------------------------------------
# Headline similarity helpers (public for tests)
# ---------------------------------------------------------------------------
_HEADLINE_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "to",
        "for",
        "of",
        "in",
        "on",
        "with",
        "agent",
        "phase",
        "task",
        "tasks",
        "work",
        "working",
        "progress",
        "checking",
        "check",
        "investigating",
        "investigate",
        "debugging",
        "debug",
        "analyzing",
        "analyze",
        "exploring",
        "explore",
        "reviewing",
        "review",
        "fixing",
        "fix",
        "implementing",
        "implement",
        "updating",
        "update",
        "writing",
        "write",
        "running",
        "run",
        "editing",
        "edit",
        "refining",
        "refine",
    }
)


def _normalize_headline_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {word for word in words if word not in _HEADLINE_STOP_WORDS}


def _headlines_are_similar(left: str, right: str) -> bool:
    left_norm = " ".join(re.findall(r"[a-z0-9]+", left.lower()))
    right_norm = " ".join(re.findall(r"[a-z0-9]+", right.lower()))
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm or left_norm in right_norm or right_norm in left_norm:
        return True

    left_tokens = _normalize_headline_tokens(left)
    right_tokens = _normalize_headline_tokens(right)
    if not left_tokens or not right_tokens:
        return False

    shared = left_tokens & right_tokens
    if len(shared) < 2:
        return False

    overlap = len(shared) / min(len(left_tokens), len(right_tokens))
    return overlap >= 0.67


def _count_similar_trailing_headlines(history: list[str], headline: str) -> int:
    count = 0
    for existing in reversed(history):
        if _headlines_are_similar(existing, headline):
            count += 1
            continue
        break
    return count


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ProgressTrackingService:
    """Manages headline milestone generation and plan extraction for active jobs.

    Event-driven: headlines and plan updates fire on step boundaries rather
    than on timers.  The sister session for each job is looked up via
    ``SisterSessionManager``.
    """

    def __init__(
        self,
        sister_sessions: SisterSessionManager,
        event_bus: EventBus,
    ) -> None:
        self._sister_sessions = sister_sessions
        self._event_bus = event_bus

        # Per-job headline state
        self._headline_transcript: dict[str, list[str]] = {}
        self._headline_tool_intents: dict[str, list[str]] = {}
        self._headline_last_text: dict[str, str] = {}
        self._headline_history: dict[str, list[str]] = {}

        # Per-job plan state
        self._plan_transcript: dict[str, list[str]] = {}
        self._plan_last_steps: dict[str, list[dict[str, str]]] = {}
        self._plan_terminal_state: dict[str, str] = {}
        # Jobs receiving native plan data (manage_todo_list / TodoWrite)
        self._native_plan_active: set[str] = set()

    # -- Lifecycle -----------------------------------------------------------

    def start_tracking(self, job_id: str) -> None:
        """Initialise per-job state (no background tasks)."""
        self._headline_transcript[job_id] = []
        self._headline_tool_intents[job_id] = []
        self._headline_last_text[job_id] = ""
        self._headline_history[job_id] = []
        self._plan_transcript[job_id] = []
        self._plan_last_steps[job_id] = []

    def stop_tracking(self, job_id: str) -> None:
        """No-op — no background tasks to cancel."""

    def cleanup(self, job_id: str) -> None:
        """Remove all per-job in-memory state."""
        self._headline_transcript.pop(job_id, None)
        self._headline_tool_intents.pop(job_id, None)
        self._headline_last_text.pop(job_id, None)
        self._headline_history.pop(job_id, None)
        self._plan_transcript.pop(job_id, None)
        self._plan_last_steps.pop(job_id, None)
        self._native_plan_active.discard(job_id)

    # -- Data ingestion ------------------------------------------------------

    def feed_transcript(
        self,
        job_id: str,
        role: str,
        content: str,
        tool_intent: str = "",
    ) -> None:
        """Feed transcript data from the event stream into tracking buffers."""
        if role == "agent" and content:
            buf = self._headline_transcript.get(job_id)
            if buf is not None:
                buf.append(content[:_HEADLINE_MSG_MAX])
                if len(buf) > 3:
                    self._headline_transcript[job_id] = buf[-3:]

            pbuf = self._plan_transcript.get(job_id)
            if pbuf is not None:
                pbuf.append(content[:_PLAN_MSG_MAX])
                if len(pbuf) > 8:
                    self._plan_transcript[job_id] = pbuf[-8:]

        if role == "tool_call" and tool_intent:
            ibuf = self._headline_tool_intents.get(job_id)
            if ibuf is not None:
                ibuf.append(tool_intent[:_TOOL_INTENT_MAX])
                if len(ibuf) > 10:
                    self._headline_tool_intents[job_id] = ibuf[-10:]

    async def feed_native_plan(self, job_id: str, items: list[dict[str, str]]) -> None:
        """Accept plan steps from the agent's native todo tool.

        Maps agent todo statuses to plan step statuses and publishes
        ``agent_plan_updated`` immediately, bypassing the LLM extraction loop.
        """
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
        steps: list[dict[str, str]] = []
        for item in items:
            label = str(item.get("title") or item.get("content") or item.get("label") or "").strip()
            if not label:
                continue
            raw_status = str(item.get("status", "pending")).strip().lower()
            status = status_map.get(raw_status, "pending")
            steps.append({"label": label, "status": status})

        if not steps:
            return

        self._native_plan_active.add(job_id)

        if steps != self._plan_last_steps.get(job_id, []):
            self._plan_last_steps[job_id] = steps
            await self._event_bus.publish(
                DomainEvent(
                    event_id=DomainEvent.make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.agent_plan_updated,
                    payload={"steps": steps},
                )
            )

    def set_terminal_state(self, job_id: str, outcome: str) -> None:
        """Record terminal outcome (``succeeded`` / ``failed`` / ``canceled``)."""
        self._plan_terminal_state[job_id] = outcome

    def get_plan_steps(self, job_id: str) -> list[dict[str, str]]:
        """Return the last known plan steps for a job (may be empty)."""
        return list(self._plan_last_steps.get(job_id, []))

    # -- Plan finalization ---------------------------------------------------

    async def finalize_plan_steps(self, job_id: str) -> None:
        """Emit a final plan update so the frontend resolves any spinning steps."""
        terminal_outcome = self._plan_terminal_state.pop(job_id, None)
        last_steps = self._plan_last_steps.get(job_id)
        if not terminal_outcome or not last_steps:
            return

        succeeded = terminal_outcome in (JobState.review, JobState.completed)
        final_steps = []
        for s in last_steps:
            status = s.get("status", "pending")
            if status in ("active", "pending"):
                status = "done" if succeeded else "skipped"
            final_steps.append({"label": s["label"], "status": status})

        if final_steps == last_steps:
            return

        try:
            await self._event_bus.publish(
                DomainEvent(
                    event_id=DomainEvent.make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.agent_plan_updated,
                    payload={"steps": final_steps},
                )
            )
        except Exception:
            log.debug("plan_finalize_emit_failed", job_id=job_id, exc_info=True)

    # -- Event-driven headline generation -------------------------------------

    async def generate_headline_on_step(
        self,
        job_id: str,
        sister: SisterSession,
    ) -> HeadlineResult:
        """Generate a headline milestone on a step boundary.

        Called by the step-completed event handler.  Uses the buffered
        transcript data and the job's sister session for the LLM call.

        Returns a ``HeadlineResult`` with details about what happened.
        """
        import json as _json

        buf = self._headline_transcript.get(job_id)
        intents_buf = self._headline_tool_intents.get(job_id)

        recent_msgs: list[str] = list(buf) if buf else []
        recent_intents: list[str] = list(intents_buf) if intents_buf else []

        if buf:
            buf.clear()
        if intents_buf:
            intents_buf.clear()

        if not recent_msgs and not recent_intents:
            return HeadlineResult()

        parts = []
        for msg in recent_msgs:
            parts.append(msg[:_HEADLINE_MSG_MAX])
        if recent_intents:
            parts.append("Tool intents: " + ", ".join(recent_intents))

        history = self._headline_history.get(job_id, [])
        history_block = ""
        if history:
            numbered = "\n".join(f"  {i + 1}. {h}" for i, h in enumerate(history))
            history_block = f"Milestone history so far:\n{numbered}\n\n"

        prompt = (
            _MILESTONE_PROMPT_PREFIX
            + history_block
            + "Recent agent activity:\n"
            + "\n---\n".join(parts)
            + _MILESTONE_PROMPT_SUFFIX
        )

        try:
            raw = await sister.complete(prompt, timeout=30)
            raw = raw.strip()

            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
                raw = raw.strip()

            try:
                parsed = _json.loads(raw)
            except (ValueError, AttributeError):
                parsed = {}

            if parsed.get("defer"):
                log.debug("headline_deferred", job_id=job_id)
                return HeadlineResult()
            else:
                headline = str(parsed.get("present", "")).strip().strip('"').strip(".")
                headline_past = str(parsed.get("past", "")).strip().strip('"').strip(".")
                summary = str(parsed.get("summary", "")).strip().strip('"')
                replace_last = int(parsed.get("replace_last", 0))

                last_headline = self._headline_last_text.get(job_id, "")
                if headline and len(headline) > 3 and headline != last_headline:
                    replace_last = max(replace_last, _count_similar_trailing_headlines(history, headline))
                    replace_last = max(0, min(replace_last, len(history)))

                    if replace_last > 0:
                        self._headline_history[job_id] = history[:-replace_last] + [headline]
                    else:
                        self._headline_history.setdefault(job_id, []).append(headline)
                    self._headline_last_text[job_id] = headline

                    await self._event_bus.publish(
                        DomainEvent(
                            event_id=DomainEvent.make_event_id(),
                            job_id=job_id,
                            timestamp=datetime.now(UTC),
                            kind=DomainEventKind.progress_headline,
                            payload={
                                "headline": headline,
                                "headline_past": headline_past,
                                "replaces_count": replace_last,
                                "summary": summary,
                            },
                        )
                    )
                    return HeadlineResult(
                        emitted=True,
                        headline=headline,
                        headline_past=headline_past,
                        replace_last=replace_last,
                    )
            return HeadlineResult()
        except Exception:
            log.debug("headline_generation_failed", job_id=job_id, exc_info=True)
            return HeadlineResult()

    # -- Event-driven plan extraction ----------------------------------------

    async def generate_plan_on_step(
        self,
        job_id: str,
        sister: SisterSession,
    ) -> None:
        """Extract/update the plan on a step boundary.

        Skipped when the agent provides native plan data via manage_todo_list.
        Called by the step-completed event handler.
        """
        import json as _json

        if job_id in self._native_plan_active:
            return

        buf = self._plan_transcript.get(job_id)
        if not buf:
            return

        recent = list(buf)

        milestones = self._headline_history.get(job_id, [])
        milestone_block = ""
        if milestones:
            milestone_block = "Completed milestones:\n" + "\n".join(f"  - {m}" for m in milestones) + "\n\n"

        prev_steps = self._plan_last_steps.get(job_id, [])
        prev_block = ""
        if prev_steps:
            lines = []
            for s in prev_steps:
                lines.append(f"  - [{s.get('status', 'pending')}] {s.get('label', '')}")
            prev_block = "Previous plan:\n" + "\n".join(lines) + "\n\n"

        prompt = (
            _PLAN_PROMPT_PREFIX
            + milestone_block
            + prev_block
            + "Recent agent messages:\n"
            + "\n---\n".join(recent)
            + _PLAN_PROMPT_SUFFIX
        )

        try:
            raw = await sister.complete(prompt, timeout=30)
            raw = raw.strip()

            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
                raw = raw.strip()

            parsed = _json.loads(raw)
            steps = parsed.get("steps", [])

            if not isinstance(steps, list):
                steps = []

            if not steps:
                if self._plan_last_steps.get(job_id):
                    self._plan_last_steps[job_id] = []
                    await self._event_bus.publish(
                        DomainEvent(
                            event_id=DomainEvent.make_event_id(),
                            job_id=job_id,
                            timestamp=datetime.now(UTC),
                            kind=DomainEventKind.agent_plan_updated,
                            payload={"steps": []},
                        )
                    )
            else:
                clean_steps = []
                for s in steps:
                    if isinstance(s, dict) and s.get("label"):
                        status = s.get("status", "pending")
                        if status not in ("done", "active", "pending", "skipped"):
                            status = "pending"
                        clean_steps.append({"label": s["label"], "status": status})

                if clean_steps and clean_steps != self._plan_last_steps.get(job_id, []):
                    self._plan_last_steps[job_id] = clean_steps
                    await self._event_bus.publish(
                        DomainEvent(
                            event_id=DomainEvent.make_event_id(),
                            job_id=job_id,
                            timestamp=datetime.now(UTC),
                            kind=DomainEventKind.agent_plan_updated,
                            payload={"steps": clean_steps},
                        )
                    )
        except Exception:
            log.debug("plan_extraction_failed", job_id=job_id, exc_info=True)


# ---------------------------------------------------------------------------
# Event bus subscriber — triggers headline + plan generation on step boundaries
# ---------------------------------------------------------------------------


class _ProgressSubscriber:
    """EventBus subscriber that dispatches step_completed events to ProgressTrackingService.

    Also tracks step groups: steps that belong to the same headline phase
    are clustered together and emitted as ``step_group_updated`` events.

    Groups update **retroactively**:
    - On every step_completed, the *open* group is re-emitted with the latest
      step IDs so the frontend sees it grow in real time.
    - When a new headline fires, the open group is *closed* and a new one starts.
    - When ``replace_last > 0`` (headline consolidation), the last N closed groups
      are merged into the current one — the headline retroactively covers a wider
      span of steps.
    """

    def __init__(self, service: ProgressTrackingService) -> None:
        self._svc = service
        # Per-job group state
        self._current_group_steps: dict[str, list[str]] = {}
        self._group_counter: dict[str, int] = {}
        # Closed groups kept for merge-on-replace_last
        self._closed_groups: dict[str, list[dict]] = {}  # job_id → [{groupId, headline, stepIds}]
        # Current open group headline (from last emitted headline, or empty)
        self._current_headline: dict[str, str] = {}
        self._current_headline_past: dict[str, str] = {}

    def _next_group_id(self, job_id: str) -> str:
        counter = self._group_counter.get(job_id, 0) + 1
        self._group_counter[job_id] = counter
        return f"g-{counter}"

    async def _emit_group(self, job_id: str, group_id: str, headline: str,
                          headline_past: str, step_ids: list[str]) -> None:
        await self._svc._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.step_group_updated,
                payload={
                    "group_id": group_id,
                    "headline": headline,
                    "headline_past": headline_past,
                    "step_ids": list(step_ids),
                },
            )
        )

    async def __call__(self, event: DomainEvent) -> None:
        # Accumulate step IDs as they start
        if event.kind == DomainEventKind.step_started:
            step_id = event.payload.get("step_id", "")
            if step_id:
                self._current_group_steps.setdefault(event.job_id, []).append(step_id)
            return

        if event.kind != DomainEventKind.step_completed:
            return
        if event.payload.get("status") == "canceled":
            return

        job_id = event.job_id
        sister = self._svc._sister_sessions.get(job_id)
        if sister is None:
            return

        result = await self._svc.generate_headline_on_step(job_id, sister)
        await self._svc.generate_plan_on_step(job_id, sister)

        step_ids = self._current_group_steps.get(job_id, [])
        if not step_ids:
            return

        if result.emitted:
            # --- Phase boundary: close the current open group ---

            if result.replace_last > 0:
                # Merge: absorb step IDs from the last N closed groups
                closed = self._closed_groups.get(job_id, [])
                merge_count = min(result.replace_last, len(closed))
                merged_step_ids: list[str] = []
                merged_group_id: str | None = None
                for grp in closed[-merge_count:]:
                    if merged_group_id is None:
                        merged_group_id = grp["group_id"]  # reuse earliest group's ID
                    merged_step_ids.extend(grp["step_ids"])
                # Remove the merged groups from closed list
                if merge_count > 0:
                    self._closed_groups[job_id] = closed[:-merge_count]

                # Combine: merged old groups + current open group
                all_step_ids = merged_step_ids + step_ids
                gid = merged_group_id or self._next_group_id(job_id)
                await self._emit_group(job_id, gid, result.headline, result.headline_past, all_step_ids)

                # Record as closed
                self._closed_groups.setdefault(job_id, []).append({
                    "group_id": gid,
                    "headline": result.headline,
                    "step_ids": all_step_ids,
                })
            else:
                # New phase — close current group as-is
                gid = self._next_group_id(job_id)
                await self._emit_group(job_id, gid, result.headline, result.headline_past, step_ids)

                self._closed_groups.setdefault(job_id, []).append({
                    "group_id": gid,
                    "headline": result.headline,
                    "step_ids": list(step_ids),
                })

            # Update headline and reset accumulator
            self._current_headline[job_id] = result.headline
            self._current_headline_past[job_id] = result.headline_past
            self._current_group_steps[job_id] = []

        else:
            # --- No phase boundary: re-emit the open group so frontend sees it grow ---
            headline = self._current_headline.get(job_id, "")
            headline_past = self._current_headline_past.get(job_id, "")
            if headline:
                # Emit open-group update when there's a known headline so the
                # frontend sees every step assigned to its group immediately —
                # including the first step after a phase boundary.
                next_counter = self._group_counter.get(job_id, 0) + 1
                await self._emit_group(job_id, f"g-{next_counter}", headline, headline_past, step_ids)
