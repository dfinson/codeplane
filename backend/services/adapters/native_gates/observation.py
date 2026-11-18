"""Native observations enter canonical local history before optional safe export."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from backend.models.usage import UsageAmount
from backend.services.events.export_metadata import export_metadata

from ._optional import load_sdk
from .observation_scope import NativeObservationOwner, ObservationSubscription
from .readiness import UnsupportedNativeHookError, require_ready

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.models.session_event import SessionEvent


def _exporter(
    owner: NativeObservationOwner,
    sink: Callable[[dict[str, object]], None] | None,
    identity_key: bytes | None,
    approved_models: frozenset[str],
) -> Callable[[SessionEvent], None] | None:
    if sink is None:
        return None
    if identity_key is None or len(identity_key) < 32:
        raise ValueError("Native metadata export requires a protected 256-bit identity key")

    def export(event: SessionEvent) -> None:
        sink(
            export_metadata(
                event,
                identity_key=identity_key,
                integration=owner.family,
                provider="openai" if owner.family == "openai_agents" else owner.family,
                role="primary",
                approved_models=approved_models,
                usage=tuple(UsageAmount.model_validate(value) for value in event.payload.get("native_usage", ())),
            )
        )

    return export


async def subscribe_openai(
    owner: NativeObservationOwner,
    *,
    export_sink: Callable[[dict[str, object]], None] | None = None,
    identity_key: bytes | None = None,
    approved_models: frozenset[str] = frozenset(),
    provider: Any = None,
) -> ObservationSubscription:
    await owner.require_registration("openai_agents")
    export = _exporter(owner, export_sink, identity_key, approved_models)
    require_ready("openai_agents")
    tracing = load_sdk("agents.tracing")
    if TYPE_CHECKING:
        from ._optional import TracingProcessor
    else:
        TracingProcessor = tracing.TracingProcessor  # noqa: N806 - real SDK base.
    trace_provider = load_sdk("agents.tracing.provider")
    provider = provider if provider is not None else tracing.get_trace_provider()
    if not isinstance(provider, trace_provider.DefaultTraceProvider):
        raise UnsupportedNativeHookError("Exact unsubscription requires the qualified native DefaultTraceProvider")
    subscription = ObservationSubscription(owner, export)

    class Processor(TracingProcessor):
        def on_trace_start(self, trace: Any) -> None:
            subscription.capture(lambda: trace.export() if trace.trace_id == owner.source_id else None)

        def on_trace_end(self, trace: Any) -> None:
            if trace.trace_id == owner.source_id and owner.admits_callback():
                asyncio.run_coroutine_threadsafe(owner.close(), owner.loop)

        def on_span_start(self, span: Any) -> None:
            pass

        def on_span_end(self, span: Any) -> None:
            subscription.capture(lambda: span.export() if span.trace_id == owner.source_id else None)

        def shutdown(self) -> None:
            # The Runtime owner awaits the subscription drain before releasing.
            subscription.request_detach()

        def force_flush(self) -> None:
            pass

    processor = Processor()

    def remove() -> None:
        processors = provider._multi_processor
        with processors._lock:
            processors._processors = tuple(item for item in processors._processors if item is not processor)

    subscription._remove = remove
    try:
        provider.register_processor(processor)
    except BaseException:
        subscription.request_detach()
        raise
    return subscription


async def subscribe_crewai(
    owner: NativeObservationOwner,
    crew: Any,
    *,
    export_sink: Callable[[dict[str, object]], None] | None = None,
    identity_key: bytes | None = None,
    approved_models: frozenset[str] = frozenset(),
) -> ObservationSubscription:
    await owner.require_registration("crewai")
    export = _exporter(owner, export_sink, identity_key, approved_models)
    require_ready("crewai")
    crewai = load_sdk("crewai")
    event_bus = load_sdk("crewai.events").crewai_event_bus
    events = load_sdk("crewai.events.types.tool_usage_events")
    if not isinstance(crew, crewai.Crew) or str(crew.id) != owner.source_id:
        raise TypeError("CrewAI observation requires its exact caller-owned Crew/source")
    agents = tuple(crew.agents)
    tasks = tuple(crew.tasks)
    subscription = ObservationSubscription(owner, export)
    event_types = (events.ToolUsageStartedEvent, events.ToolUsageFinishedEvent, events.ToolUsageErrorEvent)

    def observe(source: Any, event: Any) -> None:
        if not any(source.agent is agent for agent in agents) or not any(source.task is task for task in tasks):
            return

        def record() -> dict[str, Any]:
            data = event.to_json(exclude={"agent", "from_agent", "from_task"})
            if not isinstance(data, dict):
                raise TypeError("Native CrewAI observation did not export a record object")
            data.update(
                {
                    "agent_id": str(source.agent.id),
                    "task_id": str(source.task.id),
                    "crew_id": str(crew.id),
                }
            )
            return data

        subscription.capture(record)

    def remove() -> None:
        with event_bus._rwlock.w_locked():
            for event_type in event_types:
                handlers = event_bus._sync_handlers.get(event_type, frozenset())
                event_bus._sync_handlers[event_type] = handlers - {observe}
                event_bus._handler_dependencies.get(event_type, {}).pop(observe, None)
                event_bus._execution_plan_cache.pop(event_type, None)

    subscription._remove = remove
    try:
        for event_type in event_types:
            event_bus.on(event_type)(observe)
    except BaseException:
        subscription.request_detach()
        raise
    return subscription
