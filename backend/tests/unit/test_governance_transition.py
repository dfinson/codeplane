import json
import sqlite3
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from backend import config as backend_config
from backend.cli import cli
from backend.models.domain import StateConflictError
from backend.persistence.governance_ownership import GovernanceRuntimeLease
from backend.persistence.governance_repo import GovernanceLedger
from backend.persistence.legacy_governance_repo import LEGACY_POLICY, LegacyGovernanceRepository
from backend.services.action_policy.classifier import Preset
from backend.tests.legacy_fixtures import seed_legacy_store
from backend.tests.unit.test_approval_service import session_factory as session_factory


@pytest.fixture
def populated(tmp_path):
    with sqlite3.connect(tmp_path / "data.db") as app:
        app.executescript("""
            CREATE TABLE jobs(id TEXT PRIMARY KEY, observation_only INTEGER);
            INSERT INTO jobs VALUES('legacy-job',0);
            CREATE TABLE job_telemetry_summary(job_id TEXT, session_kind TEXT, total_cost_usd REAL);
            INSERT INTO job_telemetry_summary VALUES('legacy-job','job',1.25);
            INSERT INTO job_telemetry_summary VALUES('legacy-job','sidecar',100.0);
        """)
    decider = seed_legacy_store(tmp_path / "governance.db")
    try:
        yield tmp_path, decider
    finally:
        decider.close()


def importer(directory):
    return LegacyGovernanceRepository(directory / "governance.db", directory / "data.db")


def test_captured_retained_data_import_compares_provenance_and_retains_read_only_backup(populated):
    directory, decider = populated
    original = decider._store.connection.execute("SELECT session_meta_json FROM processed_events").fetchone()[0]
    with pytest.raises(StateConflictError, match="verified import"):
        importer(directory).require_verified()
    with GovernanceRuntimeLease(directory, exclusive=True) as lease:
        result = importer(directory).import_original(lease)
        again = importer(directory).import_original(lease)
    assert result["status"] == "imported" and again["status"] == "already_imported"
    assert result["source_digest"] == again["source_digest"]
    backup = Path(result["backup_path"])
    assert stat.S_IMODE(backup.stat().st_mode) & 0o222 == 0
    with sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True) as retained:
        assert retained.execute("SELECT session_meta_json FROM processed_events").fetchone()[0] == original
    importer(directory).require_verified()
    with sqlite3.connect(directory / "governance.db") as current:
        assert (
            current.execute("SELECT COUNT(*) FROM cp_legacy_rows WHERE table_name='processed_events'").fetchone()[0]
            == 1
        )
        grant = current.execute("SELECT policy_id,reason_key FROM cp_legacy_grants").fetchone()
        assert grant == (LEGACY_POLICY, "readonly_shell_locked")
        assert current.execute("SELECT session_meta_json FROM processed_events").fetchone()[0] == original


async def test_imported_budget_unknown_currency_and_policy_scope_do_not_become_new_receipts(populated):
    directory, _ = populated
    with GovernanceRuntimeLease(directory, exclusive=True) as lease:
        importer(directory).import_original(lease)
    ledger = GovernanceLedger(directory / "governance.db", application_database=directory / "data.db")
    await ledger.initialize()
    try:
        totals = await ledger.job_totals("legacy-job")
        assert totals.executed_calls == 1 and totals.legacy_estimated_usd == 1.25
        assert not totals.verified_usd_present and totals.legacy_currency_unverified
        assert totals.budget_accountable
        await ledger.require_policy_scope("legacy-job", LEGACY_POLICY)
        await ledger.require_policy_scope("new-unrelated-job", "cp-policy-v1")
        with pytest.raises(StateConflictError, match="unexpired legacy grant"):
            await ledger.require_policy_scope("legacy-job", "cp-policy-v1")
        with pytest.raises(ValueError, match="cannot be recounted"):
            await ledger.record_completion(
                job_id="legacy-job",
                epoch="new-epoch",
                event_id="replayed",
                tool_call_id="old-call",
                source_id="new-session",
                action_digest="unproven",
                executed=True,
                succeeded=True,
                destructive=False,
            )
        await ledger.record_completion(
            job_id="new-job",
            epoch="new-epoch",
            event_id="new-event",
            tool_call_id="new-call",
            source_id="new-session",
            action_digest="new-action",
            executed=True,
            succeeded=False,
            destructive=True,
        )
        with GovernanceRuntimeLease(directory, exclusive=True) as lease:
            assert importer(directory).import_original(lease)["status"] == "already_imported"
        assert (await ledger.job_totals("new-job")).executed_calls == 1
        with sqlite3.connect(directory / "governance.db") as database:
            assert database.execute("SELECT COUNT(*) FROM cp_executed_tools").fetchone()[0] == 1
    finally:
        await ledger.close()


@pytest.mark.parametrize("defect", ["reserved", "unknown-job", "counter-conflict", "wide-grant"])
def test_unmappable_originals_require_reconciliation_without_partial_import(populated, defect):
    directory, decider = populated
    with decider._store.write_transaction() as database:
        if defect == "reserved":
            database.execute("UPDATE processed_events SET session_meta_json=?", ('{"reserved":true}',))
        elif defect == "unknown-job":
            database.execute("UPDATE trust_grants SET session_id='unmapped'")
        elif defect == "counter-conflict":
            database.execute("UPDATE session_state SET total_tool_calls=2")
        else:
            database.execute("UPDATE trust_grants SET key='hard_gate'")
    with GovernanceRuntimeLease(directory, exclusive=True) as lease, pytest.raises(StateConflictError):
        importer(directory).import_original(lease)
    with sqlite3.connect(directory / "governance.db") as database:
        assert database.execute("SELECT name FROM sqlite_master WHERE name='cp_legacy_import'").fetchone() is None


def test_expiry_is_strict_without_extending_or_rewriting_original_grants(populated):
    directory, decider = populated
    now = datetime.now(UTC)
    with decider._store.write_transaction() as database:
        database.execute(
            "UPDATE trust_grants SET granted_at=?,ttl_seconds=60", ((now - timedelta(seconds=60)).isoformat(),)
        )
    with GovernanceRuntimeLease(directory, exclusive=True) as lease:
        result = importer(directory).import_original(lease, now=now)
    assert result["active_scoped_grants"] == 0
    with sqlite3.connect(directory / "governance.db") as database:
        assert database.execute("SELECT COUNT(*) FROM cp_legacy_grants").fetchone()[0] == 0
        assert (
            database.execute("SELECT COUNT(*) FROM cp_legacy_rows WHERE table_name='trust_grants'").fetchone()[0] == 1
        )


def test_original_and_projection_changes_prevent_writer_activation(populated):
    directory, _ = populated
    with GovernanceRuntimeLease(directory, exclusive=True) as lease:
        importer(directory).import_original(lease)
    with sqlite3.connect(directory / "governance.db") as database:
        database.execute("UPDATE cp_legacy_budget SET executed_calls=9")
    with pytest.raises(StateConflictError, match="projections changed"):
        importer(directory).require_verified()


@pytest.mark.parametrize("change", ["clear", "drop"])
def test_import_evidence_never_becomes_pristine_when_original_tables_disappear(populated, change):
    directory, _ = populated
    with GovernanceRuntimeLease(directory, exclusive=True) as lease:
        importer(directory).import_original(lease)
    with sqlite3.connect(directory / "governance.db") as database:
        for table in ("session_state", "budget_counters", "processed_events", "trust_grants"):
            database.execute(f"{'DELETE FROM' if change == 'clear' else 'DROP TABLE'} {table}")
    with pytest.raises(StateConflictError, match="reconciliation|incomplete"):
        importer(directory).require_verified()


@pytest.mark.parametrize("source", ["missing", "empty-sqlite", "empty-tf"])
def test_fresh_unimported_source_does_not_require_a_fabricated_import(tmp_path, source):
    database = tmp_path / "governance.db"
    if source == "empty-sqlite":
        with sqlite3.connect(database):
            pass
    elif source == "empty-tf":
        original = seed_legacy_store(database, rows=False)
        original.close()
    importer(tmp_path).require_verified()
    assert not (tmp_path / "governance-backups").exists()
    if source == "missing":
        assert not database.exists()


@pytest.mark.parametrize("estimate", [None, 1.25])
async def test_actual_authority_abstains_from_unknown_usd_but_retains_execution_completeness(
    tmp_path, session_factory, estimate
):
    from backend.models.domain import ApprovalResolution
    from backend.models.events import EventKind
    from backend.services.action_policy.agt_policy import PolicyInput
    from backend.services.adapters.contracts import ExecutionIdentity
    from backend.tests.unit.test_policy_authority import _services

    authority, pin, ledger, audit, approvals, events, provider = await _services(tmp_path, session_factory)
    try:
        with sqlite3.connect(tmp_path / "data.db") as application:
            application.executescript("""
                CREATE TABLE jobs(id TEXT PRIMARY KEY, observation_only INTEGER);
                INSERT INTO jobs VALUES('job-1',0);
                CREATE TABLE job_telemetry_summary(job_id TEXT, session_kind TEXT, total_cost_usd REAL);
            """)
            application.execute("INSERT INTO job_telemetry_summary VALUES('job-1','job',?)", (estimate,))
        retained = seed_legacy_store(tmp_path / "governance.db", job_id="job-1", grants=False)
        retained.close()
        with GovernanceRuntimeLease(tmp_path, exclusive=True) as lease:
            importer(tmp_path).import_original(lease)
        requested = []

        async def reject_unexpected_approval(event):
            if event.kind == EventKind.approval_requested:
                requested.append(event)
                await approvals.resolve(event.payload["approval_id"], ApprovalResolution.rejected)

        async def current():
            return True

        events.subscribe(reject_unexpected_approval)

        async def authorize():
            return await authority.authorize(
                pin=pin,
                execution=ExecutionIdentity("job-1", "managed", "epoch", "token"),
                local=PolicyInput("job-1", Preset.supervised, trusted_file_read=True),
                fields={"command": "", "arguments": '{"path":"file"}', "tool_definition": "read", "context": ""},
                is_current=current,
            )

        allowed = await authorize()
        assert allowed.proceed and allowed.decision.reason_code == "known_read"
        assert requested == []
        assert not (await ledger.job_totals("job-1")).verified_usd_present
        provider.complete.assert_not_awaited()
        assert await audit.deliver_one()
        budget = audit._entries(0)[-1].data["metadata"]["budget_snapshot"]
        assert budget["verified_usd"] is None
        assert budget["usd_guard_status"] == "abstained_unavailable"
        assert budget["legacy_estimated_usd"] == (str(estimate) if estimate is not None else None)
        await ledger.record_uncertainty("job-1", "epoch", "missing-id", "missing_tool_identity")
        unresolved = await authorize()
        assert not unresolved.proceed and unresolved.decision.reason_code == "incomplete_evidence"
        assert len(requested) == 1
    finally:
        await ledger.close()


def test_failed_comparison_rolls_back_import_but_retains_original_recovery_backup(populated, monkeypatch):
    directory, decider = populated
    before = decider._store.connection.execute("SELECT * FROM processed_events").fetchall()
    repository = importer(directory)

    def reject(*_):
        raise StateConflictError("Injected comparison failure")

    monkeypatch.setattr(repository, "_verify", reject)
    with (
        GovernanceRuntimeLease(directory, exclusive=True) as lease,
        pytest.raises(StateConflictError, match="comparison failure"),
    ):
        repository.import_original(lease)
    assert decider._store.connection.execute("SELECT * FROM processed_events").fetchall() == before
    with sqlite3.connect(directory / "governance.db") as database:
        assert database.execute("SELECT name FROM sqlite_master WHERE name='cp_legacy_import'").fetchone() is None
    (backup,) = (directory / "governance-backups").glob("original-*.db")
    assert stat.S_IMODE(backup.stat().st_mode) & 0o222 == 0
    with GovernanceRuntimeLease(directory, exclusive=True) as lease:
        assert importer(directory).import_original(lease)["status"] == "imported"


async def test_existing_replacement_outcomes_require_reconciliation_instead_of_double_count(populated):
    directory, _ = populated
    ledger = GovernanceLedger(directory / "governance.db", application_database=directory / "data.db")
    await ledger.initialize()
    try:
        await ledger.record_completion(
            job_id="legacy-job",
            epoch="replacement",
            event_id="new-event",
            tool_call_id="new-call",
            source_id="new-session",
            action_digest="new-action",
            executed=True,
            succeeded=True,
            destructive=False,
        )
        with (
            GovernanceRuntimeLease(directory, exclusive=True) as lease,
            pytest.raises(StateConflictError, match="overlap"),
        ):
            importer(directory).import_original(lease)
        assert (await ledger.job_totals("legacy-job")).executed_calls == 1
    finally:
        await ledger.close()


@pytest.mark.parametrize("missing", [False, True])
async def test_absent_or_null_legacy_spend_remains_unknown(populated, missing):
    directory, _ = populated
    with sqlite3.connect(directory / "data.db") as database:
        if missing:
            database.execute("DELETE FROM job_telemetry_summary WHERE session_kind='job'")
        else:
            database.execute("UPDATE job_telemetry_summary SET total_cost_usd=NULL WHERE session_kind='job'")
    with GovernanceRuntimeLease(directory, exclusive=True) as lease:
        importer(directory).import_original(lease)
    ledger = GovernanceLedger(directory / "governance.db", application_database=directory / "data.db")
    await ledger.initialize()
    try:
        totals = await ledger.job_totals("legacy-job")
        assert totals.legacy_estimated_usd is None and totals.legacy_currency_unverified
        assert not totals.verified_usd_present and totals.budget_accountable
    finally:
        await ledger.close()


def test_maintenance_and_runtime_ownership_exclude_each_other_without_deleting_lock(tmp_path):
    with (
        GovernanceRuntimeLease(tmp_path),
        GovernanceRuntimeLease(tmp_path),
        pytest.raises(StateConflictError, match="still active"),
    ):
        GovernanceRuntimeLease(tmp_path, exclusive=True).acquire()
    with GovernanceRuntimeLease(tmp_path, exclusive=True), pytest.raises(StateConflictError, match="still active"):
        GovernanceRuntimeLease(tmp_path).acquire()
    assert (tmp_path / ".governance-runtime.lock").is_file()
    with GovernanceRuntimeLease(tmp_path):
        pass


def test_runtime_ownership_is_real_across_processes(tmp_path):
    child = (
        "import sys; from pathlib import Path; "
        "from backend.persistence.governance_ownership import GovernanceRuntimeLease; "
        "lease=GovernanceRuntimeLease(Path(sys.argv[1]),exclusive=sys.argv[2]=='exclusive'); "
        "lease.acquire(); lease.close()"
    )
    with GovernanceRuntimeLease(tmp_path):
        shared = subprocess.run([sys.executable, "-c", child, str(tmp_path), "shared"], capture_output=True, timeout=10)
        assert shared.returncode == 0, shared.stderr.decode()
        result = subprocess.run(
            [sys.executable, "-c", child, str(tmp_path), "exclusive"], capture_output=True, timeout=10
        )
        assert result.returncode != 0 and b"still active" in result.stderr
    with GovernanceRuntimeLease(tmp_path, exclusive=True):
        result = subprocess.run([sys.executable, "-c", child, str(tmp_path), "shared"], capture_output=True, timeout=10)
        assert result.returncode != 0 and b"still active" in result.stderr
    result = subprocess.run([sys.executable, "-c", child, str(tmp_path), "exclusive"], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()


async def test_failed_activation_closes_owned_resources_without_starting_a_writer(populated):
    from backend.services.action_policy.production import StandaloneGovernance
    from backend.services.events.event_bus import EventBus

    directory, _ = populated
    root = StandaloneGovernance(SimpleNamespace(_event_bus=EventBus()), directory)
    with pytest.raises(StateConflictError, match="verified import"):
        await root.start()
    assert root._delivery is None
    with pytest.raises(RuntimeError, match="I/O is closed"):
        await root._ledger.require_admission()
    with GovernanceRuntimeLease(directory, exclusive=True):
        pass


def test_lazy_operator_import_command_uses_active_home_and_refuses_a_live_runtime(populated, monkeypatch):
    directory, _ = populated
    monkeypatch.setattr(backend_config, "get_codeplane_dir", lambda: directory)
    with GovernanceRuntimeLease(directory):
        blocked = CliRunner().invoke(cli, ["governance-transition", "import-legacy"])
        assert blocked.exit_code != 0 and "still active" in blocked.output
    imported = CliRunner().invoke(cli, ["governance-transition", "import-legacy"])
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.output)["status"] == "imported"
