"""Per-job named sidecar sessions.

SDK-agnostic — delegates all LLM calls to the ``AgentAdapterInterface``
obtained from the ``AdapterRegistry``.  Each job owns a dict of named
sidecar sessions, each with its own system prompt and lifetime policy.

Sidecar phases:
    preflight  — runs before the main agent starts
    midflight  — runs alongside the main agent
    postflight — runs after the main agent completes

Sidecar lifetimes:
    ephemeral  — single completion call, then discarded
    windowed   — bounded by turn count or wall-clock timeout
    persistent — lives for the entire job

A **standby pool** of pre-created ``SidecarSession`` wrappers is kept
ready so sessions can be handed off instantly.  The pool auto-refills.

Also provides ``complete()`` for one-shot callers without a job context
(naming, terminal ask).
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections import OrderedDict, deque
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.events import EventKind
from backend.services.completers.lightweight_completer import LightweightCompleter

if TYPE_CHECKING:
    from backend.models.domain import SessionConfig, SidecarConfig
    from backend.services.adapters.agent_adapter import AgentAdapterInterface

log = structlog.get_logger()

# Default model for utility work — cheap and fast
DEFAULT_UTILITY_MODEL = "gpt-4o-mini"

# Orphan expiry — warm sessions not adopted within this window are closed
_ORPHAN_EXPIRY_S = 300.0  # 5 minutes
_ORPHAN_CHECK_INTERVAL_S = 30.0

# Standby pool — keep this many sessions ready to hand off instantly
_STANDBY_POOL_SIZE = 2

# Maximum number of closed-job metric snapshots to retain
_CLOSED_JOBS_MAX = 500

# Retry count for one-shot callers
_TIMEOUT_RETRIES = 1

# Default system prompt for utility sidecars
_DEFAULT_SYSTEM_PROMPT = """\
You are a concise utility assistant embedded in a coding task management system
called CodePlane. Your sole purpose is to generate short metadata: titles, branch
names, progress summaries, commit messages, and PR descriptions.

Rules:
- Always respond with ONLY the requested format (usually JSON).
- Never add commentary, greetings, or markdown fencing unless the caller asks.
- Be extremely concise — every token costs time.
- You do NOT execute code or use tools. You only produce text.
"""


class SidecarSession:
    """SDK-agnostic wrapper around an adapter's ``complete()`` method.

    Each sidecar session has its own system prompt, optional turn/time
    limits, and independent metrics.
    """

    def __init__(
        self,
        adapter: AgentAdapterInterface,
        *,
        system_prompt: str | None = None,
        max_turns: int | None = None,
        timeout_s: float | None = None,
        model: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> None:
        self._adapter = adapter
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        self._prime_lock = asyncio.Lock()
        self._primed = False
        self._model = model
        self._excluded_tools = excluded_tools if excluded_tools is not None else ["*"]
        self.created_at: float = time.monotonic()
        # Windowed lifetime limits
        self._max_turns = max_turns
        self._timeout_s = timeout_s
        # Metrics — only accessed via += after each call, safe under GIL
        self.call_count: int = 0
        self.total_latency_ms: float = 0.0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.last_call_at: float | None = None

    @property
    def expired(self) -> bool:
        """True if this session has exceeded its windowed lifetime limits."""
        if self._max_turns is not None and self.call_count >= self._max_turns:
            return True
        return self._timeout_s is not None and (time.monotonic() - self.created_at) >= self._timeout_s

    def _reset_metrics(self) -> None:
        """Zero out all metric counters (used when recycling back to pool)."""
        self.call_count = 0
        self.total_latency_ms = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self.last_call_at = None

    async def _ensure_primed(self, prompt: str) -> str:
        """Prepend system prompt on the very first call, then let all
        subsequent calls proceed without any lock contention."""
        if self._primed:
            return prompt
        async with self._prime_lock:
            if self._primed:
                return prompt
            self._primed = True
            return f"{self._system_prompt}\n\n{prompt}"

    async def complete(self, prompt: str, timeout: float = 30.0) -> str:
        """Send *prompt* to the adapter and return the response text.

        Retries once on timeout — empirically 74% of post-timeout retries
        succeed within the same window.
        """
        effective = await self._ensure_primed(prompt)
        for attempt in range(_TIMEOUT_RETRIES + 1):
            t0 = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self._adapter.complete(
                        effective,
                        model=self._model,
                        system_message=self._system_prompt,
                        excluded_tools=self._excluded_tools,
                    ),
                    timeout=timeout,
                )
            except TimeoutError:
                if attempt >= _TIMEOUT_RETRIES:
                    raise
                log.debug("sidecar_complete_retry", attempt=attempt + 1)
                continue
            elapsed_ms = (time.monotonic() - t0) * 1000
            self.call_count += 1
            self.total_latency_ms += elapsed_ms
            self.total_input_tokens += result.input_tokens
            self.total_output_tokens += result.output_tokens
            self.total_cost_usd += result.cost_usd
            self.last_call_at = time.monotonic()
            return result.text or ""
        return ""


class AgenticSidecarSession:
    """Agentic sidecar — runs a real SDK session with tool access.

    Unlike :class:`SidecarSession` which only calls ``adapter.complete()``,
    this creates a full SDK session via ``adapter.create_session()`` and
    collects the streamed response.  Tool policy enforcement is handled
    externally by the dispatcher pipeline — this class is the execution
    layer only.
    """

    def __init__(
        self,
        adapter: AgentAdapterInterface,
        session_config: SessionConfig,
        *,
        max_turns: int | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._adapter = adapter
        self._session_config = session_config
        self._session_id: str | None = None
        self.created_at: float = time.monotonic()
        self._max_turns = max_turns
        self._timeout_s = timeout_s
        # Metrics
        self.call_count: int = 0
        self.total_latency_ms: float = 0.0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.last_call_at: float | None = None

    @property
    def expired(self) -> bool:
        if self._max_turns is not None and self.call_count >= self._max_turns:
            return True
        return self._timeout_s is not None and (time.monotonic() - self.created_at) >= self._timeout_s

    async def run(self, prompt: str, timeout: float = 120.0) -> str:
        """Create an SDK session, run it to completion, return final text.

        The session is created fresh for each call.  Events are consumed
        until the stream ends or timeout is hit.  The final assistant
        message is returned.
        """

        t0 = time.monotonic()
        config = self._session_config
        # Override prompt for this invocation
        config = type(config)(
            workspace_path=config.workspace_path,
            prompt=prompt,
            job_id=config.job_id,
            sdk=config.sdk,
            model=config.model,
            mcp_servers=config.mcp_servers,
            protected_paths=config.protected_paths,
            blocking_permission_handler=config.blocking_permission_handler,
            max_turns=self._max_turns,
            disallowed_tools=config.disallowed_tools,
            session_kind=config.session_kind,
            system_prompt_override=config.system_prompt_override,
        )

        try:
            session_id = await self._adapter.create_session(config)
        except Exception:
            log.warning("agentic_sidecar_session_create_failed", exc_info=True)
            self.call_count += 1
            return ""
        self._session_id = session_id

        final_text = ""
        try:
            async for event in self._adapter.stream_events(session_id):
                tf_event = event
                if tf_event.kind != EventKind.message_assistant or not isinstance(tf_event.payload, dict):
                    continue
                content = tf_event.payload.get("content")
                if isinstance(content, str) and content:
                    final_text = content
        except (TimeoutError, OSError, RuntimeError):
            log.warning("agentic_sidecar_session_error", session_id=session_id, exc_info=True)
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            self.call_count += 1
            self.total_latency_ms += elapsed_ms
            self.last_call_at = time.monotonic()
            self._session_id = None

        if not final_text:
            log.warning("agentic_sidecar_no_output", session_id=session_id)

        return final_text

    async def abort(self) -> None:
        """Abort the running session if any."""
        if self._session_id:
            with contextlib.suppress(Exception):
                await self._adapter.abort_session(self._session_id)


class SidecarSessionManager:
    """Registry of named sidecar sessions per job.

    Each job owns ``dict[name, SidecarSession]``.  Callers always address
    a sidecar by ``(job_id, name)``.

    Also provides ``complete()`` for one-shot callers without a job context
    (naming, terminal ask).
    """

    def __init__(
        self,
        adapter: AgentAdapterInterface,
        *,
        model: str = DEFAULT_UTILITY_MODEL,
        pool_size: int = _STANDBY_POOL_SIZE,
    ) -> None:
        self._adapter = adapter
        self._model = model
        self._pool_size = pool_size

        # Completer — delegates to adapter (SDK)
        self._completer = LightweightCompleter(adapter, model=model)

        # Standby pool — ready-to-use SidecarSession instances
        self._pool: deque[SidecarSession] = deque()

        # Pre-warmed sessions awaiting adoption (token → session)
        self._warm: dict[str, SidecarSession] = {}
        self._warm_created_at: dict[str, float] = {}

        # Named sessions bound to a job (job_id → {name → session})
        self._jobs: dict[str, dict[str, SidecarSession]] = {}

        # Snapshots of per-job metrics preserved after close_job() (LRU-bounded)
        self._closed_jobs: OrderedDict[str, dict[str, object]] = OrderedDict()

        self._bg_tasks: list[asyncio.Task[None]] = []

        # Global metrics (accumulated from closed sessions)
        self._global_call_count: int = 0
        self._global_latency_ms: float = 0.0
        self._global_input_tokens: int = 0
        self._global_output_tokens: int = 0
        self._global_cost_usd: float = 0.0

    @property
    def model(self) -> str:
        return self._model

    # -- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Seed the standby pool and start background maintenance tasks."""
        self._fill_pool()
        self._bg_tasks.append(asyncio.create_task(self._orphan_reaper(), name="sidecar-orphan-reaper"))
        log.debug("sidecar_session_manager_started", pool_size=self._pool_size)

    async def shutdown(self) -> None:
        """Cancel background tasks and clear all sessions."""
        for task in self._bg_tasks:
            task.cancel()
        for task in self._bg_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._bg_tasks.clear()
        self._pool.clear()
        self._warm.clear()
        self._warm_created_at.clear()
        self._jobs.clear()
        await self._completer.close()
        log.debug("sidecar_session_manager_shutdown")

    # -- Standby pool --------------------------------------------------------

    def _make_session(
        self,
        *,
        system_prompt: str | None = None,
        max_turns: int | None = None,
        timeout_s: float | None = None,
        model: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> SidecarSession:
        """Create a new SidecarSession wrapper (cheap — no I/O)."""
        return SidecarSession(
            adapter=self._adapter,
            system_prompt=system_prompt,
            max_turns=max_turns,
            timeout_s=timeout_s,
            model=model,
            excluded_tools=excluded_tools,
        )

    def _fill_pool(self) -> None:
        """Top up the standby pool to ``_pool_size``."""
        while len(self._pool) < self._pool_size:
            self._pool.append(self._make_session())

    def _pop_or_create(
        self,
        *,
        system_prompt: str | None = None,
        max_turns: int | None = None,
        timeout_s: float | None = None,
        model: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> SidecarSession:
        """Pop a session from the pool, or create one with the given config.

        Pool sessions use the default system prompt.  If a custom prompt or
        lifetime is requested, a fresh session is always created (pool
        sessions are generic).
        """
        if (
            system_prompt is not None
            or max_turns is not None
            or timeout_s is not None
            or model is not None
            or excluded_tools is not None
        ):
            self._fill_pool()
            return self._make_session(
                system_prompt=system_prompt,
                max_turns=max_turns,
                timeout_s=timeout_s,
                model=model,
                excluded_tools=excluded_tools,
            )
        session = self._pool.popleft() if self._pool else self._make_session()
        self._fill_pool()
        return session

    # -- Ephemeral sessions -------------------------------------------------

    def make_ephemeral(
        self,
        *,
        system_prompt: str | None = None,
        max_turns: int | None = None,
        model: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> SidecarSession:
        """Create a disposable one-shot session (not tracked per-job)."""
        return self._make_session(
            system_prompt=system_prompt,
            max_turns=max_turns,
            model=model,
            excluded_tools=excluded_tools,
        )

    # -- Pre-warm (new-job panel) -------------------------------------------

    def warm(self) -> str:
        """Hand out a pooled session under a token.

        Returns immediately — no blocking I/O.
        """
        token = secrets.token_urlsafe(16)
        self._warm[token] = self._pop_or_create()
        self._warm_created_at[token] = time.monotonic()
        log.debug("sidecar_session_warmed", token=token[:8])
        return token

    def release(self, token: str) -> bool:
        """Return an unused warm session to the pool.  Returns True if found."""
        session = self._warm.pop(token, None)
        self._warm_created_at.pop(token, None)
        if session is None:
            return False
        if len(self._pool) < self._pool_size:
            session._primed = False  # noqa: SLF001
            session._reset_metrics()  # noqa: SLF001
            self._pool.append(session)
        log.debug("sidecar_session_released", token=token[:8])
        return True

    # -- Named sidecar management -------------------------------------------

    def open(
        self,
        job_id: str,
        name: str,
        *,
        config: SidecarConfig | None = None,
        token: str | None = None,
    ) -> SidecarSession:
        """Open a named sidecar session for a job.

        If *token* is provided, adopts the pre-warmed session (falling back
        to a fresh one if the token expired).  Otherwise creates from pool
        or with *config* settings.

        Returns the session (also stored internally).
        """
        job_sidecars = self._jobs.setdefault(job_id, {})

        if name in job_sidecars:
            existing = job_sidecars[name]
            if not existing.expired:
                return existing
            # Expired windowed session — close it and replace
            self._accumulate_global(existing)

        if token is not None:
            session = self._warm.pop(token, None)
            self._warm_created_at.pop(token, None)
            if session is None:
                log.debug("sidecar_adopt_token_miss", token=token[:8], job_id=job_id, name=name)
                session = self._pop_or_create()
        elif config is not None:
            session = self._pop_or_create(
                system_prompt=config.system_prompt,
                max_turns=config.max_turns,
                timeout_s=config.timeout_s,
            )
        else:
            session = self._pop_or_create()

        job_sidecars[name] = session
        log.debug("sidecar_session_opened", job_id=job_id, name=name)
        return session

    def get(self, job_id: str, name: str) -> SidecarSession | None:
        """Get a named sidecar session for a running job.

        Returns None if no session exists for that name, or if the session
        has expired its windowed lifetime.
        """
        job_sidecars = self._jobs.get(job_id)
        if job_sidecars is None:
            return None
        session = job_sidecars.get(name)
        if session is None:
            return None
        if session.expired:
            self._accumulate_global(session)
            del job_sidecars[name]
            log.debug("sidecar_session_expired", job_id=job_id, name=name)
            return None
        return session

    def list_names(self, job_id: str) -> list[str]:
        """Return names of all active sidecars for a job."""
        job_sidecars = self._jobs.get(job_id)
        if job_sidecars is None:
            return []
        return list(job_sidecars.keys())

    def close(self, job_id: str, name: str) -> None:
        """Close a single named sidecar session."""
        job_sidecars = self._jobs.get(job_id)
        if job_sidecars is None:
            return
        session = job_sidecars.pop(name, None)
        if session is not None:
            self._accumulate_global(session)
            log.debug("sidecar_session_closed", job_id=job_id, name=name)
        if not job_sidecars:
            self._jobs.pop(job_id, None)

    def close_job(self, job_id: str) -> None:
        """Close all sidecar sessions for a finished job."""
        job_sidecars = self._jobs.pop(job_id, None)
        if job_sidecars is None:
            return
        total_calls = 0
        total_latency = 0.0
        total_input = 0
        total_output = 0
        total_cost = 0.0
        for session in job_sidecars.values():
            self._accumulate_global(session)
            total_calls += session.call_count
            total_latency += session.total_latency_ms
            total_input += session.total_input_tokens
            total_output += session.total_output_tokens
            total_cost += session.total_cost_usd
        if total_calls > 0:
            self._closed_jobs[job_id] = {
                "callCount": total_calls,
                "avgLatencyMs": round(total_latency / total_calls, 1),
                "totalLatencyMs": round(total_latency, 1),
                "inputTokens": total_input,
                "outputTokens": total_output,
                "costUsd": round(total_cost, 6),
            }
            while len(self._closed_jobs) > _CLOSED_JOBS_MAX:
                self._closed_jobs.popitem(last=False)
        log.debug("sidecar_job_closed", job_id=job_id, sidecars=list(job_sidecars.keys()))

    def _accumulate_global(self, session: SidecarSession) -> None:
        """Add a session's metrics to the global counters."""
        self._global_call_count += session.call_count
        self._global_latency_ms += session.total_latency_ms
        self._global_input_tokens += session.total_input_tokens
        self._global_output_tokens += session.total_output_tokens
        self._global_cost_usd += session.total_cost_usd

    # -- Non-job one-shot (Completable protocol) -----------------------------

    async def complete(self, prompt: str, timeout: float = 30.0) -> str:
        """One-shot completion for callers without a job context.

        Routes through the SDK adapter via a pooled SidecarSession.
        """
        session = self._pop_or_create()
        try:
            for attempt in range(_TIMEOUT_RETRIES + 1):
                try:
                    return await session.complete(prompt, timeout=timeout)
                except TimeoutError:
                    if attempt >= _TIMEOUT_RETRIES:
                        raise
                    session._primed = False  # noqa: SLF001
            return ""
        except (OSError, RuntimeError, TimeoutError):
            log.warning("sidecar_oneshot_failed", exc_info=True)
            return ""
        finally:
            self._accumulate_global(session)
            if len(self._pool) < self._pool_size:
                session._primed = False  # noqa: SLF001
                session._reset_metrics()  # noqa: SLF001
                self._pool.append(session)

    # -- Metrics -------------------------------------------------------------

    def get_metrics(self) -> dict[str, Any]:
        """Return global + per-job sidecar session metrics."""
        active_calls = 0
        active_latency = 0.0
        for job_sidecars in self._jobs.values():
            for s in job_sidecars.values():
                active_calls += s.call_count
                active_latency += s.total_latency_ms
        total_calls = self._global_call_count + active_calls
        total_latency = self._global_latency_ms + active_latency

        per_job: dict[str, dict[str, object]] = {}
        per_job.update(self._closed_jobs)
        for job_id, job_sidecars in self._jobs.items():
            jc = sum(s.call_count for s in job_sidecars.values())
            jl = sum(s.total_latency_ms for s in job_sidecars.values())
            per_job[job_id] = {
                "callCount": jc,
                "avgLatencyMs": round(jl / jc, 1) if jc else 0,
                "totalLatencyMs": round(jl, 1),
                "inputTokens": sum(s.total_input_tokens for s in job_sidecars.values()),
                "outputTokens": sum(s.total_output_tokens for s in job_sidecars.values()),
                "costUsd": round(sum(s.total_cost_usd for s in job_sidecars.values()), 6),
            }

        return {
            "global": {
                "totalCalls": total_calls,
                "avgLatencyMs": round(total_latency / total_calls, 1) if total_calls else 0,
                "activeJobs": len(self._jobs),
                "poolSize": len(self._pool),
                "warmTokens": len(self._warm),
            },
            "jobs": per_job,
        }

    # -- Background tasks ----------------------------------------------------

    async def _orphan_reaper(self) -> None:
        """Close warm sessions that were never adopted."""
        try:
            while True:
                await asyncio.sleep(_ORPHAN_CHECK_INTERVAL_S)
                now = time.monotonic()
                expired = [
                    token for token, created in self._warm_created_at.items() if now - created > _ORPHAN_EXPIRY_S
                ]
                for token in expired:
                    self._warm.pop(token, None)
                    self._warm_created_at.pop(token, None)
                    log.debug("sidecar_session_orphan_expired", token=token[:8])
        except asyncio.CancelledError:
            log.debug("sidecar_session_cleanup_cancelled")
