"""Utility session pool — warm Copilot SDK sessions with a cheap/fast model.

Keeps a pool of sessions alive on server start for non-agentic meta-work:
naming, summarization, progress headlines, commit messages, etc.

Design invariants
-----------------
* Every concurrent ``complete()`` call gets its **own** session — sessions are
  never shared between callers.  This is tracked via ``_WarmSession.in_use``.
* The pool autoscales without a hard ceiling: it grows to match concurrent
  demand and shrinks back to the minimum via idle housekeeping.
* Scale-up is serialized through ``_scale_lock`` so a burst of concurrent
  callers triggers exactly ONE batch of session connects, not N thundering herds.
* ``start()`` initialises the pool in parallel so startup time is bounded by
  a single connection round-trip, not pool_size × connection time.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from copilot.generated.session_events import SessionEvent as SdkSessionEvent
    from copilot.session import CopilotSession

log = structlog.get_logger()

# Default model for utility work — cheap and fast
DEFAULT_UTILITY_MODEL = "gpt-4o-mini"

# Autoscaling constants
_MIN_POOL = 1
_SCALE_DOWN_IDLE_S = 120.0  # close a session idle longer than this
_HOUSEKEEPING_INTERVAL_S = 30
# Maximum time to wait for a free session before creating a fresh emergency one
_CHECKOUT_SPIN_S = 3.0
_CHECKOUT_SLEEP_S = 0.05
_UTILITY_TIMEOUT_RETRIES = 1
_UTILITY_TIMEOUT_BACKOFF_MULTIPLIER = 1.5
_UTILITY_TIMEOUT_BACKOFF_MIN_S = 15.0

# System prompt injected at session creation so the model understands its role
_UTILITY_SYSTEM_PROMPT = """\
You are a concise utility assistant embedded in a coding task management system
called CodePlane. Your sole purpose is to generate short metadata: titles, branch
names, progress summaries, commit messages, and PR descriptions.

Rules:
- Always respond with ONLY the requested format (usually JSON).
- Never add commentary, greetings, or markdown fencing unless the caller asks.
- Be extremely concise — every token costs time.
- You do NOT execute code or use tools. You only produce text.
"""


class UtilitySessionService:
    """Pool of warm Copilot SDK sessions for cheap meta-work.

    Usage::

        utility = UtilitySessionService(model="gpt-4o-mini")
        await utility.start()          # call once at server startup
        result = await utility.complete("Generate a title for: ...")
        await utility.shutdown()       # call at server shutdown

    Each concurrent ``complete()`` call gets its own session.  The pool grows
    to match demand (no hard ceiling) and shrinks via idle housekeeping.

    Thread-safety model (asyncio single-threaded):
    * ``_lock`` guards ``_sessions`` list mutations and ``in_use`` checks.
    * ``_scale_lock`` serialises scale-up so a burst of N concurrent callers
      triggers exactly one batch connect, not N parallel ones.
    """

    def __init__(
        self,
        model: str = DEFAULT_UTILITY_MODEL,
        pool_size: int = 2,
    ) -> None:
        self._model = model
        self._initial_pool_size = pool_size
        self._sessions: list[_WarmSession] = []
        self._lock = asyncio.Lock()
        self._scale_lock = asyncio.Lock()  # serialise concurrent scale-ups
        self._started = False
        self._pending: int = 0
        self._active_jobs: int = 0
        self._housekeeping_task: asyncio.Task[None] | None = None

    @property
    def model(self) -> str:
        return self._model

    async def start(self) -> None:
        """Create the warm session pool in parallel. Safe to call multiple times."""
        if self._started:
            return
        candidates = [_WarmSession(model=self._model, index=i) for i in range(self._initial_pool_size)]
        results = await asyncio.gather(*[ws.connect() for ws in candidates], return_exceptions=True)
        for ws, result in zip(candidates, results, strict=False):
            if isinstance(result, Exception):
                log.warning("utility_session_start_failed", index=ws.index, exc_info=result)
            else:
                self._sessions.append(ws)
                log.info("utility_session_ready", index=ws.index, model=self._model)
        self._started = True
        if not self._sessions:
            log.warning("utility_session_pool_empty", model=self._model)
        self._housekeeping_task = asyncio.create_task(self._housekeeping_loop(), name="utility-session-housekeeping")

    async def complete(self, prompt: str, timeout: float = 30.0) -> str:
        """Send a prompt to a free session and return the response.

        Guarantees each caller gets its own session — no sharing.
        Scales the pool up if all sessions are busy.
        """
        self._pending += 1
        try:
            ws = await self._checkout_session()
            try:
                attempt_timeout = timeout
                for attempt in range(_UTILITY_TIMEOUT_RETRIES + 1):
                    try:
                        return await ws.complete(prompt, timeout=attempt_timeout)
                    except TimeoutError:
                        log.warning(
                            "utility_session_complete_timeout",
                            index=ws.index,
                            attempt=attempt + 1,
                            timeout_s=attempt_timeout,
                        )
                        if attempt >= _UTILITY_TIMEOUT_RETRIES:
                            raise
                        await ws.reconnect()
                        attempt_timeout = max(
                            attempt_timeout * _UTILITY_TIMEOUT_BACKOFF_MULTIPLIER,
                            attempt_timeout + _UTILITY_TIMEOUT_BACKOFF_MIN_S,
                        )
            except Exception:
                log.warning("utility_session_complete_failed", index=ws.index, exc_info=True)
                try:
                    await ws.reconnect()
                except Exception:
                    log.warning("utility_session_reconnect_failed", index=ws.index, exc_info=True)
                return ""
            finally:
                async with self._lock:
                    ws.in_use = False
        finally:
            self._pending -= 1
        return ""

    async def shutdown(self) -> None:
        """Abort all warm sessions."""
        if self._housekeeping_task is not None:
            self._housekeeping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._housekeeping_task
            self._housekeeping_task = None
        for ws in self._sessions:
            await ws.close()
        self._sessions.clear()
        self._started = False
        log.debug("utility_session_pool_shutdown")

    # ------------------------------------------------------------------
    # Session checkout — guaranteed exclusive access per caller
    # ------------------------------------------------------------------

    async def _checkout_session(self) -> _WarmSession:
        """Return an exclusive free session, scaling up if all are busy.

        1. Fast path: find a free session in the pool.
        2. Scale path: scale the pool up and retry.
        3. Spin path: busy-wait up to _CHECKOUT_SPIN_S for a session to free.
        4. Emergency path: create a fresh session unconditionally.
        """
        # Fast path
        async with self._lock:
            ws = self._find_free()
            if ws is not None:
                ws.in_use = True
                log.debug("utility_pool_checkout_fast", index=ws.index, pending=self._pending)
                return ws

        # All busy (or empty) — scale up then retry
        await self._scale_up_to_demand()

        async with self._lock:
            ws = self._find_free()
            if ws is not None:
                ws.in_use = True
                log.debug("utility_pool_checkout_post_scale", index=ws.index, pool_size=len(self._sessions))
                return ws

        # Scale-up didn't help (e.g. all connections failed).  Spin briefly.
        deadline = time.monotonic() + _CHECKOUT_SPIN_S
        while time.monotonic() < deadline:
            await asyncio.sleep(_CHECKOUT_SLEEP_S)
            async with self._lock:
                ws = self._find_free()
                if ws is not None:
                    ws.in_use = True
                    log.debug("utility_pool_checkout_spin", index=ws.index)
                    return ws

        # Emergency: create a brand-new session outside the normal pool.
        log.warning("utility_pool_checkout_emergency", pending=self._pending, pool_size=len(self._sessions))
        ws = _WarmSession(model=self._model, index=-1)
        await ws.connect()
        async with self._lock:
            ws.index = len(self._sessions)
            self._sessions.append(ws)
        ws.in_use = True
        return ws

    def _find_free(self) -> _WarmSession | None:
        """Return the first free session, or None. Must be called under _lock."""
        for ws in self._sessions:
            if not ws.in_use:
                return ws
        return None

    # ------------------------------------------------------------------
    # Job-aware proactive scaling
    # ------------------------------------------------------------------

    async def notify_job_started(self) -> None:
        """Signal that a new job is running — proactively scale the pool."""
        self._active_jobs += 1
        await self._scale_up_to_demand()

    async def notify_job_ended(self) -> None:
        """Signal that a job finished — pool will shrink via idle housekeeping."""
        self._active_jobs = max(0, self._active_jobs - 1)

    # ------------------------------------------------------------------
    # Autoscaling internals
    # ------------------------------------------------------------------

    async def _scale_up_to_demand(self) -> None:
        """Grow the pool to match current demand, serialised by _scale_lock.

        Using _scale_lock means a burst of N concurrent callers triggers
        exactly ONE batch of session connects rather than N thundering herds.
        Callers that lose the race simply wait for the winner to finish, then
        find sessions already available.
        """
        target = max(self._pending, self._active_jobs, _MIN_POOL)
        if len(self._sessions) >= target:
            return  # fast path — avoid lock acquisition

        async with self._scale_lock:
            # Re-check under the scale lock; a previous winner may have already
            # added enough sessions while we were waiting.
            target = max(self._pending, self._active_jobs, _MIN_POOL)
            need = target - len(self._sessions)
            if need <= 0:
                return

            new_index_base = len(self._sessions)
            candidates = [_WarmSession(model=self._model, index=new_index_base + i) for i in range(need)]

            results = await asyncio.gather(*[ws.connect() for ws in candidates], return_exceptions=True)

            connected: list[_WarmSession] = []
            for ws, result in zip(candidates, results, strict=False):
                if isinstance(result, Exception):
                    log.warning("utility_pool_scale_up_failed", index=ws.index, exc_info=result)
                else:
                    connected.append(ws)

            if not connected:
                log.error("utility_pool_scale_up_all_failed", need=need)
                return

            async with self._lock:
                for ws in connected:
                    ws.index = len(self._sessions)
                    self._sessions.append(ws)
                log.info(
                    "utility_pool_scaled_up",
                    new_size=len(self._sessions),
                    target=target,
                    active_jobs=self._active_jobs,
                    pending=self._pending,
                )

    async def _housekeeping_loop(self) -> None:
        """Periodically close idle sessions beyond the minimum pool size."""
        try:
            while True:
                await asyncio.sleep(_HOUSEKEEPING_INTERVAL_S)
                await self._scale_down_idle()
        except asyncio.CancelledError:
            pass

    async def _scale_down_idle(self) -> None:
        """Close idle, non-busy sessions beyond the minimum pool size."""
        now = time.monotonic()
        to_close: list[_WarmSession] = []
        floor = max(_MIN_POOL, self._active_jobs)
        async with self._lock:
            if len(self._sessions) <= floor:
                return
            survivors: list[_WarmSession] = []
            for ws in self._sessions:
                if (
                    not ws.in_use
                    and ws.index != 0
                    and (now - ws.last_used_at) > _SCALE_DOWN_IDLE_S
                    and (len(survivors) + len(self._sessions) - len(to_close) - 1) >= floor
                ):
                    to_close.append(ws)
                else:
                    survivors.append(ws)
            if to_close:
                self._sessions = survivors
                log.debug("utility_pool_scaled_down", closed=len(to_close), new_size=len(self._sessions))
        for ws in to_close:
            await ws.close()


class _WarmSession:
    """A single long-lived Copilot SDK session for utility completions.

    The pool guarantees each session is used by at most one caller at a time
    via the ``in_use`` flag.  The internal ``_lock`` provides an additional
    safety net (e.g. during reconnects initiated by the pool's error handler).
    """

    def __init__(self, model: str, index: int) -> None:
        self.model = model
        self.index = index
        self._session: CopilotSession | None = None
        self._lock = asyncio.Lock()
        self.last_used_at: float = time.monotonic()
        self.in_use: bool = False  # controlled exclusively by UtilitySessionService

    async def connect(self) -> None:
        """Create a fresh Copilot session with the utility model."""
        from copilot import CopilotClient, PermissionRequestResult
        from copilot.types import SessionConfig

        client = CopilotClient()

        async def _noop_permission(request: object, invocation: dict[str, str]) -> PermissionRequestResult:
            return PermissionRequestResult(kind="approved")

        import tempfile

        opts = SessionConfig(
            working_directory=tempfile.gettempdir(),
            on_permission_request=_noop_permission,
            model=self.model,
        )

        session = await client.create_session(opts)
        self._session = session

        # Prime the session with the system prompt so the model context is warm.
        # Keep the timeout short — a prime that times out is not fatal; the session
        # still works, the first real request will just carry a bit more context.
        done = asyncio.Event()

        def _on_event(sdk_event: SdkSessionEvent) -> None:
            kind_str = sdk_event.type.value if sdk_event.type else ""
            if kind_str in (
                "assistant.message",
                "session.task_complete",
                "session.idle",
                "session.error",
                "session.shutdown",
            ):
                done.set()

        session.on(_on_event)
        await session.send(
            {
                "prompt": _UTILITY_SYSTEM_PROMPT,
                "mode": "immediate",
                "attachments": [],
            }
        )
        try:
            await asyncio.wait_for(done.wait(), timeout=5)
        except TimeoutError:
            log.debug("utility_session_prime_timeout", index=self.index)

    async def complete(self, prompt: str, timeout: float = 30.0) -> str:
        """Send a prompt and collect the response."""
        async with self._lock:
            self.last_used_at = time.monotonic()
            if self._session is None:
                await self.connect()
            if self._session is None:
                return ""

            delta_chunks: list[str] = []
            final_message: str | None = None
            activity = asyncio.Event()
            finished = False
            last_progress_at = time.monotonic()

            def _on_event(sdk_event: SdkSessionEvent) -> None:
                nonlocal final_message, finished, last_progress_at
                kind_str = sdk_event.type.value if sdk_event.type else ""
                payload = sdk_event.data.to_dict() if sdk_event.data else {}
                if kind_str == "assistant.streaming_delta":
                    content = payload.get("delta_content") or payload.get("content") or ""
                    if content:
                        delta_chunks.append(content)
                        last_progress_at = time.monotonic()
                        activity.set()
                elif kind_str == "assistant.message":
                    content = payload.get("content") or ""
                    if content:
                        final_message = content
                        last_progress_at = time.monotonic()
                    finished = True
                    activity.set()
                elif kind_str in (
                    "session.task_complete",
                    "session.idle",
                    "session.error",
                    "session.shutdown",
                ):
                    finished = True
                    activity.set()

            self._session.on(_on_event)
            await self._session.send(
                {
                    "prompt": prompt,
                    "mode": "immediate",
                    "attachments": [],
                }
            )

            try:
                overall_deadline = time.monotonic() + max(timeout * 2.0, timeout + _UTILITY_TIMEOUT_BACKOFF_MIN_S)
                while not finished:
                    now = time.monotonic()
                    idle_remaining = (last_progress_at + timeout) - now
                    overall_remaining = overall_deadline - now
                    wait_timeout = min(idle_remaining, overall_remaining)
                    if wait_timeout <= 0:
                        raise TimeoutError

                    activity.clear()
                    await asyncio.wait_for(activity.wait(), timeout=wait_timeout)
            except TimeoutError:
                log.warning(
                    "utility_complete_timeout",
                    index=self.index,
                    timeout_s=timeout,
                    streamed_chars=sum(len(chunk) for chunk in delta_chunks),
                    had_final_message=bool(final_message),
                )
                # Session is in an unknown state after a timeout — kill it so
                # the pool-level caller triggers a reconnect instead of reusing
                # a dead session on every subsequent call (death spiral).
                await self.close()
                raise

            if final_message is not None:
                return final_message
            return "".join(delta_chunks)

    async def reconnect(self) -> None:
        """Close and re-create the session."""
        await self.close()
        await self.connect()

    async def close(self) -> None:
        """Abort the session if open."""
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.abort()
            self._session = None
