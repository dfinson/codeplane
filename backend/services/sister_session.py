"""Per-job dedicated utility sessions ("sister sessions").

Every running job gets exactly one dedicated cheap-model session that lives
for the lifetime of that job.  No pool, no checkout contention.

Lifecycle:
1. **Pre-warm** — frontend opens the new-job panel → ``warm()`` creates a
   session and returns a token.  If the user navigates away, ``release()``
   tears it down.  Orphaned sessions auto-expire after ``_ORPHAN_EXPIRY_S``.
2. **Adopt** — ``POST /api/jobs`` passes the token → ``adopt(token, job_id)``
   binds the warm session to the job.
3. **Create-for-job** — resume path (no pre-warm) → ``create_for_job(job_id)``.
4. **Use** — ``get(job_id)`` returns the session for direct calls (no
   checkout, no contention — it belongs to this job).
5. **Close** — job reaches terminal state → ``close_job(job_id)`` tears down
   the session.

Also provides a global ``complete()`` for non-job callers (terminal ask,
MCP naming) that creates a one-shot session, uses it, and discards it.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from copilot.generated.session_events import SessionEvent as SdkSessionEvent
    from copilot.session import CopilotSession

log = structlog.get_logger()

# Default model for utility work — cheap and fast
DEFAULT_UTILITY_MODEL = "gpt-4o-mini"

# Orphan expiry — warm sessions not adopted within this window are closed
_ORPHAN_EXPIRY_S = 300.0  # 5 minutes
_ORPHAN_CHECK_INTERVAL_S = 30.0

# Retry / backoff for one-shot callers
_TIMEOUT_RETRIES = 1
_TIMEOUT_BACKOFF_MULTIPLIER = 1.5
_TIMEOUT_BACKOFF_MIN_S = 15.0

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


class SisterSession:
    """A single long-lived Copilot SDK session for utility completions.

    Each job owns exactly one of these.  Thread-safety is provided by
    the internal ``_lock`` (asyncio single-threaded model).
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self._session: CopilotSession | None = None
        self._lock = asyncio.Lock()
        self.created_at: float = time.monotonic()

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
            log.debug("sister_session_prime_timeout")

    async def complete(self, prompt: str, timeout: float = 30.0) -> str:
        """Send a prompt and collect the response."""
        async with self._lock:
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
                overall_deadline = time.monotonic() + max(timeout * 2.0, timeout + _TIMEOUT_BACKOFF_MIN_S)
                while not finished:
                    now = time.monotonic()
                    idle_remaining = (last_progress_at + timeout) - now
                    overall_remaining = overall_deadline - now

                    wait_time = min(max(idle_remaining, 0.1), max(overall_remaining, 0.1))
                    if wait_time <= 0:
                        raise TimeoutError("utility completion timed out")

                    activity.clear()
                    try:
                        await asyncio.wait_for(activity.wait(), timeout=wait_time)
                    except TimeoutError:
                        if time.monotonic() - last_progress_at > timeout:
                            raise TimeoutError("utility completion idle timeout") from None
                        if time.monotonic() > overall_deadline:
                            raise TimeoutError("utility completion overall timeout") from None
            except TimeoutError:
                log.warning("sister_session_complete_timeout", timeout_s=timeout)
                raise

            if final_message:
                return final_message
            return "".join(delta_chunks)

    async def reconnect(self) -> None:
        """Tear down and recreate the underlying SDK session."""
        await self.close()
        await self.connect()

    async def close(self) -> None:
        """Close the underlying SDK session."""
        if self._session is not None:
            try:
                await self._session.stop()
            except Exception:
                log.debug("sister_session_close_error", exc_info=True)
            self._session = None


class SisterSessionManager:
    """Registry of per-job dedicated utility sessions.

    Replaces the old ``UtilitySessionService`` pool.  No shared sessions,
    no checkout contention — each job owns its session exclusively.

    For non-job callers (terminal ask, MCP naming) that don't have an
    associated job, ``complete()`` creates a one-shot session.
    """

    def __init__(self, model: str = DEFAULT_UTILITY_MODEL) -> None:
        self._model = model
        # Pre-warmed sessions awaiting adoption (token → session)
        self._warm: dict[str, SisterSession] = {}
        self._warm_created_at: dict[str, float] = {}
        # Adopted sessions bound to a job (job_id → session)
        self._jobs: dict[str, SisterSession] = {}
        self._orphan_task: asyncio.Task[None] | None = None

    @property
    def model(self) -> str:
        return self._model

    async def start(self) -> None:
        """Start the orphan reaper background task."""
        self._orphan_task = asyncio.create_task(
            self._orphan_reaper(), name="sister-orphan-reaper"
        )

    # -- Pre-warm (new-job panel) -------------------------------------------

    async def warm(self) -> str:
        """Create a warm session and return a token for later adoption.

        Called when the user opens the new-job panel.
        """
        token = secrets.token_urlsafe(16)
        session = SisterSession(model=self._model)
        try:
            await session.connect()
        except Exception:
            log.warning("sister_warm_connect_failed", exc_info=True)
            raise
        self._warm[token] = session
        self._warm_created_at[token] = time.monotonic()
        log.debug("sister_session_warmed", token=token[:8])
        return token

    async def release(self, token: str) -> bool:
        """Release a warm session the user didn't use.  Returns True if found."""
        session = self._warm.pop(token, None)
        self._warm_created_at.pop(token, None)
        if session is None:
            return False
        await session.close()
        log.debug("sister_session_released", token=token[:8])
        return True

    # -- Job binding ---------------------------------------------------------

    async def adopt(self, token: str, job_id: str) -> None:
        """Bind a pre-warmed session to a job.

        If the token doesn't exist (expired / already released), a new
        session is created on the spot.
        """
        session = self._warm.pop(token, None)
        self._warm_created_at.pop(token, None)
        if session is None:
            log.debug("sister_adopt_token_miss", token=token[:8], job_id=job_id)
            session = SisterSession(model=self._model)
            await session.connect()
        self._jobs[job_id] = session
        log.debug("sister_session_adopted", job_id=job_id)

    async def create_for_job(self, job_id: str) -> None:
        """Create a fresh sister session for a job (resume path)."""
        session = SisterSession(model=self._model)
        await session.connect()
        self._jobs[job_id] = session
        log.debug("sister_session_created", job_id=job_id)

    # -- Per-job access (no checkout, no contention) -------------------------

    def get(self, job_id: str) -> SisterSession | None:
        """Get the sister session for a job.  Always available while job runs."""
        return self._jobs.get(job_id)

    # -- Cleanup -------------------------------------------------------------

    async def close_job(self, job_id: str) -> None:
        """Close and remove the sister session for a finished job."""
        session = self._jobs.pop(job_id, None)
        if session is not None:
            await session.close()
            log.debug("sister_session_closed", job_id=job_id)

    async def shutdown(self) -> None:
        """Close all sessions (server shutdown)."""
        if self._orphan_task is not None:
            self._orphan_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._orphan_task
            self._orphan_task = None
        for session in list(self._warm.values()):
            await session.close()
        self._warm.clear()
        self._warm_created_at.clear()
        for session in list(self._jobs.values()):
            await session.close()
        self._jobs.clear()
        log.debug("sister_session_manager_shutdown")

    # -- Non-job one-shot (terminal ask, MCP naming) -------------------------

    async def complete(self, prompt: str, timeout: float = 30.0) -> str:
        """One-shot completion for callers without a job context.

        Creates a temporary session, uses it, and discards it.
        For repeated use by a single job, use ``get(job_id).complete()`` instead.
        """
        session = SisterSession(model=self._model)
        try:
            await session.connect()
            for attempt in range(_TIMEOUT_RETRIES + 1):
                try:
                    return await session.complete(prompt, timeout=timeout)
                except TimeoutError:
                    if attempt >= _TIMEOUT_RETRIES:
                        raise
                    await session.reconnect()
            return ""
        except Exception:
            log.warning("sister_oneshot_failed", exc_info=True)
            return ""
        finally:
            await session.close()

    # -- Orphan reaper -------------------------------------------------------

    async def _orphan_reaper(self) -> None:
        """Close warm sessions that were never adopted."""
        try:
            while True:
                await asyncio.sleep(_ORPHAN_CHECK_INTERVAL_S)
                now = time.monotonic()
                expired = [
                    token
                    for token, created in self._warm_created_at.items()
                    if now - created > _ORPHAN_EXPIRY_S
                ]
                for token in expired:
                    session = self._warm.pop(token, None)
                    self._warm_created_at.pop(token, None)
                    if session is not None:
                        await session.close()
                        log.debug("sister_session_orphan_expired", token=token[:8])
        except asyncio.CancelledError:
            pass
