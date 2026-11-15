"""Process-local bindings to real attempt sessions, fenced by the owning runtime."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.adapters.agent_adapter import AgentAdapterInterface


class AttemptBindingConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AttemptBinding:
    job_id: str
    attempt_id: str
    session_id: str
    adapter: AgentAdapterInterface
    sdk: str
    token: str
    cancel_requested: bool = False


class AttemptBindings:
    """No scheduler or invented session: trusted host code supplies the live handle."""

    def __init__(self, epoch: str) -> None:
        self._epoch = epoch
        self._bindings: dict[tuple[str, str], AttemptBinding] = {}

    def register(
        self,
        job_id: str,
        attempt_id: str,
        adapter: AgentAdapterInterface,
        session_id: str,
        sdk: str,
    ) -> AttemptBinding:
        if not all(value.strip() for value in (job_id, attempt_id, session_id, sdk)):
            raise ValueError("Attempt binding requires nonempty Job, attempt, SDK and actual session identities")
        key = (job_id, attempt_id)
        if key in self._bindings:
            raise AttemptBindingConflictError("Attempt already has a live execution binding")
        if any(binding.adapter is adapter and binding.session_id == session_id for binding in self._bindings.values()):
            raise AttemptBindingConflictError("Session already belongs to a live attempt")
        nonce = uuid.uuid4().hex
        token = hashlib.sha256(
            f"{self._epoch}:{job_id}:{attempt_id}:{id(adapter)}:{session_id}:{nonce}".encode()
        ).hexdigest()
        binding = AttemptBinding(job_id, attempt_id, session_id, adapter, sdk, token)
        self._bindings[key] = binding
        return binding

    def get(self, job_id: str, attempt_id: str) -> AttemptBinding | None:
        return self._bindings.get((job_id, attempt_id))

    def unregister(self, job_id: str, attempt_id: str, expected_execution: str) -> AttemptBinding:
        binding = self.get(job_id, attempt_id)
        if binding is None or binding.token != expected_execution:
            raise AttemptBindingConflictError("Attempt execution is missing or has been replaced")
        del self._bindings[(job_id, attempt_id)]
        return binding

    def request_cancel(self, job_id: str, attempt_id: str, expected_execution: str) -> AttemptBinding:
        binding = self.get(job_id, attempt_id)
        if binding is None or binding.token != expected_execution:
            raise AttemptBindingConflictError("Attempt execution is missing or has been replaced")
        binding = replace(binding, cancel_requested=True)
        self._bindings[(job_id, attempt_id)] = binding
        return binding

    def for_job(self, job_id: str) -> tuple[AttemptBinding, ...]:
        return tuple(binding for binding in self._bindings.values() if binding.job_id == job_id)

    def all(self) -> tuple[AttemptBinding, ...]:
        return tuple(self._bindings.values())
