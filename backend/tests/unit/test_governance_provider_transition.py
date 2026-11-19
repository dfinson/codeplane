"""Final-runtime refusal and data retention; executable rollback is qualified in release287."""

import sqlite3
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from backend.cli import cli
from backend.models.domain import StateConflictError
from backend.models.execution_contracts import ImplementationPin
from backend.services.action_policy.production import StandaloneGovernance
from backend.tests.unit.test_approval_service import session_factory as session_factory
from backend.tests.unit.test_standalone_root import recovery_runtime as recovery_runtime


async def test_final_runtime_does_not_discover_or_construct_retained_policy(recovery_runtime):
    host, root, registry_adapter, _, _, _ = recovery_runtime
    assert not hasattr(root, "_retained")
    assert not hasattr(host, "_governance_decider")
    assert registry_adapter._sessions == {}


async def test_preserved_old_job_pin_cannot_be_replaced_by_final_runtime(recovery_runtime):
    host, root, adapter, _, _, _ = recovery_runtime
    current = host._job_contract_pins["job-1"]
    legacy = current.contract.model_copy(
        update={
            "id": "cp-retained-governance-v1",
            "policy": ImplementationPin(id="tf-policy-v1", sha256="a" * 64),
        }
    )
    root._providers.register_job("job-2", legacy)
    with pytest.raises(StateConflictError, match="reconciliation"):
        await host.prepare_execution_contract("job-2", "must-not-repin", recovery=False)
    assert root._providers.owner("job-2") == legacy
    assert adapter._sessions == {}


async def test_persisted_retained_selection_fails_startup_without_optional_discovery(recovery_runtime):
    from backend.services.runtime import contract_catalog

    host, root, _, _, _, workspace = recovery_runtime
    legacy = host._job_contract_pins["job-1"].contract.model_copy(
        update={
            "id": "cp-retained-governance-v1",
            "policy": ImplementationPin(id="tf-policy-v1", sha256="a" * 64),
        }
    )
    await root.close()
    with sqlite3.connect(root._ledger.path) as database:
        database.execute(
            "INSERT INTO cp_provider_selection VALUES(1,?,1,'retained-evidence')", (legacy.model_dump_json(),)
        )
    replacement = StandaloneGovernance(host, workspace.parent)
    original = contract_catalog._distribution

    def available(name, version):
        assert name != "traceforge-toolkit", "Final startup must not discover the retired distribution"
        return original(name, version)

    with (
        patch.object(contract_catalog, "_distribution", side_effect=available),
        pytest.raises(StateConflictError, match="transition release 287483a7"),
    ):
        await replacement.start()
    with sqlite3.connect(root._ledger.path) as database:
        assert (
            database.execute("SELECT contract_json FROM cp_provider_selection").fetchone()[0]
            == legacy.model_dump_json()
        )


def test_final_cli_has_no_retained_execution_or_recovery_switch():
    selection = CliRunner().invoke(cli, ["governance-transition", "select-provider", "retained", "--generation", "0"])
    assert selection.exit_code != 0 and "not 'standalone'" in selection.output
    recovery = CliRunner().invoke(cli, ["governance-transition", "reconcile-retained"])
    assert recovery.exit_code != 0 and "transition release 287483a7" in recovery.output
    assert "install TF" in recovery.output
