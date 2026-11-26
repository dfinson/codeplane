import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from backend.models.domain import StateConflictError
from backend.models.usage import UsageAmount
from backend.persistence.governance_repo import GovernanceLedger


def pause_grant_write(monkeypatch, ledger):
    entered, release = asyncio.Event(), asyncio.Event()
    original = ledger._io

    async def paused(operation, *arguments):
        result = await original(operation, *arguments)
        if operation.__qualname__.endswith("grant_reasons.<locals>.write") and not entered.is_set():
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(ledger, "_io", paused)
    return entered, release


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_grant_issuance_preserves_prior_and_concurrent_same_source_grants(tmp_path, monkeypatch, cancel):
    ledger = GovernanceLedger(tmp_path / "governance.db", application_database=tmp_path / "data.db")
    await ledger.initialize()
    await ledger.grant_reasons("job", "policy", frozenset({"cp_usd_ceiling"}), epoch="epoch", source_id="same-source")
    before = await ledger.reason_grants("job", "policy")
    entered, release = pause_grant_write(monkeypatch, ledger)
    alive = True

    async def current():
        return alive

    task = asyncio.create_task(
        ledger.grant_reasons(
            "job",
            "policy",
            frozenset({"cp_usd_ceiling", "external_mutation"}),
            epoch="epoch",
            source_id="same-source",
            is_current=current,
        )
    )
    sibling = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        reader = asyncio.create_task(ledger.reason_grants("job", "policy"))
        await asyncio.sleep(0)
        assert not reader.done(), "An unconfirmed issuance must not be observable"
        sibling = asyncio.create_task(
            ledger.grant_reasons(
                "job",
                "policy",
                frozenset({"budget_pressure"}),
                epoch="epoch",
                source_id="same-source",
            )
        )
        alive = False
        if cancel:
            task.cancel()
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task is None
        await sibling
        await reader
        after = await ledger.reason_grants("job", "policy")
        assert set(after) == {"cp_usd_ceiling", "budget_pressure"}
        assert after["cp_usd_ceiling"] == before["cp_usd_ceiling"]
        assert after["budget_pressure"].issuance_id != after["cp_usd_ceiling"].issuance_id
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, *([sibling] if sibling is not None else []), return_exceptions=True)
        await ledger.close()


async def test_reason_grants_preserve_job_policy_scope_and_24_hour_expiry_across_restart(tmp_path):
    from datetime import UTC, datetime, timedelta

    path = tmp_path / "governance.db"
    ledger = GovernanceLedger(path, application_database=tmp_path / "data.db")
    await ledger.initialize()
    await ledger.grant_reasons("job", "policy", frozenset({"cp_usd_ceiling"}), epoch="epoch", source_id="human")
    await ledger.close()
    restored = GovernanceLedger(path, application_database=tmp_path / "data.db")
    try:
        assert await restored.active_reason_grants("job", "policy") == {"cp_usd_ceiling"}
        assert not await restored.active_reason_grants("other-job", "policy")
        assert not await restored.active_reason_grants("job", "changed-policy")
        with restored._connect() as connection:
            row = connection.execute("SELECT created_at,expires_at FROM cp_reason_grant_issuances").fetchone()
            assert datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"]) == timedelta(
                hours=24
            )
            connection.execute(
                "UPDATE cp_reason_grant_issuances SET expires_at=?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
            )
        assert not await restored.active_reason_grants("job", "policy")
    finally:
        await restored.close()


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


async def test_native_callback_can_read_and_write_ledger_with_one_default_executor_worker(tmp_path) -> None:
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    ledger = GovernanceLedger(tmp_path / "governance.db", application_database=tmp_path / "data.db")
    await ledger.initialize()

    async def policy_work() -> int:
        await ledger.require_admission()
        await ledger.record_uncertainty("job", "epoch", "source", "needs-reconciliation")
        return (await ledger.job_totals("job")).unaccounted

    def native_callback() -> int:
        return asyncio.run_coroutine_threadsafe(policy_work(), loop).result(timeout=2)

    try:
        assert await asyncio.wait_for(asyncio.to_thread(native_callback), timeout=3) == 1
    finally:
        await ledger.close()


async def test_cancelled_waiter_and_close_drain_the_actual_sql_write_and_join_worker(tmp_path, monkeypatch) -> None:
    path = tmp_path / "governance.db"
    ledger = GovernanceLedger(path, application_database=tmp_path / "data.db")
    await ledger.initialize()
    entered, release = threading.Event(), threading.Event()
    worker_ids: list[int] = []
    write = ledger._record_uncertainty

    def delayed(*args) -> None:
        worker_ids.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        write(*args)

    monkeypatch.setattr(ledger, "_record_uncertainty", delayed)
    pending = asyncio.create_task(ledger.record_uncertainty("job", "epoch", "source", "retained"))
    async with asyncio.timeout(2):
        while not entered.is_set():
            await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    closing = asyncio.create_task(ledger.close())
    try:
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        with pytest.raises(RuntimeError, match="I/O is closed"):
            await ledger.require_admission()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
    assert not any(thread.ident in worker_ids and thread.is_alive() for thread in threading.enumerate())
    restored = GovernanceLedger(path, application_database=tmp_path / "data.db")
    try:
        assert (await restored.job_totals("job")).unaccounted == 1
    finally:
        await restored.close()
