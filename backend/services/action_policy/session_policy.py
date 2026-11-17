"""Invocation context shared by real SDK boundaries and the standalone authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.models.session_event import EventMetadata
    from backend.services.auth.permission_policy import PermissionRequest


@dataclass(frozen=True, slots=True)
class PermissionInvocation:
    job_id: str
    session_id: str
    provider: str
    metadata: EventMetadata
    workspace: str
    protected_paths: tuple[str, ...]
    request: PermissionRequest
    tool_name: str
    arguments_json: str
    is_current: Callable[[], Awaitable[bool]]
    tool_call_id: str | None = None
    dialect: str | None = None
    tool_definition: str = ""
    hard_gate: bool = False


@runtime_checkable
class SessionPolicy(Protocol):
    @property
    def execution_epoch(self) -> str: ...

    async def authorize_permission(self, invocation: PermissionInvocation) -> bool: ...
