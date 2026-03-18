"""Utility session pool — warm Copilot SDK sessions with a cheap/fast model.

Keeps one or two sessions alive on server start for non-agentic meta-work:
naming, summarization, progress headlines, commit messages, etc.

Sessions are created once at startup and reused across all jobs. If a session
dies it is transparently re-created on the next call.

Pool size autoscales between MIN_POOL (1) and max_pool_fn() based on observed
queue depth: when there are more pending calls than available sessions, a new
session is spawned (up to the ceiling). Sessions idle longer than
SCALE_DOWN_IDLE_S are closed during periodic housekeeping.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    from copilot.generated.session_events import SessionEvent as SdkSessionEvent
    from copilot.session import CopilotSession

log = structlog.get_logger()

# Default model for utility work — cheap and fast
DEFAULT_UTILITY_MODEL = "gpt-4o-mini"

# Autoscaling constants
_MIN_POOL = 1
_SCALE_DOWN_IDLE_S = 120.0  # close a session idle longer than this
_HOUSEKEEPING_INTERVAL_S = 30

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

    The pool autoscales between 1 and max_pool_fn() sessions based on
    observed queue depth. Pass a callable so it stays in sync with live
    config changes without a server restart.
    """

    def __init__(
        self,
        model: str = DEFAULT_UTILITY_MODEL,
        pool_size: int = 1,
        max_pool_fn: Callable[[], int] | None = None,
    ) -> None:
        self._model = model
        self._initial_pool_size = pool_size
        self._max_pool_fn: Callable[[], int] = max_pool_fn or (lambda: self._initial_pool_size)
        self._sessions: list[_WarmSession] = []
        self._lock = asyncio.Lock()
        self._round_robin = 0
        self._started = False
        self._pending: int = 0
        self._active_jobs: int = 0
        self._housekeeping_task: asyncio.Task[None] | None = None

    @property
    def model(self) -> str:
        return self._model

    async def start(self) -> None:
        """Create the warm session pool. Safe to call multiple times."""
        if self._started:
            return
        for i in range(self._initial_pool_size):
            ws = _WarmSession(model=self._model, index=i)
            try:
                await ws.connect()
                self._sessions.append(ws)
                log.debug("utility_session_ready", index=i, model=self._model)
            except Exception:
                log.warning("utility_session_start_failed", index=i, exc_info=True)
        self._started = True
        if not self._sessions:
            log.warning("utility_session_pool_empty", model=self._model)
        self._housekeeping_task = asyncio.create_task(self._housekeeping_loop(), name="utility-session-housekeeping")

    async def complete(self, prompt: str, timeout: float = 30.0) -> str:
        """Send a prompt to a warm session and return the response.

        Automatically reconnects if the session is dead. Falls back to
        creating a one-off session if the pool is exhausted.
        """
        self._pending += 1
        try:
            if not self._sessions:
                ws = _WarmSession(model=self._model, index=0)
                try:
                    await ws.connect()
                    self._sessions.append(ws)
                except Exception:
                    log.error("utility_session_cold_start_failed", exc_info=True)
                    return ""

            await self._maybe_scale_up()

            async with self._lock:
                idx = self._round_robin % len(self._sessions)
                self._round_robin += 1
                ws = self._sessions[idx]

            log.debug("utility_pool_queue_depth", pending=self._pending, pool_size=len(self._sessions))

            try:
                return await ws.complete(prompt, timeout=timeout)
            except Exception:
                log.warning("utility_session_complete_failed", index=ws.index, exc_info=True)
                try:
                    await ws.reconnect()
                except Exception:
                    log.warning("utility_session_reconnect_failed", index=ws.index, exc_info=True)
                return ""
        finally:
            self._pending -= 1

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
    # Job-aware proactive scaling
    # ------------------------------------------------------------------

    async def notify_job_started(self) -> None:
        """Signal that a new job is running — proactively scale the pool."""
        self._active_jobs += 1
        await self._scale_to_target()

    async def notify_job_ended(self) -> None:
        """Signal that a job finished — pool will shrink via idle housekeeping."""
        self._active_jobs = max(0, self._active_jobs - 1)

    async def _scale_to_target(self) -> None:
        """Scale pool up to match active job count (capped at max_pool)."""
        target = min(max(_MIN_POOL, self._active_jobs), max(_MIN_POOL, self._max_pool_fn()))
        if len(self._sessions) >= target:
            return
        async with self._lock:
            while len(self._sessions) < target:
                new_index = len(self._sessions)
                ws = _WarmSession(model=self._model, index=new_index)
                try:
                    await ws.connect()
                    self._sessions.append(ws)
                    log.debug(
                        "utility_pool_scaled_up",
                        new_size=len(self._sessions),
                        target=target,
                        active_jobs=self._active_jobs,
                    )
                except Exception:
                    log.warning("utility_pool_scale_up_failed", index=new_index, exc_info=True)
                    break

    # ------------------------------------------------------------------
    # Autoscaling internals
    # ------------------------------------------------------------------

    async def _maybe_scale_up(self) -> None:
        """Spawn a new session if the queue is deeper than current capacity."""
        max_pool = max(_MIN_POOL, self._max_pool_fn())
        target = max(self._pending, self._active_jobs)
        if target > len(self._sessions) and len(self._sessions) < max_pool:
            async with self._lock:
                if target > len(self._sessions) and len(self._sessions) < max_pool:
                    new_index = len(self._sessions)
                    ws = _WarmSession(model=self._model, index=new_index)
                    try:
                        await ws.connect()
                        self._sessions.append(ws)
                        log.debug(
                            "utility_pool_scaled_up",
                            new_size=len(self._sessions),
                            pending=self._pending,
                            active_jobs=self._active_jobs,
                            max_pool=max_pool,
                        )
                    except Exception:
                        log.warning("utility_pool_scale_up_failed", index=new_index, exc_info=True)

    async def _housekeeping_loop(self) -> None:
        """Periodically close idle sessions beyond the minimum pool size."""
        try:
            while True:
                await asyncio.sleep(_HOUSEKEEPING_INTERVAL_S)
                await self._scale_down_idle()
        except asyncio.CancelledError:
            pass

    async def _scale_down_idle(self) -> None:
        """Close sessions that have been idle longer than SCALE_DOWN_IDLE_S."""
        now = time.monotonic()
        to_close: list[_WarmSession] = []
        floor = max(_MIN_POOL, self._active_jobs)
        async with self._lock:
            if len(self._sessions) <= floor:
                return
            survivors: list[_WarmSession] = []
            for ws in self._sessions:
                if (
                    ws.index != 0
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

    Uses a mutex to serialize requests — each session handles one prompt at a
    time. With pool_size=1 this means utility calls are sequential, which is
    fine for the low-volume meta-work they handle.
    """

    def __init__(self, model: str, index: int) -> None:
        self.model = model
        self.index = index
        self._session: CopilotSession | None = None
        self._lock = asyncio.Lock()
        self.last_used_at: float = time.monotonic()

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

        # Prime the session with the system prompt so subsequent calls are faster
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
            await asyncio.wait_for(done.wait(), timeout=15)
        except TimeoutError:
            log.warning("utility_session_prime_timeout", index=self.index)

    async def complete(self, prompt: str, timeout: float = 30.0) -> str:
        """Send a prompt and collect the response. Thread-safe via mutex."""
        async with self._lock:
            self.last_used_at = time.monotonic()
            if self._session is None:
                await self.connect()
            if self._session is None:
                return ""

            collected: list[str] = []
            done = asyncio.Event()

            def _on_event(sdk_event: SdkSessionEvent) -> None:
                kind_str = sdk_event.type.value if sdk_event.type else ""
                payload = sdk_event.data.to_dict() if sdk_event.data else {}
                if kind_str == "assistant.message":
                    content = payload.get("content") or ""
                    if content:
                        collected.append(content)
                    done.set()
                elif kind_str in (
                    "session.task_complete",
                    "session.idle",
                    "session.error",
                    "session.shutdown",
                ):
                    done.set()

            self._session.on(_on_event)
            await self._session.send(
                {
                    "prompt": prompt,
                    "mode": "immediate",
                    "attachments": [],
                }
            )

            try:
                await asyncio.wait_for(done.wait(), timeout=timeout)
            except TimeoutError:
                log.warning("utility_complete_timeout", index=self.index)

            return "\n".join(collected)

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
