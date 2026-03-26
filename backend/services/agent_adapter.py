"""Agent adapter interface — the contract all SDK adapters must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from backend.models.domain import SessionConfig, SessionEvent


class AgentSDK(StrEnum):
    """Supported agent SDK backends."""

    copilot = "copilot"
    claude = "claude"


# Model prefixes accepted by each SDK.  An empty tuple means "any model".
_SDK_MODEL_PREFIXES: dict[AgentSDK, tuple[str, ...]] = {
    AgentSDK.copilot: (),  # Copilot proxies to multiple providers — any model
    AgentSDK.claude: ("claude-",),  # Claude SDK only supports Anthropic models
}


class SDKModelMismatchError(ValueError):
    """Raised when a model is incompatible with the selected SDK."""


def validate_sdk_model(sdk: str, model: str | None) -> None:
    """Raise SDKModelMismatchError if *model* is incompatible with *sdk*.

    No-op when model is None/empty (SDK will use its default).
    """
    if not model:
        return
    try:
        sdk_enum = AgentSDK(sdk)
    except ValueError:
        raise SDKModelMismatchError(f"Unknown SDK: {sdk!r}") from None
    prefixes = _SDK_MODEL_PREFIXES.get(sdk_enum, ())
    if prefixes and not model.startswith(prefixes):
        allowed = ", ".join(f"{p}*" for p in prefixes)
        raise SDKModelMismatchError(
            f"Model {model!r} is not compatible with the {sdk} SDK. Accepted model prefixes: {allowed}"
        )


# Shared system prompt appended to all agent sessions.
# Tells the agent it's running headless inside CodePlane.
CODEPLANE_SYSTEM_PROMPT = (
    "You are running inside CodePlane, a headless non-interactive orchestration "
    "framework. There is no human at a terminal. Do not enter plan mode or "
    "pause to present a plan for review. Proceed directly with task execution. "
    "Do NOT run git merge, git pull, git rebase, git cherry-pick, or "
    "git reset --hard unless the operator explicitly instructs you to do so. "
    "CodePlane manages branch merging and integration automatically — running "
    "these commands on your own initiative can corrupt the managed worktree state."
    "\n\n"
    "**FINAL MESSAGE LAW — NO EXCEPTIONS:**\n"
    "When your work is complete and you are yielding control back to the user, "
    "your LAST message MUST be a concise task summary covering: the original task, "
    "every follow-up or change the user requested, and exactly what was done. "
    "This is the ONLY content allowed in that final message. "
    "**UNDER NO CIRCUMSTANCES** may your final message mention testing, linting, "
    "formatting, code hygiene, build steps, or any other housekeeping activity "
    "unless the user EXPLICITLY asked for it in the conversation. "
    "Do NOT pad the summary with caveats, next steps, or offers to do more. "
    "Write it as a tight 'what was done' recap so the user can read one message "
    "and immediately know the full state of their request."
)


class AgentAdapterInterface(ABC):
    """Wraps the agent runtime behind a generic interface."""

    @abstractmethod
    async def create_session(self, config: SessionConfig) -> str:
        """Create a session, return session_id."""

    @abstractmethod
    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        """Stream events from a running session."""
        yield  # type: ignore[misc]

    @abstractmethod
    async def send_message(self, session_id: str, message: str) -> None:
        """Send a follow-up message into a running session."""

    @abstractmethod
    async def abort_session(self, session_id: str) -> None:
        """Abort the current message processing and destroy the session."""

    async def interrupt_session(self, session_id: str) -> None:  # noqa: B027
        """Interrupt the current turn without destroying the session.

        After interruption the session remains alive and a new turn can be
        started via ``send_message``.  The default implementation is a no-op
        (adapters that don't support non-destructive interruption simply
        fall through).
        """

    def pause_tools(self, session_id: str) -> None:  # noqa: B027
        """Block all tool execution for the given session.

        While paused, permission callbacks immediately deny every tool
        request so the agent cannot take actions.  Call ``resume_tools``
        to lift the block.
        """

    def resume_tools(self, session_id: str) -> None:  # noqa: B027
        """Lift the tool block set by ``pause_tools``."""

    @abstractmethod
    async def complete(self, prompt: str) -> str | None:
        """Non-agentic single-turn completion. Returns the full response text, or None on error."""
        return None
