"""Caller limits and provenance shared by text-only providers without new SDK APIs."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from backend.models.execution_contracts import ExecutionContractPin


@dataclass(frozen=True, slots=True)
class CompletionContext:
    max_output_bytes: int = 64 * 1024
    role: Literal["primary", "scout", "monitor", "assessment", "title", "utility"] = "utility"
    job_id: str | None = None
    execution_epoch: str | None = None
    execution_contract: ExecutionContractPin | None = None


_CURRENT: ContextVar[CompletionContext | None] = ContextVar("utility_completion_context", default=None)


def current_completion_context() -> CompletionContext:
    return _CURRENT.get() or CompletionContext()


def validate_completion_identity(requested: str | None, actual: str, context: CompletionContext) -> None:
    from backend.services.adapters.agent_adapter import UtilityCompletionError, normalize_model_name

    if context.role not in {"assessment", "title"}:
        return
    if not actual or not requested:
        raise UtilityCompletionError("Utility model identity is unavailable", reason="model_identity_missing")
    if normalize_model_name(actual) != normalize_model_name(requested):
        raise UtilityCompletionError("Utility provider served a different model", reason="model_mismatch")


@contextmanager
def completion_context(context: CompletionContext) -> Iterator[None]:
    if not 1 <= context.max_output_bytes <= 64 * 1024:
        raise ValueError("Completion output limit must be between 1 and 65536 bytes")
    if context.execution_contract is not None and (
        context.execution_contract.job_id != context.job_id
        or context.execution_contract.budget_id != context.job_id
    ):
        raise ValueError("Utility contract must retain the originating Job and budget")
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)
