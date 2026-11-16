import asyncio
from decimal import Decimal

import pytest

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
