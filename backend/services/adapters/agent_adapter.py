"""Agent adapter interface — the contract all SDK adapters must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from backend.models.domain import AgentSDK, SDKModelMismatchError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from backend.models.api_schemas import ExecutionPhase
    from backend.models.domain import SessionConfig
    from backend.models.events import SessionEvent
    from backend.models.usage import UsageAmount


@dataclass(slots=True)
class CompletionResult:
    """SDK-agnostic result of a single-turn ``complete()`` call.

    Adapters populate whichever fields their SDK exposes.  Missing data
    stays at zero — callers must treat zeroes as *unknown*, not *none*.
    """

    text: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    usage: tuple[UsageAmount, ...] = ()
    usage_event_id: str | None = None
    raw_text: str | None = None
    response_normalization: Literal["single_json_code_fence"] | None = None


class UtilityCompletionError(OSError):
    """A text-only provider call failed; no successful completion is available.

    The OSError base preserves existing utility consumers' provider-I/O
    fallback paths. ``reason`` distinguishes timeout, abort and SDK failures.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str = "sdk_error",
        completion: CompletionResult | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.completion = completion


# Model prefixes accepted by each SDK.  An empty tuple means "any model".
_SDK_MODEL_PREFIXES: dict[AgentSDK, tuple[str, ...]] = {
    AgentSDK.copilot: (),  # Copilot proxies to multiple providers — any model
    AgentSDK.claude: ("claude-",),  # Claude SDK only supports Anthropic models
}


def normalize_model_name(model: str) -> str:
    """Strip date-version suffix from model names for comparison.

    SDKs often return the fully-qualified model name with a date suffix
    (e.g. ``claude-haiku-4-5-20251001``) even when the request used the
    short alias (``claude-haiku-4-5``).  This normalises both forms so
    that ``requested == actual`` comparisons aren't falsely triggered.
    """
    import re

    return re.sub(r"-\d{8}$", "", model)


def validate_sdk_model(sdk: str, model: str | None) -> None:
    """Raise SDKModelMismatchError if *model* is incompatible with *sdk*.

    No-op when model is None/empty (SDK will use its default).
    """
    try:
        sdk_enum = AgentSDK(sdk)
    except ValueError:
        raise SDKModelMismatchError(f"Unknown SDK: {sdk!r}") from None
    if not model:
        return
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
        yield  # type: ignore[misc]  # abstract async generator needs a yield for the type system

    @abstractmethod
    async def send_message(self, session_id: str, message: str) -> None:
        """Send a follow-up message into a running session."""

    async def send_peer_message(
        self, session_id: str, message: str, *, can_send: Callable[[], Awaitable[bool]] | None = None
    ) -> None:
        """Queue a peer note, checking runtime eligibility immediately before enqueue."""
        raise NotImplementedError("This adapter does not support peer delivery")

    @abstractmethod
    async def abort_session(self, session_id: str) -> None:
        """Abort the current message processing and destroy the session."""

    async def shutdown(self) -> None:  # noqa: B027
        """Release owned clients and drain callback work before service teardown."""

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
    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_message: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> CompletionResult:
        """Non-agentic single-turn completion.

        Returns a ``CompletionResult`` with the response text and any
        token/cost metadata the SDK exposes. Text-only implementations deny
        tool permissions and raise ``UtilityCompletionError`` on provider
        failure, timeout, abort or an empty response. Caller cancellation
        propagates after releasing owned resources.
        """
        return CompletionResult()

    def set_execution_phase(self, job_id: str, phase: ExecutionPhase) -> None:  # noqa: B027
        """Update the current execution phase for a job (used by cost analytics)."""
