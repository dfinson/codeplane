"""Utility session pool — warm Copilot SDK sessions with a cheap/fast model.

Keeps one or two sessions alive on server start for non-agentic meta-work:
naming, summarization, progress headlines, commit messages, etc.

Sessions are created once at startup and reused across all jobs. If a session
dies it is transparently re-created on the next call.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from copilot.generated.session_events import SessionEvent as SdkSessionEvent
    from copilot.session import CopilotSession

log = structlog.get_logger()

# Default model for utility work — cheap and fast
DEFAULT_UTILITY_MODEL = "gpt-4o-mini"

# System prompt injected at session creation so the model understands its role
_UTILITY_SYSTEM_PROMPT = """\
You are a concise utility assistant embedded in a coding task management system
called Tower. Your sole purpose is to generate short metadata: titles, branch
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
    """

    def __init__(self, model: str = DEFAULT_UTILITY_MODEL, pool_size: int = 1) -> None:
        self._model = model
        self._pool_size = pool_size
        self._sessions: list[_WarmSession] = []
        self._lock = asyncio.Lock()
        self._round_robin = 0
        self._started = False

    @property
    def model(self) -> str:
        return self._model

    async def start(self) -> None:
        """Create the warm session pool. Safe to call multiple times."""
        if self._started:
            return
        for i in range(self._pool_size):
            ws = _WarmSession(model=self._model, index=i)
            try:
                await ws.connect()
                self._sessions.append(ws)
                log.info("utility_session_ready", index=i, model=self._model)
            except Exception:
                log.warning("utility_session_start_failed", index=i, exc_info=True)
        self._started = True
        if not self._sessions:
            log.warning("utility_session_pool_empty", model=self._model)

    async def complete(self, prompt: str, timeout: float = 30.0) -> str:
        """Send a prompt to a warm session and return the response.

        Automatically reconnects if the session is dead. Falls back to
        creating a one-off session if the pool is exhausted.
        """
        if not self._sessions:
            # Pool empty — try a cold start
            ws = _WarmSession(model=self._model, index=0)
            try:
                await ws.connect()
                self._sessions.append(ws)
            except Exception:
                log.error("utility_session_cold_start_failed", exc_info=True)
                return ""

        # Pick a session round-robin
        async with self._lock:
            idx = self._round_robin % len(self._sessions)
            self._round_robin += 1
            ws = self._sessions[idx]

        try:
            return await ws.complete(prompt, timeout=timeout)
        except Exception:
            log.warning("utility_session_complete_failed", index=ws.index, exc_info=True)
            # Try to reconnect for next call
            try:
                await ws.reconnect()
            except Exception:
                log.warning("utility_session_reconnect_failed", index=ws.index, exc_info=True)
            return ""

    async def shutdown(self) -> None:
        """Abort all warm sessions."""
        for ws in self._sessions:
            await ws.close()
        self._sessions.clear()
        self._started = False
        log.info("utility_session_pool_shutdown")


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

    async def connect(self) -> None:
        """Create a fresh Copilot session with the utility model."""
        from copilot import CopilotClient, PermissionRequestResult
        from copilot.types import SessionConfig

        client = CopilotClient()

        async def _noop_permission(request: object, invocation: dict[str, str]) -> PermissionRequestResult:
            return PermissionRequestResult(kind="approved")

        opts = SessionConfig(
            working_directory="/tmp",
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
