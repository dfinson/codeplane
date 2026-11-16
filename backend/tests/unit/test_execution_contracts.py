import pytest

from backend.models.domain import StateConflictError
from backend.models.events import new_event
from backend.models.execution_contracts import (
    ExecutionAdmission,
    ExecutionContract,
    ExecutionContractPin,
    ImplementationPin,
)
from backend.models.session_event import EventMetadata
from backend.persistence.event_repo import EventRepository
from backend.persistence.execution_admission_repo import ExecutionAdmissionRepository
from backend.persistence.execution_contract_repo import ExecutionContractRepository, _identity
from backend.services.runtime.execution_contracts import ExecutionContracts
from backend.tests.unit.test_approval_service import session_factory as session_factory


def _contract(name):
    component = ImplementationPin(id=f"test-{name}", sha256="a" * 64)
    return ExecutionContract(
        id=name, policy=component, parser=component, grouping=component, title=component,
        grammars=(ImplementationPin(id="test-grammar", sha256="b" * 64),),
    )


async def test_restart_reuses_admission_pin_while_live_epoch_and_token_change(session_factory):
    repository = ExecutionContractRepository(session_factory)
    contract = _contract("old")
    first = ExecutionContracts(repository, available={"old": contract}, selected_for_new="old")
    pin = await first.fresh("job-1", "run")
    await first.bind_handle(pin, sdk="copilot", session_id="reused", live_epoch="one", execution_token="token-one")
    restarted = ExecutionContracts(repository, available={"old": contract}, selected_for_new="old")
    restored = await restarted.recover("job-1", "run")
    assert restored == pin
    await restarted.bind_handle(
        restored, sdk="copilot", session_id="reused", live_epoch="two", execution_token="token-two",
    )
    async with session_factory() as session:
        events = [item.event for item in await EventRepository(session).list_after(0, job_id="job-1")]
    assert len([event for event in events if event.kind == "execution.contract.pinned"]) == 1
    handles = [event for event in events if event.kind == "execution.contract.handle_bound"]
    assert [event.metadata.execution_epoch for event in handles] == ["one", "two"]
    assert all(event.payload["budget_id"] == "job-1" for event in handles)


async def test_missing_recovery_never_mints_pin_and_new_continuation_retains_job_budget(session_factory):
    repository = ExecutionContractRepository(session_factory)
    old, new = _contract("old"), _contract("new")
    first = ExecutionContracts(repository, available={"old": old}, selected_for_new="old")
    assert await first.recover("job-1", "unrecorded") is None
    retained = await first.fresh("job-1", "first-run")
    changed = ExecutionContracts(repository, available={"old": old, "new": new}, selected_for_new="new")
    assert await changed.recover("job-1", "first-run") == retained
    assert await changed.fresh("job-1", "first-run") == retained
    continuation = await changed.fresh("job-1", "next-run")
    assert continuation.contract == new
    assert continuation.budget_id == retained.budget_id == "job-1"
    missing_old = ExecutionContracts(repository, available={"new": new}, selected_for_new="new")
    with pytest.raises(StateConflictError, match="unavailable or changed"):
        await missing_old.recover("job-1", "first-run")


async def test_identical_pin_retry_is_idempotent_and_conflicting_pin_fails(session_factory):
    repository = ExecutionContractRepository(session_factory)
    pin = ExecutionContractPin(job_id="job-1", admission_id="run", budget_id="job-1", contract=_contract("old"))
    assert await repository.create(pin) == await repository.create(pin)
    with pytest.raises(StateConflictError, match="Conflicting"):
        await repository.create(pin.model_copy(update={"contract": _contract("new")}))


async def test_observed_pin_looking_event_cannot_be_consumed_as_admission_authority(session_factory):
    pin = ExecutionContractPin(job_id="job-1", admission_id="run", budget_id="job-1", contract=_contract("old"))
    async with session_factory() as session:
        await EventRepository(session).append(new_event(
            "job-1", "execution.contract.pinned", pin.model_dump(mode="json"), event_id=_identity("job-1", "run"),
            metadata=EventMetadata(integration_binding_id="binding", namespace=("observation-source", "events")),
        ))
        await session.commit()
    with pytest.raises(StateConflictError, match="untrusted provenance"):
        await ExecutionContractRepository(session_factory).get("job-1", "run")


async def test_abandoned_continuation_restores_selection_without_erasing_admission_or_pin(session_factory):
    initial = ExecutionAdmission(id="initial", job_id="job-1", origin="creation")
    continuation = ExecutionAdmission(
        id="continuation", job_id="job-1", origin="continuation", previous_admission_id=initial.id,
    )
    async with session_factory() as session:
        admissions = ExecutionAdmissionRepository(session)
        await admissions.declare_legacy(initial, expected_current=None)
        await session.commit()
    contracts = ExecutionContracts(
        ExecutionContractRepository(session_factory), available={"old": _contract("old")}, selected_for_new="old",
    )
    first_pin = await contracts.fresh("job-1", initial.id)
    async with session_factory() as session:
        await ExecutionAdmissionRepository(session).declare_legacy(continuation, expected_current=initial.id)
        await session.commit()
    second_pin = await contracts.fresh("job-1", continuation.id)
    async with session_factory() as session:
        admissions = ExecutionAdmissionRepository(session)
        with pytest.raises(StateConflictError, match="replaced"):
            await admissions.abandon(initial, reason="Late failure from previous invocation")
        await admissions.abandon(continuation, reason="Continuation failed during startup")
        await session.commit()
    async with session_factory() as session:
        assert await ExecutionAdmissionRepository(session).current("job-1") == initial
        events = await EventRepository(session).list_after(0, job_id="job-1")
    assert len([row for row in events if row.event.kind == "execution.admission.declared"]) == 2
    assert await contracts.recover("job-1", initial.id) == first_pin
    assert await contracts.recover("job-1", continuation.id) == second_pin


async def test_rolled_back_admission_transaction_does_not_change_current_selection(session_factory):
    initial = ExecutionAdmission(id="initial", job_id="job-1", origin="creation")
    async with session_factory() as session:
        assert await ExecutionAdmissionRepository(session).current("job-1") is None
        await ExecutionAdmissionRepository(session).declare_legacy(initial, expected_current=None)
        await session.rollback()
    async with session_factory() as session:
        assert await ExecutionAdmissionRepository(session).current("job-1") is None
        assert await EventRepository(session).list_after(0, job_id="job-1") == []


async def test_source_observation_cannot_select_a_legacy_admission(session_factory):
    from backend.persistence.execution_admission_repo import _DECLARED
    from backend.persistence.execution_admission_repo import _identity as admission_identity

    admission = ExecutionAdmission(id="forged", job_id="job-1", origin="creation")
    async with session_factory() as session:
        await EventRepository(session).append(new_event(
            "job-1", _DECLARED, admission.model_dump(mode="json"),
            event_id=admission_identity("job-1", admission.id, _DECLARED),
            metadata=EventMetadata(
                source_framework="codeplane", namespace=("control", "legacy-admission"),
                integration_binding_id="observed",
            ),
        ))
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(StateConflictError, match="untrusted"):
            await ExecutionAdmissionRepository(session).current("job-1")
