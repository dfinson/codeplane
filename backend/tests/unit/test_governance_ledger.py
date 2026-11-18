import asyncio
from decimal import Decimal

import pytest

from backend.models.domain import StateConflictError
from backend.models.usage import UsageAmount
from backend.persistence.governance_repo import GovernanceLedger


async def test_executed_failure_accrues_once_but_rejection_never_does(tmp_path) -> None:
    ledger = GovernanceLedger(tmp_path / "governance.db", application_database=tmp_path / "data.db")
    await ledger.initialize()
    arguments = dict(
        job_id="job",
        epoch="epoch",
        event_id="event",
        tool_call_id="call",
        source_id="sdk-session",
        action_digest="digest",
        executed=True,
        succeeded=False,
        destructive=True,
    )
    results = await asyncio.gather(ledger.record_completion(**arguments), ledger.record_completion(**arguments))
    assert sorted(results) == [False, True]
    assert not await ledger.record_completion(**dict(arguments, tool_call_id="rejected", executed=False))
    totals = await ledger.totals("job", "epoch")
    assert totals.executed_calls == totals.destructive_calls == 1
    assert totals.verified_usd == 0 and not totals.verified_usd_present
    with pytest.raises(ValueError, match="Conflicting"):
        await ledger.record_completion(**dict(arguments, action_digest="changed"))


async def test_missing_tool_identity_marks_budget_unaccountable_across_restart(tmp_path) -> None:
    path = tmp_path / "governance.db"
    ledger = GovernanceLedger(path, application_database=tmp_path / "data.db")
    await ledger.initialize()
    await ledger.record_completion(
        job_id="job",
        epoch="epoch",
        event_id="event",
        tool_call_id=None,
        source_id="sdk-session",
        action_digest="digest",
        executed=True,
        succeeded=True,
        destructive=False,
    )
    restored = GovernanceLedger(path, application_database=tmp_path / "data.db")
    totals = await restored.totals("job", "epoch")
    assert totals.executed_calls == 0
    assert totals.unaccounted == 1 and not totals.budget_accountable


async def test_only_verified_usd_accrues_and_zero_is_present(tmp_path) -> None:
    ledger = GovernanceLedger(tmp_path / "governance.db", application_database=tmp_path / "data.db")
    await ledger.initialize()
    usage = UsageAmount(
        amount=Decimal("50"),
        unit="copilot_cost_unit",
        producer="copilot",
        model="model",
        job_id="job",
        execution_epoch="epoch",
        role="primary",
        source="assistant.usage",
    )
    await ledger.record_usage("native", usage)
    totals = await ledger.totals("job", "epoch")
    assert totals.verified_usd == 0 and not totals.verified_usd_present
    verified = usage.model_copy(update={"amount": Decimal("0"), "unit": "USD", "currency": "USD", "verified": True})
    await ledger.record_usage("verified", verified)
    assert not await ledger.record_usage("verified", verified)
    totals = await ledger.totals("job", "epoch")
    assert totals.verified_usd == 0 and totals.verified_usd_present


def test_governance_never_uses_application_database_or_its_alias(tmp_path) -> None:
    path = tmp_path / "data.db"
    path.touch()
    with pytest.raises(ValueError):
        GovernanceLedger(path, application_database=path)
    alias = tmp_path / "alias.db"
    alias.hardlink_to(path)
    with pytest.raises(ValueError):
        GovernanceLedger(alias, application_database=path)


async def test_sibling_vendor_call_ids_are_distinct_and_each_replay_is_deduplicated(tmp_path) -> None:
    ledger = GovernanceLedger(tmp_path / "governance.db", application_database=tmp_path / "data.db")
    await ledger.initialize()
    for source in ("attempt-first", "attempt-second"):
        arguments = dict(
            job_id="job",
            epoch="epoch",
            source_id=source,
            event_id=f"event-{source}",
            tool_call_id="vendor-shared",
            action_digest="same-action",
            executed=True,
            succeeded=True,
            destructive=False,
        )
        assert await ledger.record_completion(**arguments)
        assert not await ledger.record_completion(**arguments)
    totals = await ledger.totals("job", "epoch")
    assert totals.executed_calls == 2 and totals.unaccounted == 0


async def test_job_budget_retains_prior_epochs_and_uncertainty_after_restart(tmp_path) -> None:
    path = tmp_path / "governance.db"
    ledger = GovernanceLedger(path, application_database=tmp_path / "data.db")
    await ledger.initialize()
    for job_id, epoch, call_id in (
        ("job", "first-process", "first"),
        ("job", "second-process", "second"),
        ("job", "first-process", None),
        ("other-job", "first-process", "unrelated"),
    ):
        await ledger.record_completion(
            job_id=job_id,
            epoch=epoch,
            event_id=call_id or "unknown",
            source_id="actual-session",
            tool_call_id=call_id,
            action_digest="digest",
            executed=True,
            succeeded=False,
            destructive=True,
        )
        await ledger.record_usage(
            call_id or "unknown",
            UsageAmount(
                amount=Decimal("0.125"),
                unit="USD",
                currency="USD",
                verified=True,
                producer="sdk",
                role="primary",
                source="provider.cost",
                model=None,
                job_id=job_id,
                execution_epoch=epoch,
            ),
        )
    restored = GovernanceLedger(path, application_database=tmp_path / "data.db")
    carried = await restored.job_totals("job")
    assert carried.executed_calls == carried.destructive_calls == 2
    assert carried.verified_usd == Decimal("0.375") and carried.verified_usd_present
    assert carried.unaccounted == 1 and not carried.budget_accountable
    current = await restored.totals("job", "second-process")
    assert current.executed_calls == 1 and current.verified_usd == Decimal("0.125")
    assert current.budget_accountable
    empty = await restored.job_totals("new-job")
    assert empty.executed_calls == 0 and not empty.verified_usd_present


async def test_admission_fence_survives_restart_and_cannot_be_released_by_a_stale_writer(tmp_path) -> None:
    path = tmp_path / "governance.db"
    ledger = GovernanceLedger(path, application_database=tmp_path / "data.db")
    await ledger.initialize()
    await ledger.require_admission()
    initial = await ledger.admission_state()
    blocked = await ledger.set_admission_control(
        expected_generation=initial.generation, blocked=True, reason="Provider transition is draining"
    )
    restored = GovernanceLedger(path, application_database=tmp_path / "data.db")
    await restored.initialize()
    with pytest.raises(StateConflictError, match="draining"):
        await restored.require_admission()
    with pytest.raises(StateConflictError, match="generation changed"):
        await restored.set_admission_control(
            expected_generation=initial.generation, blocked=False, reason="Stale activation"
        )
    assert await restored.admission_state() == blocked
    await restored.record_completion(
        job_id="existing-job",
        epoch="existing-epoch",
        event_id="completion-during-drain",
        tool_call_id="existing-call",
        source_id="existing-session",
        action_digest="accepted-before-fence",
        executed=True,
        succeeded=True,
        destructive=False,
    )
    assert (await ledger.job_totals("existing-job")).executed_calls == 1
    await restored.set_admission_control(
        expected_generation=blocked.generation, blocked=False, reason="Drain and comparison verified"
    )
    await ledger.require_admission()


async def test_uninitialized_admission_fence_does_not_create_or_assume_an_open_gate(tmp_path) -> None:
    path = tmp_path / "missing.db"
    ledger = GovernanceLedger(path, application_database=tmp_path / "data.db")
    with pytest.raises(StateConflictError, match="not been initialized"):
        await ledger.require_admission()
    assert not path.exists()
