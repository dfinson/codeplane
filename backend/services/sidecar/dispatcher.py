"""Sidecar dispatcher — evaluates triggers and executes pipelines.

Subscribes to the EventBus and runs condition evaluation, context assembly,
LLM calls, output parsing, and result routing for all active sidecars.

This is the engine that connects sidecar *definitions* (templates) to sidecar
*sessions* (LLM completers).  It replaces the hardcoded stall-detection and
plan-inference logic previously scattered across RuntimeService.

Architecture notes:
- One dispatcher instance per process (APP scope).
- ``activate(job_id, definitions)`` is called by RuntimeService when a job starts.
- ``deactivate(job_id)`` is called when a job reaches a terminal state.
- The dispatcher subscribes to EventBus to evaluate event/regex/content/file
  conditions.  RuntimeService calls ``increment()`` for threshold counters.
- Timer conditions are evaluated by a periodic ``tick()`` coroutine.
- Context providers and output callbacks are registered at startup by the
  services that own the state.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from fnmatch import fnmatch
from string import Formatter
from typing import TYPE_CHECKING, Any, cast

import structlog

from backend.models.events import is_admission_receipt
from backend.services.events.observation import is_observation_history

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.domain import SidecarLifetime, SidecarPhase
    from backend.models.events import SessionEvent
    from backend.services.events.event_bus import EventBus
    from backend.services.sidecar.session import SidecarSessionManager

from backend.models.domain import SessionConfig, SidecarConfig
from backend.models.events import EventKind

log = structlog.get_logger()


class _SafeFormatter(Formatter):
    """String formatter that only allows simple {key} substitutions.

    Blocks attribute access ({key.attr}), index access ({key[0]}), and
    format specs that could leak object internals.
    """

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> tuple[Any, str]:
        # Only allow simple key names — no dots, brackets, or conversions
        if not field_name.isidentifier():
            raise KeyError(field_name)
        val, key = super().get_field(field_name, args, kwargs)
        return val, key


_safe_fmt = _SafeFormatter()

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

ContextProvider = Callable[[str], Awaitable[dict[str, Any] | None]]
OutputCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


# ---------------------------------------------------------------------------
# Concurrency policy
# ---------------------------------------------------------------------------


class Concurrency(StrEnum):
    skip_if_running = "skip_if_running"
    queue = "queue"
    parallel = "parallel"


# ---------------------------------------------------------------------------
# Condition dataclasses (frozen, parametrized, no handler functions)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventCondition:
    event_kinds: tuple[str, ...]
    event_filter: dict[str, str] = field(default_factory=dict)
    once: bool = False


@dataclass(frozen=True)
class TimerCondition:
    interval_s: float
    idle_guard_s: float | None = None


@dataclass(frozen=True)
class ThresholdCondition:
    metric: str
    value: int
    once: bool = False


@dataclass(frozen=True)
class ManualCondition:
    pass


@dataclass(frozen=True)
class RegexCondition:
    pattern: str
    source: str = "messages"
    once: bool = False
    _compiled: re.Pattern[str] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class FilePatternCondition:
    glob: str
    change_kind: str = "any"


@dataclass(frozen=True)
class ContentMatchCondition:
    keywords: tuple[str, ...]
    case_sensitive: bool = False
    source: str = "messages"
    once: bool = False


TriggerCondition = (
    EventCondition
    | TimerCondition
    | ThresholdCondition
    | ManualCondition
    | RegexCondition
    | FilePatternCondition
    | ContentMatchCondition
)


# ---------------------------------------------------------------------------
# Output parsers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlainText:
    strip: bool = True


@dataclass(frozen=True)
class JsonObject:
    required_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class JsonArray:
    item_keys: tuple[str, ...] = ()


OutputParser = PlainText | JsonObject | JsonArray


# ---------------------------------------------------------------------------
# Output routes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventBusRoute:
    event_kind: str
    payload_mapping: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JobMetadataRoute:
    field_name: str


@dataclass(frozen=True)
class CallbackRoute:
    callback_name: str


@dataclass(frozen=True)
class ConditionalRoute:
    field_name: str
    value: str
    inner: OutputRoute


@dataclass(frozen=True)
class AgentMessageRoute:
    role: str = "system"
    label: str = ""


@dataclass(frozen=True)
class GateRoute:
    verdict_field: str = "verdict"
    reason_field: str = "reason"
    timeout_s: float | None = None


OutputRoute = EventBusRoute | JobMetadataRoute | CallbackRoute | ConditionalRoute | AgentMessageRoute | GateRoute


# ---------------------------------------------------------------------------
# Tool access policy (for agentic sidecars)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SidecarToolPolicy:
    """Declares what tools a sidecar can use.

    ``allowed_categories`` is the primary control — coarse-grained tool
    groups rather than individual tool names.  The shell allowlist further
    restricts which commands are permitted when shell access is granted.
    """

    allowed_categories: frozenset[str]  # {"read", "search", "shell_readonly", "shell_write", "write", "mcp"}
    blocked_tools: frozenset[str] = frozenset()
    mcp_servers: frozenset[str] = frozenset()
    path_scope: str = "worktree"  # "worktree" (default) or "repo"
    shell_readonly: bool = True
    shell_allowlist: tuple[str, ...] = ()


# Canonical tool category names.
TOOL_CATEGORY_READ = "read"
TOOL_CATEGORY_SEARCH = "search"
TOOL_CATEGORY_SHELL_READONLY = "shell_readonly"
TOOL_CATEGORY_SHELL_WRITE = "shell_write"
TOOL_CATEGORY_WRITE = "write"
TOOL_CATEGORY_MCP = "mcp"

ALL_TOOL_CATEGORIES = frozenset(
    {
        TOOL_CATEGORY_READ,
        TOOL_CATEGORY_SEARCH,
        TOOL_CATEGORY_SHELL_READONLY,
        TOOL_CATEGORY_SHELL_WRITE,
        TOOL_CATEGORY_WRITE,
        TOOL_CATEGORY_MCP,
    }
)

# Named access tiers (convenience labels for common combinations).
TOOL_ACCESS_NONE = "none"
TOOL_ACCESS_READ_ONLY = "read_only"
TOOL_ACCESS_SHELL_RESTRICTED = "shell_restricted"
TOOL_ACCESS_AGENTIC = "agentic"

_TOOL_ACCESS_LEVELS = frozenset(
    {TOOL_ACCESS_NONE, TOOL_ACCESS_READ_ONLY, TOOL_ACCESS_SHELL_RESTRICTED, TOOL_ACCESS_AGENTIC}
)


# ---------------------------------------------------------------------------
# Trigger pipeline + sidecar definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerPipeline:
    condition: TriggerCondition
    context_sources: tuple[str, ...] = ()
    prompt_template: str = ""
    output_parser: OutputParser = field(default_factory=PlainText)
    output_routes: tuple[OutputRoute, ...] = ()
    concurrency: Concurrency = Concurrency.skip_if_running


@dataclass(frozen=True)
class SidecarDefinition:
    name: str
    phase: str  # "preflight" | "midflight" | "postflight"
    lifetime: str  # "ephemeral" | "windowed" | "persistent"
    scope: str = "global"  # "global" | "repo" | "job"
    model: str | None = None
    system_prompt: str = ""
    max_turns: int | None = None
    timeout_s: float | None = None
    session_kind: str = "sidecar"
    gate: str | None = None
    triggers: tuple[TriggerPipeline, ...] = ()
    icon: str | None = None
    description: str = ""
    template_id: str | None = None
    # Tool access — None or "none" means text-only (current default).
    tool_access: str = TOOL_ACCESS_NONE
    tool_policy: SidecarToolPolicy | None = None
    # Per-sidecar preset override — None inherits from the parent job.
    preset: str | None = None
    # Whether this sidecar auto-attaches to every new job.
    # False (default) means it's available but requires explicit attachment.
    auto_attach: bool = False


# ---------------------------------------------------------------------------
# Per-job runtime state
# ---------------------------------------------------------------------------


@dataclass
class _JobState:
    """Mutable per-job dispatcher state."""

    definitions: list[SidecarDefinition] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    in_flight: set[str] = field(default_factory=set)  # sidecar names currently executing
    fired_once: set[tuple[str, int]] = field(default_factory=set)  # (name, pipeline_idx) already fired
    last_timer: dict[str, float] = field(default_factory=dict)  # name → monotonic time of last timer fire
    last_activity: float = field(default_factory=time.monotonic)
    gated_since: float | None = None  # monotonic time when agent was paused by a gate
    executing_depth: int = 0  # recursion guard: >0 means pipeline is running for this job
    queued: list[tuple[SidecarDefinition, int, dict[str, Any] | None]] = field(default_factory=list)
    # Per-sidecar monotonic time of last agent message injection.
    # Used to gate injections: skip if agent has had no activity since last inject.
    last_agent_inject: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# JSON → dataclass hydration (for template definitions from DB)
# ---------------------------------------------------------------------------


def _hydrate_condition(raw: dict[str, Any]) -> TriggerCondition:
    """Convert a JSON condition dict to a frozen dataclass."""
    kind = raw.get("kind", "manual")
    if kind == "event":
        event_kinds = raw.get("eventKinds") or raw.get("eventKind")
        event_kinds = (event_kinds,) if isinstance(event_kinds, str) else tuple(event_kinds or ())
        return EventCondition(
            event_kinds=event_kinds,
            event_filter=raw.get("eventFilter") or {},
            once=raw.get("once", False),
        )
    if kind == "timer":
        return TimerCondition(
            interval_s=float(raw.get("intervalS", raw.get("interval_s", 60))),
            idle_guard_s=raw.get("idleGuardS") or raw.get("idle_guard_s"),
        )
    if kind == "threshold":
        return ThresholdCondition(
            metric=raw.get("metric", "messages"),
            value=int(raw.get("value", 1)),
            once=raw.get("once", False),
        )
    if kind == "regex":
        pattern_str = raw.get("pattern", "")
        try:
            compiled = re.compile(pattern_str)
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern: {exc}") from exc
        return RegexCondition(
            pattern=pattern_str,
            source=raw.get("source", "messages"),
            once=raw.get("once", False),
            _compiled=compiled,
        )
    if kind == "file_pattern":
        return FilePatternCondition(
            glob=raw.get("glob", "**/*"),
            change_kind=raw.get("changeKind", raw.get("change_kind", "any")),
        )
    if kind == "content_match":
        kw = raw.get("keywords", [])
        return ContentMatchCondition(
            keywords=tuple(kw) if isinstance(kw, list) else (kw,),
            case_sensitive=raw.get("caseSensitive", False),
            source=raw.get("source", "messages"),
            once=raw.get("once", False),
        )
    return ManualCondition()


def _hydrate_parser(raw: dict[str, Any] | None) -> OutputParser:
    if not raw:
        return PlainText()
    kind = raw.get("kind", "plain_text")
    if kind == "json_object":
        keys = raw.get("requiredKeys") or raw.get("required_keys") or ()
        return JsonObject(required_keys=tuple(keys))
    if kind == "json_array":
        keys = raw.get("itemKeys") or raw.get("item_keys") or ()
        return JsonArray(item_keys=tuple(keys))
    return PlainText(strip=raw.get("strip", True))


def _hydrate_route(raw: dict[str, Any]) -> OutputRoute:
    kind = raw.get("kind", "event_bus")
    if kind == "event_bus":
        event_kind = raw.get("eventKind", "sidecar_result")
        if not event_kind.startswith("sidecar_"):
            event_kind = f"sidecar_{event_kind}"
        return EventBusRoute(
            event_kind=event_kind,
            payload_mapping=raw.get("payloadMapping") or {},
        )
    if kind == "job_metadata":
        return JobMetadataRoute(field_name=raw.get("field", ""))
    if kind == "callback":
        return CallbackRoute(callback_name=raw.get("callbackName", ""))
    if kind == "conditional":
        return ConditionalRoute(
            field_name=raw.get("field", ""),
            value=raw.get("value", ""),
            inner=_hydrate_route(raw.get("inner", {})),
        )
    if kind == "agent_message":
        return AgentMessageRoute(
            role=raw.get("role", "system"),
            label=raw.get("label", ""),
        )
    if kind == "gate":
        return GateRoute(
            verdict_field=raw.get("verdictField", "verdict"),
            reason_field=raw.get("reasonField", "reason"),
            timeout_s=float(raw["timeoutS"]) if "timeoutS" in raw else None,
        )
    return EventBusRoute(event_kind="sidecar_result")


def _hydrate_tool_policy(raw: dict[str, Any] | None) -> SidecarToolPolicy | None:
    """Convert a JSON toolPolicy dict to a frozen dataclass."""
    if not raw:
        return None

    def _as_list(key: str) -> list[str]:
        val = raw.get(key, [])
        if not isinstance(val, list):
            raise ValueError(f"toolPolicy.{key} must be a list, got {type(val).__name__}")
        return val

    path_scope = raw.get("pathScope", "worktree")
    if path_scope not in ("worktree", "repo"):
        raise ValueError(f"Invalid pathScope {path_scope!r}; must be 'worktree' or 'repo'")

    return SidecarToolPolicy(
        allowed_categories=frozenset(_as_list("allowedCategories")),
        blocked_tools=frozenset(_as_list("blockedTools")),
        mcp_servers=frozenset(_as_list("mcpServers")),
        path_scope=path_scope,
        shell_readonly=raw.get("shellReadonly", True),
        shell_allowlist=tuple(_as_list("shellAllowlist")),
    )


def hydrate_definition(raw: dict[str, Any]) -> SidecarDefinition:
    """Convert a JSON definition dict (from DB or API) to a SidecarDefinition."""
    triggers = []
    for t in raw.get("triggers", []):
        triggers.append(
            TriggerPipeline(
                condition=_hydrate_condition(t.get("condition", {})),
                context_sources=tuple(t.get("contextSources", ())),
                prompt_template=t.get("promptTemplate", ""),
                output_parser=_hydrate_parser(t.get("outputParser")),
                output_routes=tuple(_hydrate_route(r) for r in t.get("outputRoutes", [])),
                concurrency=Concurrency(t.get("concurrency", "skip_if_running")),
            )
        )
    return SidecarDefinition(
        name=raw.get("name", "unnamed"),
        phase=raw.get("phase", "midflight"),
        lifetime=raw.get("lifetime", "ephemeral"),
        scope=raw.get("scope", "global"),
        model=raw.get("model"),
        system_prompt=raw.get("systemPrompt", ""),
        max_turns=raw.get("maxTurns") or raw.get("max_turns"),
        timeout_s=raw.get("timeoutS") or raw.get("timeout_s"),
        session_kind=raw.get("sessionKind", "sidecar"),
        gate=raw.get("gate"),
        triggers=tuple(triggers),
        icon=raw.get("icon"),
        description=raw.get("description", ""),
        template_id=raw.get("templateId") or raw.get("template_id"),
        tool_access=raw.get("toolAccess", raw.get("tool_access", TOOL_ACCESS_NONE)),
        tool_policy=_hydrate_tool_policy(raw.get("toolPolicy") or raw.get("tool_policy")),
        preset=raw.get("preset"),
        auto_attach=raw.get("autoAttach", raw.get("auto_attach", False)),
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class SidecarDispatcher:
    """Central dispatcher for all sidecar trigger evaluation and execution.

    One instance per process.  Subscribes to EventBus at startup, evaluates
    conditions for all active jobs, and executes matching trigger pipelines.
    """

    def __init__(
        self,
        session_manager: SidecarSessionManager,
        event_bus: EventBus,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        gate_handler: Callable[[str, str, str, str], Awaitable[None]] | None = None,
        agent_message_handler: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._gate_handler = gate_handler
        self._agent_message_handler = agent_message_handler
        self._jobs: dict[str, _JobState] = {}

        # Extensible registries (populated at startup by service owners)
        self._context_providers: dict[str, ContextProvider] = {}
        self._callbacks: dict[str, OutputCallback] = {}

        # Background tasks
        self._timer_task: asyncio.Task[None] | None = None
        self._tick_interval_s = 5.0  # base tick rate for timer conditions

    # -- Registration -------------------------------------------------------

    def register_context(self, name: str, provider: ContextProvider) -> None:
        """Register a named context provider (called at startup)."""
        self._context_providers[name] = provider
        log.debug("dispatcher_context_registered", name=name)

    def register_callback(self, name: str, fn: OutputCallback) -> None:
        """Register a named output callback (called at startup)."""
        self._callbacks[name] = fn
        log.debug("dispatcher_callback_registered", name=name)

    def set_gate_handler(
        self,
        handler: Callable[[str, str, str, str], Awaitable[None]],
    ) -> None:
        """Set the gate verdict handler (called after dispatcher construction).

        Signature: ``async handler(job_id, sidecar_name, verdict, reason)``.
        """
        self._gate_handler = handler

    def set_agent_message_handler(
        self,
        handler: Callable[[str, str], Awaitable[None]],
    ) -> None:
        """Set the agent message injection handler.

        Signature: ``async handler(job_id, message)``.
        Called when an AgentMessageRoute fires to inject text into the agent conversation.
        """
        self._agent_message_handler = handler

    def set_gated(self, job_id: str, *, gated: bool) -> None:
        """Notify dispatcher that a job's agent is paused/resumed by a gate.

        When gated, timer idle guards treat the agent as idle from this moment.
        """
        state = self._jobs.get(job_id)
        if state:
            state.gated_since = time.monotonic() if gated else None

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start the periodic timer loop."""
        self._timer_task = asyncio.create_task(self._timer_loop(), name="sidecar-timer")
        log.info("sidecar_dispatcher_started")

    async def shutdown(self) -> None:
        """Stop the timer loop and clean up."""
        if self._timer_task:
            self._timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._timer_task
        self._jobs.clear()
        log.info("sidecar_dispatcher_shutdown")

    def activate(self, job_id: str, definitions: list[SidecarDefinition]) -> None:
        """Register sidecar definitions for a job.

        Called by RuntimeService at job start.  Opens sessions in the
        session manager for non-ephemeral sidecars.
        """
        if job_id in self._jobs:
            log.warning("dispatcher_activate_duplicate", job_id=job_id)
            return
        state = _JobState(definitions=definitions)
        self._jobs[job_id] = state

        for defn in definitions:
            if defn.lifetime != "ephemeral":
                self._session_manager.open(
                    job_id,
                    defn.name,
                    config=SidecarConfig(
                        name=defn.name,
                        phase=cast("SidecarPhase", defn.phase),
                        lifetime=cast("SidecarLifetime", defn.lifetime),
                        system_prompt=defn.system_prompt,
                        max_turns=defn.max_turns,
                        timeout_s=defn.timeout_s,
                    ),
                )

        log.info(
            "dispatcher_activated",
            job_id=job_id,
            sidecars=[d.name for d in definitions],
        )

    async def deactivate(self, job_id: str) -> None:
        """Close all sidecars for a job and flush metrics."""
        state = self._jobs.pop(job_id, None)
        if not state:
            return
        # Drain any queued pipelines before closing sessions
        for queued_defn, queued_idx, queued_ctx in state.queued:
            try:
                await self._execute_pipeline(job_id, queued_defn, queued_defn.triggers[queued_idx], queued_ctx)
            except Exception:
                log.warning(
                    "dispatcher_drain_error",
                    job_id=job_id,
                    sidecar=queued_defn.name,
                    exc_info=True,
                )
        self._session_manager.close_job(job_id)
        log.info("dispatcher_deactivated", job_id=job_id)

    # -- Trigger entry points -----------------------------------------------

    async def handle_event(self, event: SessionEvent) -> None:
        """EventBus subscriber.  Evaluate event/regex/content/file conditions."""
        if is_observation_history(event) or is_admission_receipt(event):
            return
        for job_id, state in list(self._jobs.items()):
            # Only update activity for events belonging to this job (or global events)
            if not event.session_id or event.session_id == job_id:
                state.last_activity = time.monotonic()

            # Recursion guard: if this job is already inside a pipeline execution,
            # skip trigger evaluation to prevent sidecar-generated events from
            # causing infinite loops (sidecar A fires → emits event → triggers B → emits → triggers A...).
            if state.executing_depth > 0:
                continue

            for defn in state.definitions:
                for idx, pipeline in enumerate(defn.triggers):
                    cond = pipeline.condition

                    # EventCondition
                    if isinstance(cond, EventCondition):
                        if event.kind not in cond.event_kinds:
                            continue
                        # Check event filter (payload key=value matching)
                        if cond.event_filter:
                            payload = event.payload or {}
                            if not all(str(payload.get(k)) == v for k, v in cond.event_filter.items()):
                                continue
                        # Check job_id match (events carry job_id)
                        if hasattr(event, "job_id") and event.session_id and event.session_id != job_id:
                            continue
                        if cond.once and (defn.name, idx) in state.fired_once:
                            continue
                        if cond.once:
                            state.fired_once.add((defn.name, idx))
                        extra = {"payload": event.payload} if event.payload else None
                        await self._try_execute(job_id, state, defn, idx, extra)

                    # RegexCondition — match against transcript content from event payload
                    elif isinstance(cond, RegexCondition):
                        content = self._extract_content(event, cond.source)
                        if content is None:
                            continue
                        if hasattr(event, "job_id") and event.session_id and event.session_id != job_id:
                            continue
                        match = (cond._compiled or re.compile(cond.pattern)).search(content)  # noqa: SLF001
                        if not match:
                            continue
                        if cond.once and (defn.name, idx) in state.fired_once:
                            continue
                        if cond.once:
                            state.fired_once.add((defn.name, idx))
                        extra: dict[str, Any] = {"match": match.group(0), **match.groupdict()}  # type: ignore[no-redef]
                        await self._try_execute(job_id, state, defn, idx, extra)

                    # ContentMatchCondition — keyword substring matching
                    elif isinstance(cond, ContentMatchCondition):
                        content = self._extract_content(event, cond.source)
                        if content is None:
                            continue
                        if hasattr(event, "job_id") and event.session_id and event.session_id != job_id:
                            continue
                        check_content = content if cond.case_sensitive else content.lower()
                        matched = any(
                            (kw if cond.case_sensitive else kw.lower()) in check_content for kw in cond.keywords
                        )
                        if not matched:
                            continue
                        if cond.once and (defn.name, idx) in state.fired_once:
                            continue
                        if cond.once:
                            state.fired_once.add((defn.name, idx))
                        await self._try_execute(job_id, state, defn, idx, None)

                    # FilePatternCondition — match changed file paths from diff events
                    elif isinstance(cond, FilePatternCondition):
                        if event.kind != EventKind.diff_updated:
                            continue
                        if hasattr(event, "job_id") and event.session_id and event.session_id != job_id:
                            continue
                        changed_files = self._extract_changed_files(event)
                        if not changed_files:
                            continue
                        matched_files = [
                            f
                            for f in changed_files
                            if fnmatch(f.get("path", ""), cond.glob)
                            and (cond.change_kind == "any" or f.get("change_kind") == cond.change_kind)
                        ]
                        if not matched_files:
                            continue
                        extra: dict[str, Any] = {"matched_files": [f["path"] for f in matched_files]}  # type: ignore[no-redef]
                        await self._try_execute(job_id, state, defn, idx, extra)

    def increment(self, job_id: str, metric: str, delta: int = 1) -> None:
        """Increment a threshold counter.  Called by RuntimeService."""
        state = self._jobs.get(job_id)
        if not state:
            return
        state.counters[metric] += delta
        state.last_activity = time.monotonic()

        # Check threshold conditions synchronously, schedule async execution
        for defn in state.definitions:
            for idx, pipeline in enumerate(defn.triggers):
                cond = pipeline.condition
                if not isinstance(cond, ThresholdCondition):
                    continue
                if cond.metric != metric:
                    continue
                if state.counters[metric] < cond.value:
                    continue
                if cond.once and (defn.name, idx) in state.fired_once:
                    continue
                if cond.once:
                    state.fired_once.add((defn.name, idx))
                asyncio.create_task(
                    self._try_execute(job_id, state, defn, idx, None),
                    name=f"sidecar-threshold-{defn.name}",
                )

    async def fire(
        self,
        job_id: str,
        sidecar_name: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Manual trigger — fires all ManualCondition pipelines for a sidecar.

        Returns True if the job was found (even if no manual pipelines matched).
        """
        state = self._jobs.get(job_id)
        if not state:
            log.warning("dispatcher_fire_no_job", job_id=job_id, sidecar=sidecar_name)
            return False
        for defn in state.definitions:
            if defn.name != sidecar_name:
                continue
            for idx, pipeline in enumerate(defn.triggers):
                if isinstance(pipeline.condition, ManualCondition):
                    await self._try_execute(job_id, state, defn, idx, context)
        return True

    async def run_preflight(self, job_id: str) -> None:
        """Execute all preflight sidecars before the job agent starts."""
        state = self._jobs.get(job_id)
        if not state:
            return
        for defn in state.definitions:
            if defn.phase != "preflight":
                continue
            # Open session for preflight
            self._session_manager.open(
                job_id,
                defn.name,
                config=SidecarConfig(
                    name=defn.name,
                    phase=cast("SidecarPhase", defn.phase),
                    lifetime=cast("SidecarLifetime", defn.lifetime),
                    system_prompt=defn.system_prompt,
                    max_turns=defn.max_turns,
                    timeout_s=defn.timeout_s,
                ),
            )
            for idx, _pipeline in enumerate(defn.triggers):
                await self._try_execute(job_id, state, defn, idx, None)

    async def run_postflight(self, job_id: str) -> None:
        """Execute all postflight sidecars for a completed job."""
        state = self._jobs.get(job_id)
        if not state:
            return
        for defn in state.definitions:
            if defn.phase != "postflight":
                continue
            # Open session for postflight
            self._session_manager.open(
                job_id,
                defn.name,
                config=SidecarConfig(
                    name=defn.name,
                    phase=cast("SidecarPhase", defn.phase),
                    lifetime=cast("SidecarLifetime", defn.lifetime),
                    system_prompt=defn.system_prompt,
                    max_turns=defn.max_turns,
                    timeout_s=defn.timeout_s,
                ),
            )
            for idx, _pipeline in enumerate(defn.triggers):
                await self._try_execute(job_id, state, defn, idx, None)

    # -- Timer loop ---------------------------------------------------------

    async def _timer_loop(self) -> None:
        """Periodic tick — evaluates TimerCondition for all active jobs."""
        while True:
            try:
                await asyncio.sleep(self._tick_interval_s)
                await self._tick()
            except asyncio.CancelledError:
                return
            except Exception:
                log.error("dispatcher_timer_error", exc_info=True)

    async def _tick(self) -> None:
        """Single timer tick — check all timer conditions."""
        now = time.monotonic()
        for job_id, state in list(self._jobs.items()):
            for defn in state.definitions:
                for idx, pipeline in enumerate(defn.triggers):
                    cond = pipeline.condition
                    if not isinstance(cond, TimerCondition):
                        continue

                    # Idle guard: skip if activity is too recent
                    # When the agent is gated (paused), treat it as idle
                    # since the gate time — the agent isn't making progress.
                    if cond.idle_guard_s is not None:
                        idle_s = now - state.gated_since if state.gated_since is not None else now - state.last_activity
                        if idle_s < cond.idle_guard_s:
                            continue

                    # Interval: skip if too soon since last fire
                    key = f"{defn.name}:{idx}"
                    last = state.last_timer.get(key, 0.0)
                    if (now - last) < cond.interval_s:
                        continue

                    state.last_timer[key] = now
                    await self._try_execute(job_id, state, defn, idx, None)

    # -- Pipeline execution -------------------------------------------------

    async def _try_execute(
        self,
        job_id: str,
        state: _JobState,
        defn: SidecarDefinition,
        pipeline_idx: int,
        extra_context: dict[str, Any] | None,
    ) -> None:
        """Acquire concurrency slot and execute pipeline, or queue/skip."""
        pipeline = defn.triggers[pipeline_idx]

        # Gate check
        if defn.gate and await self._is_gated(job_id, defn.gate):
            return

        # Concurrency control
        flight_key = defn.name
        if flight_key in state.in_flight:
            if pipeline.concurrency == Concurrency.skip_if_running:
                return
            if pipeline.concurrency == Concurrency.queue:
                # Natural bound: one queued item per (definition, pipeline) pair.
                max_queued = sum(len(d.triggers) for d in state.definitions)
                if len(state.queued) < max_queued:
                    state.queued.append((defn, pipeline_idx, extra_context))
                else:
                    log.warning("dispatcher_queue_full", job_id=job_id, sidecar=defn.name, cap=max_queued)
                return
            # parallel — fall through

        state.in_flight.add(flight_key)
        state.executing_depth += 1
        try:
            await self._execute_pipeline(job_id, defn, pipeline, extra_context)
        except Exception:
            log.error(
                "dispatcher_pipeline_error",
                job_id=job_id,
                sidecar=defn.name,
                exc_info=True,
            )
        finally:
            state.executing_depth -= 1
            state.in_flight.discard(flight_key)
            # Drain queue for this sidecar
            await self._drain_queue(job_id, state, defn.name)

    async def _drain_queue(
        self,
        job_id: str,
        state: _JobState,
        sidecar_name: str,
    ) -> None:
        """Execute the next queued pipeline for a sidecar, if any."""
        remaining = []
        fired = False
        for queued_defn, queued_idx, queued_ctx in state.queued:
            if queued_defn.name == sidecar_name and not fired:
                fired = True
                state.in_flight.add(sidecar_name)
                state.executing_depth += 1
                try:
                    await self._execute_pipeline(
                        job_id,
                        queued_defn,
                        queued_defn.triggers[queued_idx],
                        queued_ctx,
                    )
                except Exception:
                    log.error("dispatcher_queued_error", exc_info=True)
                finally:
                    state.executing_depth -= 1
                    state.in_flight.discard(sidecar_name)
            else:
                remaining.append((queued_defn, queued_idx, queued_ctx))
        state.queued = remaining

    async def _execute_pipeline(
        self,
        job_id: str,
        defn: SidecarDefinition,
        pipeline: TriggerPipeline,
        extra_context: dict[str, Any] | None,
    ) -> None:
        """The core execution sequence — same 7 steps for every sidecar."""
        # 1. Assemble context
        ctx: dict[str, Any] = {}
        for source_name in pipeline.context_sources:
            provider = self._context_providers.get(source_name)
            if provider is None:
                log.warning("dispatcher_unknown_provider", name=source_name)
                continue
            result = await provider(job_id)
            if result is None:
                log.debug(
                    "dispatcher_skip_null_context",
                    job_id=job_id,
                    sidecar=defn.name,
                    source=source_name,
                )
                return  # required context missing → skip
            ctx.update(result)
        if extra_context:
            ctx.update(extra_context)

        # 2. Render prompt
        if not pipeline.prompt_template:
            return
        try:
            prompt = _safe_fmt.vformat(pipeline.prompt_template, (), ctx)
        except KeyError as e:
            log.warning(
                "dispatcher_template_key_error",
                job_id=job_id,
                sidecar=defn.name,
                missing_key=str(e),
            )
            return

        # 3. Get or create session
        if defn.lifetime == "ephemeral":
            # Ephemeral: create a one-shot session, call, discard
            session = self._session_manager.make_ephemeral(
                job_id=job_id,
                system_prompt=defn.system_prompt,
                max_turns=1,
            )
        else:
            session_or_none = self._session_manager.get(job_id, defn.name)
            if session_or_none is None:
                # Session expired or not found — re-open for windowed/persistent
                if defn.lifetime in ("windowed", "persistent"):
                    self._session_manager.open(
                        job_id,
                        defn.name,
                        config=SidecarConfig(
                            name=defn.name,
                            phase=cast("SidecarPhase", defn.phase),
                            lifetime=cast("SidecarLifetime", defn.lifetime),
                            system_prompt=defn.system_prompt,
                            max_turns=defn.max_turns,
                            timeout_s=defn.timeout_s,
                        ),
                    )
                    session_or_none = self._session_manager.get(job_id, defn.name)
                if session_or_none is None:
                    log.warning("dispatcher_no_session", job_id=job_id, sidecar=defn.name)
                    return
            session = session_or_none

        # 4. Call LLM
        if defn.tool_access != TOOL_ACCESS_NONE and defn.tool_access != "none":
            # Agentic sidecar — create a full SDK session with tool access.
            raw = await self._call_agentic(job_id, defn, prompt)
            if raw is None:
                return
        else:
            # Text-only sidecar — single completion call.
            try:
                raw = await session.complete(prompt, timeout=defn.timeout_s or 30.0)
            except (TimeoutError, OSError, RuntimeError):
                log.warning("dispatcher_llm_error", job_id=job_id, sidecar=defn.name, exc_info=True)
                return

        # 5. Parse output
        parsed = self._parse(raw, pipeline.output_parser)
        if parsed is None:
            return

        # 6. Emit sidecar transcript event (for UI visibility).
        # Skip when the pipeline routes to AgentMessageRoute — that route now
        # emits its own SSE-visible event, so a separate transcript entry would
        # cause a duplicate bubble in the UI.
        has_agent_route = any(isinstance(r, AgentMessageRoute) for r in pipeline.output_routes)
        if not has_agent_route:
            await self._emit_transcript_event(job_id, defn, parsed)

        # 7. Route output
        for route in pipeline.output_routes:
            try:
                await self._route(job_id, defn, parsed, route)
            except Exception:
                log.error(
                    "dispatcher_route_error",
                    job_id=job_id,
                    sidecar=defn.name,
                    route_kind=type(route).__name__,
                    exc_info=True,
                )

    # -- Parsing ------------------------------------------------------------

    async def _call_agentic(
        self,
        job_id: str,
        defn: SidecarDefinition,
        prompt: str,
    ) -> str | None:
        """Execute a sidecar with agentic tool access.

        Builds a ``SessionConfig`` from the session's adapter, injects a
        ``blocking_permission_handler`` backed by ``SidecarPolicyRouter``,
        and runs an ``AgenticSidecarSession``.
        """
        from backend.services.sidecar.policy_router import SidecarPolicyRouter
        from backend.services.sidecar.session import AgenticSidecarSession

        # We need the adapter from the session manager.
        adapter = self._session_manager._adapter  # noqa: SLF001

        # Build a minimal SessionConfig with policy enforcement.
        worktree: str | None = None
        provider = self._context_providers.get("worktree_path")
        if provider is not None:
            result = await provider(job_id)
            if isinstance(result, dict):
                worktree = result.get("worktree_path") or result.get("worktreePath")

        async def _permission_handler(tool_name: str, tool_input_json: str) -> str:
            """Policy enforcement callback injected into the SDK session."""
            if defn.tool_policy is None:
                return "allow"
            import json as _json

            try:
                tool_input = _json.loads(tool_input_json) if tool_input_json else {}
            except (ValueError, TypeError):
                tool_input = {}
            decision = SidecarPolicyRouter.evaluate(
                tool_name=tool_name,
                tool_input=tool_input,
                policy=defn.tool_policy,
                worktree_path=worktree or "",
            )
            if decision.proceed:
                return "allow"
            log.info(
                "sidecar_tool_denied",
                job_id=job_id,
                sidecar=defn.name,
                tool=tool_name,
                reason=decision.reason,
            )
            return decision.reason or "denied by sidecar policy"

        config = SessionConfig(
            workspace_path=worktree or "",
            prompt=prompt,
            job_id=job_id,
            sdk="copilot",
            model=defn.model or self._session_manager.model,
            blocking_permission_handler=_permission_handler,
            max_turns=defn.max_turns,
            session_kind="sidecar",
            # Sidecar's own identity — not the main-agent CODEPLANE_SYSTEM_PROMPT
            system_prompt_override=defn.system_prompt or None,
        )

        agentic = AgenticSidecarSession(
            adapter=adapter,
            session_config=config,
            max_turns=defn.max_turns,
            timeout_s=defn.timeout_s,
        )
        try:
            run_result = await agentic.run(prompt, timeout=defn.timeout_s or 120.0)
        except Exception:
            log.warning("dispatcher_agentic_error", job_id=job_id, sidecar=defn.name, exc_info=True)
            return None
        return run_result

    def _parse(self, raw: str, parser: OutputParser) -> dict[str, Any] | str | list[Any] | None:
        """Parse raw LLM response according to the output parser spec."""
        if isinstance(parser, PlainText):
            return raw.strip() if parser.strip else raw

        text = raw.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            if len(lines) >= 2:
                text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].rstrip()

        if isinstance(parser, JsonObject):
            try:
                obj = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                log.debug("dispatcher_parse_failed", parser="json_object", raw=text[:200])
                return None
            if not isinstance(obj, dict):
                log.debug("dispatcher_parse_not_dict", raw=text[:200])
                return None
            for key in parser.required_keys:
                if key not in obj:
                    log.debug("dispatcher_parse_missing_key", key=key)
                    return None
            return obj

        if isinstance(parser, JsonArray):
            try:
                arr = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                log.debug("dispatcher_parse_failed", parser="json_array", raw=text[:200])
                return None
            if not isinstance(arr, list):
                return None
            return arr

        return raw

    # -- Routing ------------------------------------------------------------

    async def _route(
        self,
        job_id: str,
        defn: SidecarDefinition,
        parsed: Any,
        route: OutputRoute,
    ) -> None:
        """Deliver parsed output to a destination."""
        from backend.models.events import EventKind, new_event

        if isinstance(route, EventBusRoute):
            payload: dict[str, Any] = {"sidecar_name": defn.name}
            if isinstance(parsed, dict):
                if route.payload_mapping:
                    for dest_key, src_key in route.payload_mapping.items():
                        payload[dest_key] = parsed.get(src_key)
                else:
                    payload.update(parsed)
            elif isinstance(parsed, str):
                payload["content"] = parsed
            else:
                payload["result"] = parsed
            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=EventKind(route.event_kind),
                    payload=payload,
                )
            )

        elif isinstance(route, JobMetadataRoute):
            # Publish a metadata update event — consumers (job service) handle persistence
            value = parsed if isinstance(parsed, str) else json.dumps(parsed)
            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=EventKind.job_title_updated
                    if route.field_name == "title"
                    else EventKind.sidecar_metadata_update,
                    payload={"field": route.field_name, "value": value, "sidecar_name": defn.name},
                )
            )

        elif isinstance(route, CallbackRoute):
            callback = self._callbacks.get(route.callback_name)
            if callback is None:
                log.warning("dispatcher_unknown_callback", name=route.callback_name)
                return
            data = parsed if isinstance(parsed, dict) else {"content": parsed}
            await callback(job_id, data)

        elif isinstance(route, ConditionalRoute):
            if isinstance(parsed, dict) and str(parsed.get(route.field_name)) == route.value:
                await self._route(job_id, defn, parsed, route.inner)

        elif isinstance(route, AgentMessageRoute):
            content = parsed if isinstance(parsed, str) else json.dumps(parsed)
            raw_label = route.label or defn.name or ""
            # Strip stray brackets so user-authored labels like "[Foo]" don't
            # get double-wrapped to "[[Foo]]".
            raw_label = raw_label.strip("[]").strip()
            label_prefix = f"[{raw_label}] " if raw_label else ""
            # Message injected into the agent includes the label for context.
            full_message = f"{label_prefix}{content}"
            # Publish unified secondary session events for visibility
            import uuid as _uuid

            from backend.models.events import EventKind, new_event
            from backend.models.secondary_session import EntryKind, SecondarySessionKind, SecondarySessionStatus

            _sess_id = str(_uuid.uuid4())
            _now = datetime.now(UTC)
            # Human-friendly display name: title-case the label/name for UI.
            _display_name = (
                raw_label.replace("-", " ").replace("_", " ").title()
                if raw_label
                else defn.name.replace("-", " ").replace("_", " ").title()
            )
            _icon = defn.icon or "bot"

            if self._session_factory is not None:
                from backend.persistence.secondary_session_repo import SecondarySessionRepository

                _repo = SecondarySessionRepository(self._session_factory)
                await _repo.create_session(
                    session_id=_sess_id,
                    job_id=job_id,
                    kind=SecondarySessionKind.sidecar.value,
                    name=_display_name,
                    icon=_icon,
                    started_at=_now,
                )
                await _repo.add_entry(
                    session_id=_sess_id,
                    seq=1,
                    timestamp=_now,
                    kind=EntryKind.output.value,
                    content=content,
                )
                await _repo.complete_session(
                    _sess_id,
                    status=SecondarySessionStatus.completed.value,
                    completed_at=_now,
                    output=content,
                )
            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    timestamp=_now,
                    kind=EventKind.secondary_session_started,
                    payload={
                        "session_id": _sess_id,
                        "kind": SecondarySessionKind.sidecar.value,
                        "name": _display_name,
                        "icon": _icon,
                    },
                )
            )
            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    timestamp=_now,
                    kind=EventKind.secondary_session_completed,
                    payload={
                        "session_id": _sess_id,
                        "status": SecondarySessionStatus.completed.value,
                        "output": content,
                    },
                )
            )
            # Activity-gated injection: skip if agent has had no activity
            # since our last injection from this sidecar. This prevents
            # timer-based sidecars from flooding a stalled agent with
            # dozens of queued messages.
            if self._agent_message_handler is not None:
                state = self._jobs.get(job_id)
                should_inject = True
                if state is not None:
                    last_inject = state.last_agent_inject.get(defn.name)
                    if last_inject is not None and state.last_activity <= last_inject:
                        # Agent hasn't produced any activity since our last
                        # injection — skip to avoid message pile-up.
                        should_inject = False
                        log.debug(
                            "dispatcher_agent_inject_skipped_no_activity",
                            job_id=job_id,
                            sidecar=defn.name,
                        )
                if should_inject:
                    try:
                        await self._agent_message_handler(job_id, full_message)
                        if state is not None:
                            state.last_agent_inject[defn.name] = time.monotonic()
                    except Exception:
                        log.warning(
                            "dispatcher_agent_message_inject_failed",
                            job_id=job_id,
                            sidecar=defn.name,
                            exc_info=True,
                        )

        elif isinstance(route, GateRoute):
            if not isinstance(parsed, dict):
                log.warning("dispatcher_gate_not_dict", sidecar=defn.name)
                return
            raw_verdict = parsed.get(route.verdict_field)
            if raw_verdict is None or str(raw_verdict).strip() == "":
                log.warning(
                    "dispatcher_gate_missing_verdict",
                    sidecar=defn.name,
                    job_id=job_id,
                    field=route.verdict_field,
                )
                return
            verdict = str(raw_verdict).lower().strip()
            reason = str(parsed.get(route.reason_field, ""))
            await self._event_bus.publish(
                new_event(
                    session_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=EventKind.sidecar_gate_verdict,
                    payload={
                        "sidecar_name": defn.name,
                        "verdict": verdict,
                        "reason": reason,
                    },
                )
            )
            # Actually enforce the gate: pause or resume the agent.
            if self._gate_handler is not None:
                await self._gate_handler(job_id, defn.name, verdict, reason)

    # -- Transcript event ---------------------------------------------------

    async def _emit_transcript_event(
        self,
        job_id: str,
        defn: SidecarDefinition,
        parsed: Any,
    ) -> None:
        """Publish secondary session events so the sidecar's output appears in the feed."""
        import uuid

        from backend.models.events import EventKind, new_event
        from backend.models.secondary_session import EntryKind, SecondarySessionKind, SecondarySessionStatus

        content = parsed if isinstance(parsed, str) else json.dumps(parsed)
        session_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        # Persist to DB if session_factory available
        if self._session_factory is not None:
            from backend.persistence.secondary_session_repo import SecondarySessionRepository

            repo = SecondarySessionRepository(self._session_factory)
            await repo.create_session(
                session_id=session_id,
                job_id=job_id,
                kind=SecondarySessionKind.sidecar.value,
                name=defn.name,
                icon=defn.icon or "bot",
                started_at=now,
            )
            await repo.add_entry(
                session_id=session_id,
                seq=1,
                timestamp=now,
                kind=EntryKind.output.value,
                content=content,
            )
            await repo.complete_session(
                session_id,
                status=SecondarySessionStatus.completed.value,
                completed_at=now,
                output=content,
            )

        # Always emit SSE events for live frontend display
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=now,
                kind=EventKind.secondary_session_started,
                payload={
                    "session_id": session_id,
                    "kind": SecondarySessionKind.sidecar.value,
                    "name": defn.name,
                    "icon": defn.icon or "bot",
                },
            )
        )
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=now,
                kind=EventKind.secondary_session_completed,
                payload={
                    "session_id": session_id,
                    "status": SecondarySessionStatus.completed.value,
                    "output": content,
                },
            )
        )

    # -- Helpers ------------------------------------------------------------

    async def _is_gated(self, job_id: str, gate_field: str) -> bool:
        """Check if a job-level gate field is False (disabled)."""
        # Gate checking requires access to job state — delegate to a context provider
        provider = self._context_providers.get("job_gate")
        if provider is None:
            return False  # no gate provider → not gated
        result = await provider(job_id)
        if result is None:
            return True  # provider returned None → gated
        return not result.get(gate_field, True)

    @staticmethod
    def _extract_content(event: SessionEvent, source: str) -> str | None:
        """Extract text content from a domain event for regex/content matching."""
        payload = event.payload or {}

        if source == "messages":
            if event.kind in (EventKind.message_assistant, EventKind.message_delta):
                return str(payload.get("content")) if payload.get("content") is not None else None
            return None

        if source == "tool_calls":
            if event.kind == EventKind.tool_call_completed:
                return str(payload.get("tool_name", ""))
            return None

        if source == "tool_output":
            if event.kind == EventKind.tool_call_completed:
                return str(payload.get("result")) if payload.get("result") is not None else None
            return None

        return None

    @staticmethod
    def _extract_changed_files(event: SessionEvent) -> list[dict[str, str]]:
        """Extract file change info from a diff.updated event."""
        payload = event.payload or {}
        files = payload.get("files") or payload.get("changed_files") or []
        if isinstance(files, list):
            return [f if isinstance(f, dict) else {"path": str(f), "change_kind": "any"} for f in files]
        return []
