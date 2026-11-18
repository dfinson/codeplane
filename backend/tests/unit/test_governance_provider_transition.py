import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime

import pytest
from click.testing import CliRunner

from backend import config as backend_config
from backend.cli import cli
from backend.models.domain import JobState, StateConflictError
from backend.models.events import EventKind, new_event
from backend.models.session_event import EventMetadata
from backend.persistence.event_repo import EventRepository
from backend.persistence.governance_ownership import GovernanceRuntimeLease
from backend.persistence.job_repo import JobRepository
from backend.persistence.legacy_governance_repo import LegacyGovernanceRepository
from backend.persistence.tracker_operation_repo import TrackerOperationRepository
from backend.services.action_policy.classifier import Action, ActionKind, Preset, RepoPolicy
from backend.services.action_policy.governance import GovernanceDecider
from backend.services.action_policy.production import StandaloneGovernance
from backend.services.action_policy.session_policy import PermissionInvocation
from backend.services.auth.permission_policy import PermissionRequest
from backend.tests.native_gates import qualify_runtime_integration
from backend.tests.unit.test_tracker_operation_repo import _intent

core_runtime = qualify_runtime_integration.composed_runtime


@pytest.fixture
async def composed_runtime(core_runtime):
    host = core_runtime[0]

    async def persist(event):
        async with host._session_factory() as session:
            await EventRepository(session).append(event)
            await session.commit()

    host._event_bus.subscribe(persist, required=True)
    try:
        yield core_runtime
    finally:
        host._event_bus.unsubscribe(persist)


def _rows(path, *tables):
    with sqlite3.connect(path) as database:
        database.row_factory = sqlite3.Row
        return {
            name: [dict(row) for row in database.execute(f"SELECT * FROM {name} ORDER BY rowid")] for name in tables
        }


async def _restart(host, root, directory, roots):
    await root.close()
    root = StandaloneGovernance(host, directory)
    roots.append(root)
    host._standalone_governance = root
    await root.start()
    return root


async def _cli(*arguments):
    result = await asyncio.to_thread(CliRunner().invoke, cli, ["governance-transition", *arguments])
    assert result.exit_code == 0, (result.output, result.exception)
    return json.loads(result.output)


async def _execute_read(host, root, job_id, directory, *, retained=False, suffix="one", before_observe=None):
    pin = await host.prepare_execution_contract(job_id, f"admission-{suffix}", recovery=False)
    async with host._session_factory() as session:
        await JobRepository(session).update_state(job_id, JobState.running, datetime.now(UTC))
        await session.commit()
    path = directory / "read.txt"
    path.write_text("Actual local read\n")
    command = f"cat {path}" if retained else None
    metadata = EventMetadata(
        source_framework="copilot",
        run_id=f"local-fixture-{job_id}",
        execution_epoch=host.execution_epoch,
        execution_token=f"token-{job_id}-{suffix}",
    )
    owner = asyncio.current_task()

    async def current():
        return asyncio.current_task() is not None and owner is not None and not owner.done()

    router = root.for_job(pin, RepoPolicy(preset=Preset.supervised))
    invocation = PermissionInvocation(
        job_id=job_id,
        session_id=metadata.run_id,
        provider="copilot",
        metadata=metadata,
        workspace=str(directory),
        protected_paths=(),
        request=PermissionRequest(
            kind="shell" if retained else "read",
            workspace_path=str(directory),
            full_command_text=command,
            file_name=str(path),
        ),
        tool_name="bash" if retained else "read_file",
        arguments_json=json.dumps({"command": command} if retained else {"path": str(path)}),
        is_current=current,
        tool_call_id=f"call-{suffix}",
        dialect="bash",
    )
    assert await router.authorize_permission(invocation)
    if retained:
        process = await asyncio.create_subprocess_exec("cat", str(path), stdout=asyncio.subprocess.PIPE)
        output, _ = await process.communicate()
        assert process.returncode == 0 and output == b"Actual local read\n"
    else:
        assert path.read_text() == "Actual local read\n"
    completed = new_event(
        job_id,
        EventKind.tool_call_completed,
        {
            "tool_call_id": invocation.tool_call_id,
            "tool_name": invocation.tool_name,
            "success": True,
            "result": "Actual local read\n",
        },
        metadata=metadata,
    )
    if before_observe is not None:
        before_observe()
    await host._event_bus.publish(completed)
    if before_observe is None:
        assert root.accounting_error is None
    async with host._session_factory() as session:
        await JobRepository(session).update_state(job_id, JobState.review, datetime.now(UTC))
        await session.commit()
    return pin, completed


@pytest.mark.parametrize("continue_before_selection", [True, False])
async def test_populated_same_database_forward_cycle_preserves_evidence(
    composed_runtime, tmp_path, monkeypatch, continue_before_selection
):
    host, approvals, root, create = composed_runtime
    roots: list[StandaloneGovernance] = []
    monkeypatch.setattr(backend_config, "get_codeplane_dir", lambda: tmp_path)
    await create("legacy-job")
    await root.close()
    old = GovernanceDecider(tmp_path / "governance.db")
    old.register_job("legacy-job", Preset.supervised)
    old.observe(Action(kind=ActionKind.sdk_tool, tool_name="read_file", job_id="legacy-job"), tool_call_id="original")
    assert old.grant_trust("legacy-job", "readonly_shell_locked", 3600, reason="original scoped grant")
    old.close()
    importer = LegacyGovernanceRepository(tmp_path / "governance.db", tmp_path / "data.db")
    with GovernanceRuntimeLease(tmp_path, exclusive=True) as lease:
        imported = importer.import_original(lease)
    from pathlib import Path

    backup = Path(imported["backup_path"])
    backup_bytes = backup.read_bytes()
    original = _rows(
        tmp_path / "governance.db", "cp_legacy_rows", "cp_legacy_budget", "cp_legacy_grants", "trust_grants"
    )
    try:
        root = await _restart(host, root, tmp_path, roots)
        if continue_before_selection:
            legacy_pin = await host.prepare_execution_contract("legacy-job", "legacy-continuation", recovery=False)
            assert legacy_pin.contract.policy.id == "tf-policy-v1"
            await _execute_read(host, root, "legacy-job", tmp_path, retained=True, suffix="legacy-continuation")
        await create("cp-job")
        cp_pin, cp_receipt = await _execute_read(host, root, "cp-job", tmp_path)
        pending = await approvals.create_request("cp-job", "Preserve pending decision", '{"fixture":"pending"}')
        async with host._session_factory() as session:
            await TrackerOperationRepository(session).create(
                _intent(
                    project_id="project",
                    project_ref="project",
                    job_id="cp-job",
                    job_ref="cp-job",
                    task_link_id=None,
                    tracker_link_id=None,
                    state="uncertain",
                )
            )
            await session.commit()
        await root._ledger.record_uncertainty("cp-job", host.execution_epoch, "uncertain-receipt", "unknown outcome")
        blocked = await _cli("quiesce")
        with pytest.raises(StateConflictError, match="blocked"):
            await host.prepare_execution_contract("cp-job", "blocked-admission", recovery=False)
        before_recovery = _rows(tmp_path / "data.db", "jobs", "approvals", "tracker_operations", "events")
        await host.recover_on_startup()
        assert _rows(tmp_path / "data.db", *before_recovery) == before_recovery
        await root.close()
        assert (await _cli("select-provider", "retained", "--generation", str(blocked["generation"])))[
            "preserved_jobs"
        ] == 2
        assert _rows(tmp_path / "data.db", *before_recovery) == before_recovery
        root = await _restart(host, root, tmp_path, roots)
        if not continue_before_selection:
            assert root._providers.owner("legacy-job").policy.id == "tf-policy-v1"
            await _execute_read(host, root, "legacy-job", tmp_path, retained=True, suffix="legacy-continuation")
        assert (
            await host.prepare_execution_contract("cp-job", "continuation", recovery=False)
        ).contract == cp_pin.contract
        await create("tf-job")
        tf_pin, receipt = await _execute_read(host, root, "tf-job", tmp_path, retained=True)
        assert tf_pin.contract.policy.id == "tf-policy-v1"
        assert (await root._ledger.job_totals("tf-job")).executed_calls == 1
        duplicate = receipt.model_copy(update={"id": "redelivered-event"})
        async with host._session_factory() as session:
            await EventRepository(session).append(duplicate)
            await session.commit()
        await root.observe(duplicate)
        assert root.accounting_error is None
        assert (await root._ledger.job_totals("tf-job")).executed_calls == 1
        blocked = await _cli("quiesce")
        await root.close()
        before_return = _rows(tmp_path / "data.db", "jobs", "approvals", "tracker_operations", "events")
        await _cli("select-provider", "standalone", "--generation", str(blocked["generation"]))
        assert _rows(tmp_path / "data.db", *before_return) == before_return
        root = await _restart(host, root, tmp_path, roots)
        assert (
            await host.prepare_execution_contract("tf-job", "continued-tf", recovery=False)
        ).contract == tf_pin.contract
        await create("new-cp-job")
        new_pin, _ = await _execute_read(host, root, "new-cp-job", tmp_path)
        assert new_pin.contract == cp_pin.contract
        assert (await root._ledger.job_totals("legacy-job")).executed_calls == 2
        assert (await root._ledger.job_totals("cp-job")).executed_calls == 1
        assert (await root._ledger.job_totals("cp-job")).unaccounted == 1
        assert (await root._ledger.job_totals("tf-job")).executed_calls == 1
        assert (await root._ledger.job_totals("new-cp-job")).executed_calls == 1
        assert [approval.id for approval in await approvals.list_pending("cp-job")] == [pending.id]
        assert _rows(tmp_path / "data.db", "tracker_operations") == {
            "tracker_operations": before_return["tracker_operations"]
        }
        assert _rows(tmp_path / "governance.db", *original) == original
        assert backup.read_bytes() == backup_bytes
        assert hashlib.sha256(backup_bytes).hexdigest() == imported["backup_sha256"]
        assert backup.stat().st_mode & 0o222 == 0
        with sqlite3.connect(tmp_path / "governance.db") as database:
            assert database.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0] == 3
            assert database.execute("SELECT COUNT(*) FROM cp_executed_tools").fetchone()[0] == 2
            assert (
                database.execute("SELECT COUNT(*) FROM cp_retained_forward WHERE status='committed'").fetchone()[0] == 2
            )
            assert database.execute("SELECT COUNT(*) FROM trust_grants WHERE session_id='tf-job'").fetchone()[0] == 0
            assert database.execute("SELECT COUNT(*) FROM cp_decision_outbox WHERE job_id='cp-job'").fetchone()[0] == 1
        async with host._session_factory() as session:
            assert await EventRepository(session).get(cp_receipt.id) == cp_receipt
        importer.require_verified()
    finally:
        for owned in reversed(roots):
            await owned.close()


async def _retained_root(host, root, directory, roots):
    blocked = await _cli("quiesce")
    await root.close()
    await _cli("select-provider", "retained", "--generation", str(blocked["generation"]))
    return await _restart(host, root, directory, roots)


@pytest.mark.parametrize("stage", ["before-provider", "after-phase-one", "after-provider"])
async def test_partial_retained_accounting_reconciles_without_repeating_business_work(
    composed_runtime, tmp_path, monkeypatch, stage
):
    from types import SimpleNamespace

    host, _, root, create = composed_runtime
    roots: list[StandaloneGovernance] = []
    monkeypatch.setattr(backend_config, "get_codeplane_dir", lambda: tmp_path)
    try:
        root = await _retained_root(host, root, tmp_path, roots)
        await create("tf-job")
        original = root._providers.run_forward

        def inject():
            def interrupted(receipt_id, write):
                if stage == "before-provider":
                    raise OSError("injected before provider write")
                if stage == "after-provider":
                    write()
                    raise OSError("injected after provider write")
                provider = root._retained._decider
                pipeline = provider._pipeline_for("tf-job")

                def unavailable(*args):
                    raise OSError("injected after durable phase one")

                monkeypatch.setattr(pipeline._monitor, "_assessor", SimpleNamespace(assess=unavailable))
                original(receipt_id, write)

            monkeypatch.setattr(root._providers, "run_forward", interrupted)

        _, receipt = await _execute_read(host, root, "tf-job", tmp_path, retained=True, before_observe=inject)
        assert root.accounting_error is not None
        assert len(root._providers.pending()) == 1
        await root.close()
        blocked = StandaloneGovernance(host, tmp_path)
        with pytest.raises(StateConflictError, match="partial progress"):
            await blocked.start()
        with monkeypatch.context() as no_business:
            no_business.setattr(
                asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("Business call repeated")
            )
            assert (await _cli("reconcile-retained"))["reconciled_receipts"] == 1
        root = await _restart(host, root, tmp_path, roots)
        await root.observe(receipt)
        assert root.accounting_error is None
        assert (await root._ledger.job_totals("tf-job")).executed_calls == 1
        with sqlite3.connect(tmp_path / "governance.db") as db:
            assert db.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0] == 1
            assert db.execute("SELECT total_tool_calls FROM session_state WHERE session_id='tf-job'").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM cp_retained_forward WHERE status='committed'").fetchone()[0] == 1
        assert root._providers.pending() == []
    finally:
        for owned in reversed(roots):
            await owned.close()


@pytest.mark.parametrize(
    "change",
    ["dimensional-drift", "removed-forward-head", "dropped-source-tables", "conflicting-canonical-receipt"],
)
async def test_unowned_retained_changes_never_activate_a_replacement_writer(
    composed_runtime, tmp_path, monkeypatch, change
):
    host, _, root, create = composed_runtime
    roots: list[StandaloneGovernance] = []
    monkeypatch.setattr(backend_config, "get_codeplane_dir", lambda: tmp_path)
    try:
        root = await _retained_root(host, root, tmp_path, roots)
        await create("tf-job")
        _, receipt = await _execute_read(host, root, "tf-job", tmp_path, retained=True)
        if change == "conflicting-canonical-receipt":
            await root.observe(receipt.model_copy(update={"payload": {**receipt.payload, "result": "altered"}}))
            assert root.accounting_error is not None
            assert (await root._ledger.job_totals("tf-job")).executed_calls == 1
        else:
            await root.close()
            with sqlite3.connect(tmp_path / "governance.db") as db:
                if change == "dropped-source-tables":
                    for name in ("session_state", "budget_counters", "processed_events", "trust_grants"):
                        db.execute(f"DROP TABLE {name}")
                else:
                    db.execute(
                        "UPDATE budget_counters SET count=count+1 WHERE session_id='tf-job'"
                        if change == "dimensional-drift"
                        else "DELETE FROM cp_retained_head"
                    )
            blocked = StandaloneGovernance(host, tmp_path)
            with pytest.raises(StateConflictError, match="drift|incomplete"):
                await blocked.start()
    finally:
        for owned in reversed(roots):
            await owned.close()


@pytest.mark.parametrize("conflict", ["stale-generation", "active-job", "conflicting-pin"])
async def test_provider_selection_refuses_stale_or_undrained_state(composed_runtime, tmp_path, monkeypatch, conflict):
    host, _, root, create = composed_runtime
    monkeypatch.setattr(backend_config, "get_codeplane_dir", lambda: tmp_path)
    job = await create()
    if conflict == "active-job":
        async with host._session_factory() as session:
            assert await JobRepository(session).claim_for_start(job.id)
            await session.commit()
    if conflict == "conflicting-pin":
        from backend.models.execution_contracts import ExecutionContractPin
        from backend.persistence.execution_contract_repo import ExecutionContractRepository
        from backend.services.runtime.contract_catalog import retained_governance_contract

        pinned = await host.prepare_execution_contract(job.id, "first", recovery=False)
        await ExecutionContractRepository(host._session_factory).create(
            ExecutionContractPin(
                job_id=job.id,
                admission_id="conflicting",
                budget_id=job.id,
                contract=retained_governance_contract(pinned.contract),
            )
        )
    state = await _cli("quiesce")
    if conflict == "stale-generation":
        await _cli("quiesce")
    await root.close()
    before = _rows(tmp_path / "data.db", "jobs", "events")
    result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        ["governance-transition", "select-provider", "retained", "--generation", str(state["generation"])],
    )
    assert result.exit_code != 0
    assert {
        "stale-generation": "generation",
        "active-job": "active Jobs",
        "conflicting-pin": "implementation pins",
    }[conflict] in result.output
    assert _rows(tmp_path / "data.db", *before) == before


@pytest.mark.parametrize("defect", ["wrong-version", "missing-files"])
async def test_unavailable_optional_retained_distribution_does_not_break_standalone(
    composed_runtime, tmp_path, monkeypatch, defect
):
    from types import SimpleNamespace

    from backend.services.runtime import contract_catalog

    host, _, root, create = composed_runtime
    roots: list[StandaloneGovernance] = []
    actual = contract_catalog.distribution

    def distribution(name):
        if name == "traceforge-toolkit":
            return SimpleNamespace(version="0.0.0" if defect == "wrong-version" else "0.1.5", files=[])
        return actual(name)

    await root.close()
    monkeypatch.setattr(contract_catalog, "distribution", distribution)
    try:
        root = await _restart(host, root, tmp_path, roots)
        await create("standalone-job")
        pin = await host.prepare_execution_contract("standalone-job", "admission", recovery=False)
        assert pin.contract.policy.id == "cp-policy-v1"
        assert not root._retained_available
    finally:
        for owned in reversed(roots):
            await owned.close()


@pytest.mark.parametrize("selected", [True, False])
async def test_unavailable_selected_or_preserved_retained_pin_never_falls_back(
    composed_runtime, tmp_path, monkeypatch, selected
):
    from backend.services.action_policy import production

    host, _, root, create = composed_runtime
    roots: list[StandaloneGovernance] = []
    monkeypatch.setattr(backend_config, "get_codeplane_dir", lambda: tmp_path)
    try:
        root = await _retained_root(host, root, tmp_path, roots)
        job = await create("retained-job")
        pin = await host.prepare_execution_contract(job.id, "retained-admission", recovery=False)
        assert pin.contract.policy.id == "tf-policy-v1"
        if not selected:
            state = await _cli("quiesce")
            await root.close()
            await _cli("select-provider", "standalone", "--generation", str(state["generation"]))
        await root.close()

        def unavailable(*args):
            raise ValueError("retained implementation is unavailable")

        monkeypatch.setattr(production, "retained_governance_contract", unavailable)
        if selected:
            failed = StandaloneGovernance(host, tmp_path)
            with pytest.raises(StateConflictError, match="unavailable"):
                await failed.start()
        else:
            root = await _restart(host, root, tmp_path, roots)
            with pytest.raises(StateConflictError, match="unavailable"):
                await host.prepare_execution_contract(job.id, "retained-admission", recovery=True)
            assert root._providers.owner(job.id) == pin.contract
    finally:
        for owned in reversed(roots):
            await owned.close()


async def test_incompatible_native_admission_does_not_persist_provider_pin_or_start_witness(
    composed_runtime, tmp_path, monkeypatch
):
    host, _, root, create = composed_runtime
    roots: list[StandaloneGovernance] = []
    monkeypatch.setattr(backend_config, "get_codeplane_dir", lambda: tmp_path)
    try:
        root = await _retained_root(host, root, tmp_path, roots)
        job = await create("native-incompatible")
        before = _rows(tmp_path / "data.db", "jobs", "events")
        with pytest.raises(StateConflictError, match="cp-policy-v1"):
            await host.start_native(job.id, family="flowise", target=object(), invoke=lambda: None)
        assert root._providers.owner(job.id) is None
        assert _rows(tmp_path / "data.db", "jobs", "events") == before
        _, _ = await _execute_read(host, root, job.id, tmp_path, retained=True)
        assert root._providers.owner(job.id).policy.id == "tf-policy-v1"
    finally:
        for owned in reversed(roots):
            await owned.close()
