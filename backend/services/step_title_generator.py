"""Event-driven step title generation using a cheap model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from backend.persistence.step_repo import StepRepository
    from backend.services.event_bus import EventBus
    from backend.services.sister_session import SisterSession, SisterSessionManager

from backend.models.events import DomainEvent, DomainEventKind

log = structlog.get_logger()


@dataclass
class _TitleContext:
    intent: str
    tool_names: list[str] = field(default_factory=list)
    tool_intents: list[str] = field(default_factory=list)


def _strip_to_title(text: str) -> str:
    """Fallback: extract a short title from raw intent text."""
    for prefix in ("Can you ", "Please ", "I need you to ", "Could you "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    for sep in (".", ",", " — ", " - ", "\n"):
        if sep in text:
            text = text[:text.index(sep)]
            break
    return text[:50].strip()


class StepTitleGenerator:
    """Generates short titles for steps using a cheap model.

    Triggered by StepCompleted events. Falls back to intent truncation
    if the model call fails or is skipped.
    """

    def __init__(
        self,
        sister_sessions: SisterSessionManager,
        event_bus: EventBus,
        step_repo: StepRepository,
    ) -> None:
        self._sister_sessions = sister_sessions
        self._event_bus = event_bus
        self._step_repo = step_repo
        # Context accumulated during step execution, keyed by "job_id:step_id"
        self._step_context: dict[str, _TitleContext] = {}

    def _key(self, job_id: str, step_id: str) -> str:
        return f"{job_id}:{step_id}"

    def on_step_started(self, job_id: str, payload: dict) -> None:
        """Buffer context for title generation."""
        step_id = payload["step_id"]
        self._step_context[self._key(job_id, step_id)] = _TitleContext(
            intent=payload.get("intent", ""),
        )

    def on_transcript_event(self, job_id: str, payload: dict) -> None:
        """Accumulate tool info for the active step."""
        step_id = payload.get("step_id")
        if not step_id:
            return
        ctx = self._step_context.get(self._key(job_id, step_id))
        if not ctx:
            return
        role = payload.get("role", "")
        if role in ("tool_call", "tool_running"):
            tool_name = payload.get("tool_name", "")
            if tool_name and tool_name not in ctx.tool_names:
                ctx.tool_names.append(tool_name)
            intent = payload.get("tool_intent", "")
            if intent:
                ctx.tool_intents.append(intent[:60])
                if len(ctx.tool_intents) > 5:
                    ctx.tool_intents = ctx.tool_intents[-5:]

    async def on_step_completed(self, event: DomainEvent) -> None:
        """Generate title for a completed step."""
        step_id = event.payload["step_id"]
        ctx = self._step_context.pop(self._key(event.job_id, step_id), None)
        if not ctx:
            return

        if event.payload.get("status") == "canceled":
            return

        sister = self._sister_sessions.get(event.job_id)

        # Skip LLM for trivial steps or already-short intents that are descriptive
        # (vague/short intents like "view" or "Continuing work" must go through LLM)
        is_vague = not ctx.intent or len(ctx.intent) < 15 or ctx.intent in ("Continuing work", "Starting work")
        if not is_vague and ctx.intent and len(ctx.intent) <= 50 and not ctx.intent.startswith(("Can you", "Please", "I need")):
            title = _strip_to_title(ctx.intent)
        elif sister is not None:
            title = await self._generate_title(ctx, sister, event.payload.get("agent_message"))
        else:
            title = _strip_to_title(ctx.intent) if ctx.intent else "Working"

        await self._step_repo.set_title(step_id, title)

        await self._event_bus.publish(DomainEvent(
            event_id=DomainEvent.make_event_id(),
            job_id=event.job_id,
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.step_title_generated,
            payload={"step_id": step_id, "title": title},
        ))

    async def _generate_title(self, ctx: _TitleContext, sister: SisterSession, agent_message: str | None = None) -> str:
        """Call cheap model to generate a concise title."""
        tools_text = ", ".join(ctx.tool_names[:6]) if ctx.tool_names else ""
        intents_text = "; ".join(ctx.tool_intents[:3]) if ctx.tool_intents else ""

        prompt = (
            "Generate a 3-6 word title for this coding step. "
            "Use present participle (e.g. 'Fixing auth validation'). "
            "No period. No articles.\n\n"
        )
        if ctx.intent and ctx.intent not in ("Continuing work", "Starting work"):
            prompt += f"Original request: {ctx.intent[:100]}\n"
        if agent_message:
            prompt += f"Agent said: {agent_message[:200]}\n"
        if tools_text:
            prompt += f"Tools used: {tools_text}\n"
        if intents_text:
            prompt += f"Tool intents: {intents_text}\n"
        prompt += "\nTitle:"

        try:
            raw = await sister.complete(prompt, timeout=10)
            title = raw.strip().strip('"').strip(".")[:50]
            return title if title else _strip_to_title(ctx.intent)
        except Exception:
            return _strip_to_title(ctx.intent)

    def cleanup(self, job_id: str) -> None:
        """Remove all buffered context for a job."""
        prefix = f"{job_id}:"
        to_remove = [k for k in self._step_context if k.startswith(prefix)]
        for k in to_remove:
            self._step_context.pop(k, None)


class _StepTitleSubscriber:
    """EventBus subscriber that dispatches events to StepTitleGenerator."""

    def __init__(self, generator: StepTitleGenerator) -> None:
        self._gen = generator

    async def __call__(self, event: DomainEvent) -> None:
        if event.kind == DomainEventKind.step_started:
            self._gen.on_step_started(event.job_id, event.payload)
        elif event.kind == DomainEventKind.transcript_updated:
            role = event.payload.get("role", "")
            if role not in ("agent_delta",) and event.payload.get("step_id"):
                self._gen.on_transcript_event(event.job_id, event.payload)
        elif event.kind == DomainEventKind.step_completed:
            await self._gen.on_step_completed(event)
