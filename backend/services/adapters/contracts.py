"""Independent observation, execution, permission and utility capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from backend.models.session_event import SessionEvent
    from backend.services.action_policy.classifier import RepoPolicy
    from backend.services.action_policy.router import PolicyRouter
    from backend.services.adapters.agent_adapter import CompletionResult


class Capability(StrEnum):
    read = "read"
    write = "write"
    shell = "shell"
    delegate = "delegate"
    external_access = "external_access"


class CapabilityDeniedError(PermissionError):
    """Requested capabilities exceed policy or the actual bound adapter."""


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    capabilities: frozenset[Capability]

    def require(self, used: frozenset[Capability]) -> None:
        denied = used - self.capabilities
        if denied:
            raise CapabilityDeniedError(f"Invocation exceeds capability grant: {', '.join(sorted(denied))}")


def require_capabilities(
    requested: frozenset[Capability],
    job_allowed: frozenset[Capability],
    supported: frozenset[Capability],
) -> CapabilityGrant:
    """Reject an unsatisfied request rather than silently widening or reducing it."""
    denied = requested - (job_allowed & supported)
    if denied:
        raise CapabilityDeniedError(f"Capabilities unavailable: {', '.join(sorted(denied))}")
    return CapabilityGrant(frozenset(requested))


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    job_id: str
    binding_id: str
    epoch: str
    execution_token: str


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    id: str
    provider_family: str
    supported_versions: tuple[str, ...]
    channels: frozenset[str]
    hooks: tuple[str, ...]
    ownership: str
    steering: str
    cancellation: str
    interception: str
    identity_sources: tuple[str, ...]
    usage_units: frozenset[str]
    capabilities: frozenset[Capability] = frozenset()
    utility: bool = False


@dataclass(frozen=True, slots=True)
class AdapterReadiness:
    ready: bool
    reason: str | None = None
    installed_version: str | None = None


@runtime_checkable
class ObservationAdapter(Protocol):
    def normalize(self, record: Mapping[str, Any]) -> tuple[SessionEvent, ...]:
        """Normalize source records without claiming permission interception."""
        ...


@runtime_checkable
class ExecutionAdapter(Protocol):
    def stream_events(self, execution: ExecutionIdentity) -> AsyncIterator[SessionEvent]: ...

    async def send_message(self, execution: ExecutionIdentity, message: str) -> None: ...

    async def cancel(self, execution: ExecutionIdentity) -> str:
        """Report actual stop or cancel-requested, never observer detachment."""
        ...


@dataclass(frozen=True, slots=True)
class GateAction:
    execution: ExecutionIdentity
    tool_call_id: str
    action_digest: str
    tool_name: str
    arguments: Mapping[str, Any]


@runtime_checkable
class BlockingGateAdapter(Protocol):
    async def before_call(self, action: GateAction) -> bool:
        """Await a bound policy/approval decision before invoking the real tool."""
        ...


@runtime_checkable
class TextCompletionProvider(Protocol):
    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_message: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> CompletionResult: ...


@runtime_checkable
class SessionPolicyAdapter(Protocol):
    def attach_attempt_policy(
        self, session_id: str, job_id: str, attempt_id: str, epoch: str,
        router: PolicyRouter, policy: RepoPolicy, *, execution_token: str,
    ) -> None:
        """Reuse Job policy on an existing, attributed session without changing primary defaults."""
        ...
