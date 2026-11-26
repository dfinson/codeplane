"""Job-scoped inference identity. References only, never process-wide auth changes."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.models.domain import StateConflictError
from backend.persistence.credential_repo import CredentialRepository
from backend.persistence.job_repo import JobRepository

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class InferenceAccountError(StateConflictError):
    """The selected inference credential cannot be used. Never fall back."""


@dataclass(frozen=True)
class InferenceBinding:
    job_id: str | None = None
    credential_id: str | None = None


_EMPTY_BINDING = InferenceBinding()
_binding: ContextVar[InferenceBinding] = ContextVar("inference_binding", default=_EMPTY_BINDING)


@contextmanager
def inference_scope(*, job_id: str | None = None, credential_id: str | None = None) -> Iterator[None]:
    """Explicit async-task-local scope, inherited by child tasks and reset on exit."""
    token = _binding.set(InferenceBinding(job_id, credential_id))
    try:
        yield
    finally:
        _binding.reset(token)


async def validate_copilot_credential(repo: CredentialRepository, credential_id: str, sdk: str) -> str:
    if sdk != "copilot":
        raise InferenceAccountError("Explicit inference accounts require a managed Copilot job")
    credential = await repo.get(credential_id)
    if credential is None or credential["provider"] != "copilot":
        raise InferenceAccountError("Selected Copilot credential is missing or is not a Copilot credential")
    return str(credential["label"])


async def resolve_inference_token(
    session_factory: async_sessionmaker[AsyncSession] | None,
    *,
    job_id: str | None = None,
    credential_id: str | None = None,
) -> str | None:
    """Resolve the immutable persisted binding on every invocation."""
    scope = _binding.get()
    if job_id is None:
        job_id = scope.job_id
        credential_id = credential_id or scope.credential_id
    elif job_id == scope.job_id:
        credential_id = credential_id or scope.credential_id
    if session_factory is None:
        if credential_id:
            raise InferenceAccountError("Credential storage is unavailable for the selected Copilot account")
        # Standalone adapters without persistence retain legacy ambient behavior.
        return None
    async with session_factory() as session:
        if job_id:
            job = await JobRepository(session).get(job_id)
            if job is None:
                raise InferenceAccountError("Inference job no longer exists")
            if credential_id and credential_id != job.copilot_credential_id:
                raise InferenceAccountError("Cannot change a job's inference account")
            credential_id = job.copilot_credential_id
            if credential_id and (job.sdk != "copilot" or job.source != "managed"):
                raise InferenceAccountError("Explicit inference accounts require a managed Copilot job")
        if credential_id is None:
            return None
        repo = CredentialRepository(session)
        await validate_copilot_credential(repo, credential_id, "copilot")
        try:
            secret = await repo.resolve_secret(credential_id)
        except Exception:
            raise InferenceAccountError("Selected Copilot credential cannot be decrypted") from None
        if not secret or not secret.strip():
            raise InferenceAccountError("Selected Copilot credential is empty or unavailable")
        return secret
