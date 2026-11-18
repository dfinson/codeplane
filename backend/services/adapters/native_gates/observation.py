"""Additive native observation, explicitly not a permission or accounting gate.

The pinned SDKs expose registration but not targeted public unregistration.
Removal below is version-validated and uses the SDK's own registry locks.
Only metadata-only projections reach the supplied export sink.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from backend.models.events import new_event
from backend.models.session_event import EventMetadata
from backend.models.usage import UsageAmount
from backend.services.events.export_metadata import export_metadata

from ._optional import load_sdk
from .readiness import UnsupportedNativeHookError, require_ready

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.services.adapters.contracts import ExecutionIdentity


class ObservationSubscription:
    blocking = False

    def __init__(self) -> None:
        self._closed = threading.Event()
        self._remove: Callable[[], None] = lambda: None

    @property
    def active(self) -> bool:
        return not self._closed.is_set()

    def detach(self) -> None:
        self._closed.set()
        self._remove()


def subscribe_openai(
    execution: ExecutionIdentity,
    trace_id: str,
    *,
    identity_key: bytes,
    approved_models: frozenset[str],
    sink: Callable[[dict[str, object]], None],
    provider: Any = None,
) -> ObservationSubscription:
    require_ready("openai_agents")
    tracing = load_sdk("agents.tracing")
    if TYPE_CHECKING:
        from ._optional import TracingProcessor
    else:
        TracingProcessor = tracing.TracingProcessor  # noqa: N806 - runtime SDK base, not a local value.
    trace_provider = load_sdk("agents.tracing.provider")

    provider = provider if provider is not None else tracing.get_trace_provider()
    if not isinstance(provider, trace_provider.DefaultTraceProvider):
        raise UnsupportedNativeHookError("Exact unsubscription is qualified only for the native DefaultTraceProvider")
    subscription = ObservationSubscription()

    def emit(native: dict[str, Any] | None) -> None:
        if not subscription.active or native is None or native.get("trace_id", native.get("id")) != trace_id:
            return
        data = native.get("span_data", {})
        model = data.get("model")
        model = model if isinstance(model, str) and model in approved_models else None
        usage = []
        provided = data.get("usage")
        if isinstance(provided, dict):
            for unit in ("input_tokens", "output_tokens"):
                amount = provided.get(unit)
                if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0:
                    usage.append(
                        UsageAmount(
                            amount=Decimal(amount),
                            unit=unit,
                            producer="openai_agents",
                            model=model,
                            job_id=execution.job_id,
                            execution_epoch=execution.epoch,
                            role="primary",
                            source=f"Span.export.span_data.usage.{unit}",
                        )
                    )
        event = new_event(
            execution.job_id,
            "integration.event",
            {"model": model},
            event_id=native["id"],
            metadata=EventMetadata(
                source_framework="openai_agents",
                run_id=trace_id,
                span_id=native.get("id") if "span_data" in native else None,
                parent_id=native.get("parent_id"),
                execution_epoch=execution.epoch,
                execution_token=execution.execution_token,
            ),
        )
        sink(
            export_metadata(
                event,
                identity_key=identity_key,
                integration="openai_agents",
                provider="openai",
                role="primary",
                approved_models=approved_models,
                usage=usage,
            )
        )

    class Processor(TracingProcessor):
        def on_trace_start(self, trace: Any) -> None:
            pass

        def on_trace_end(self, trace: Any) -> None:
            emit(trace.export())

        def on_span_start(self, span: Any) -> None:
            pass

        def on_span_end(self, span: Any) -> None:
            emit(span.export())

        def shutdown(self) -> None:
            subscription.detach()

        def force_flush(self) -> None:
            pass

    processor = Processor()
    provider.register_processor(processor)

    def remove() -> None:
        processors = provider._multi_processor
        with processors._lock:
            processors._processors = tuple(item for item in processors._processors if item is not processor)

    subscription._remove = remove
    return subscription


def subscribe_crewai(
    execution: ExecutionIdentity,
    crew: Any,
    *,
    identity_key: bytes,
    sink: Callable[[dict[str, object]], None],
) -> ObservationSubscription:
    require_ready("crewai")
    crewai = load_sdk("crewai")
    crewai_event_bus = load_sdk("crewai.events").crewai_event_bus
    finished_event = load_sdk("crewai.events.types.tool_usage_events").ToolUsageFinishedEvent

    if not isinstance(crew, crewai.Crew):
        raise TypeError("CrewAI observation requires the original Crew")
    agents = tuple(crew.agents)
    tasks = frozenset(str(task.id) for task in crew.tasks)
    subscription = ObservationSubscription()

    def completed(source: Any, native: Any) -> None:
        if not subscription.active or not any(source.agent is agent for agent in agents) or native.task_id not in tasks:
            return
        event = new_event(
            execution.job_id,
            "integration.event",
            {},
            metadata=EventMetadata(
                source_framework="crewai",
                run_id=str(crew.id),
                execution_epoch=execution.epoch,
                execution_token=execution.execution_token,
                duration_ms=(native.finished_at - native.started_at).total_seconds() * 1000,
            ),
        )
        sink(
            export_metadata(
                event,
                identity_key=identity_key,
                integration="crewai",
                provider="crewai",
                role="primary",
                approved_models=frozenset(),
            )
        )

    crewai_event_bus.on(finished_event)(completed)

    def remove() -> None:
        with crewai_event_bus._rwlock.w_locked():
            handlers = crewai_event_bus._sync_handlers.get(finished_event, frozenset())
            crewai_event_bus._sync_handlers[finished_event] = handlers - {completed}
            crewai_event_bus._handler_dependencies.get(finished_event, {}).pop(completed, None)
            crewai_event_bus._execution_plan_cache.pop(finished_event, None)

    subscription._remove = remove
    return subscription
