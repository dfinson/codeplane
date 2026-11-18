"""Admission/recovery selection over immutable, application-owned contract pins."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.models.domain import StateConflictError
from backend.models.execution_contracts import ExecutionContract, ExecutionContractPin

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from backend.persistence.execution_contract_repo import ExecutionContractRepository


class ExecutionContracts:
    def __init__(
        self,
        repository: ExecutionContractRepository,
        *,
        available: Mapping[str, ExecutionContract],
        selected_for_new: str,
        select_for_job: Callable[[str, ExecutionContract | None], Awaitable[ExecutionContract]] | None = None,
    ) -> None:
        self._repository = repository
        self._available = {key: value.model_copy(deep=True) for key, value in available.items()}
        if any(key != contract.id for key, contract in self._available.items()):
            raise ValueError("Contract catalog identities must match their selected implementations")
        if selected_for_new not in self._available:
            raise ValueError("Selected admission contract has no available implementation")
        self._selected = selected_for_new
        self._select_for_job = select_for_job

    def require_available(self, pin: ExecutionContractPin) -> None:
        if self._available.get(pin.contract.id) != pin.contract:
            raise StateConflictError(
                "Pinned implementation is unavailable or changed; explicit reconciliation is required"
            )

    async def fresh(self, job_id: str, admission_id: str) -> ExecutionContractPin:
        """Only the trusted fresh-admission path calls this, after durable admission."""
        retained = await self._repository.get(job_id, admission_id)
        if retained is not None:
            self.require_available(retained)
            if self._select_for_job is not None:
                await self._select_for_job(job_id, retained.contract)
            return retained
        selected = self._available[self._selected].model_copy(deep=True)
        if self._select_for_job is not None:
            selected = await self._select_for_job(job_id, await self._repository.previous_contract(job_id))
            if self._available.get(selected.id) != selected:
                raise StateConflictError("The Job's retained implementation requires explicit reconciliation")
        pin = ExecutionContractPin(
            job_id=job_id,
            admission_id=admission_id,
            budget_id=job_id,
            contract=selected,
        )
        return await self._repository.create(pin)

    async def recover(self, job_id: str, admission_id: str) -> ExecutionContractPin | None:
        """Missing provenance stays unrecorded; recovery never creates or upgrades a pin."""
        pin = await self._repository.get(job_id, admission_id)
        if pin is not None:
            self.require_available(pin)
            if self._select_for_job is not None:
                await self._select_for_job(job_id, pin.contract)
        return pin

    async def bind_handle(
        self,
        pin: ExecutionContractPin,
        *,
        sdk: str,
        session_id: str,
        live_epoch: str,
        execution_token: str,
        attempt_id: str | None = None,
    ) -> None:
        self.require_available(pin)
        await self._repository.bind_handle(
            pin,
            sdk=sdk,
            session_id=session_id,
            live_epoch=live_epoch,
            execution_token=execution_token,
            attempt_id=attempt_id,
        )
