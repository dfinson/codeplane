import json

import pytest

from backend.persistence.governance_repo import GovernanceLedger
from backend.services.action_policy.audit import AuditIntegrityError, GovernanceAudit


async def _services(tmp_path):
    ledger = GovernanceLedger(tmp_path / "governance.db", application_database=tmp_path / "data.db")
    await ledger.initialize()
    audit = GovernanceAudit(tmp_path / "audit", ledger)
    await audit.initialize()
    return ledger, audit


async def _decision(ledger, identity):
    await ledger.enqueue_decision(
        decision_id=identity,
        job_id="job",
        epoch="epoch",
        action_digest="a" * 64,
        decision="deny",
        metadata={"policy_version": "cp-policy-v1", "approver_id": "operator"},
    )


async def test_actual_agt_audit_retains_signed_policy_action_and_key_bindings(tmp_path):
    ledger, audit = await _services(tmp_path)
    await _decision(ledger, "decision")
    assert await audit.deliver_one()
    assert not await audit.deliver_one()
    entries = audit._entries(0)
    assert len(entries) == 2
    assert entries[1].data["action_digest"] == "a" * 64
    assert entries[1].data["metadata"]["policy_version"] == "cp-policy-v1"
    assert entries[1].data["metadata"]["approver_id"] == "operator"
    assert entries[1].data["key_id"] == entries[0].data["key_id"]
    assert entries[1].previous_hash == entries[0].content_hash
    assert (tmp_path / "audit").stat().st_mode & 0o077 == 0
    assert all(path.stat().st_mode & 0o077 == 0 for path in (tmp_path / "audit").iterdir())


async def test_crash_between_audit_append_and_database_ack_does_not_duplicate_delivery(tmp_path):
    ledger, audit = await _services(tmp_path)
    await _decision(ledger, "decision")

    def append_then_fail(decision, head):
        audit._write(decision, head)
        raise OSError("Acknowledgment failed")

    with pytest.raises(OSError, match="Acknowledgment failed"):
        await ledger.deliver_audit(append_then_fail)
    assert len(audit._entries(0)) == 2
    recovered = GovernanceAudit(tmp_path / "audit", ledger)
    await recovered.initialize()
    assert await recovered.deliver_one()
    assert len(recovered._entries(0)) == 2
    assert not await recovered.deliver_one()


async def test_key_rotation_retains_old_keys_and_links_new_segment(tmp_path):
    ledger, audit = await _services(tmp_path)
    await _decision(ledger, "first")
    await audit.deliver_one()
    old = audit._entries(0)
    new_key = await audit.rotate_key()
    await _decision(ledger, "second")
    await audit.deliver_one()
    new = audit._entries(1)
    assert new[0].data["previous_segment_hash"] == old[-1].content_hash
    assert new[0].data["key_id"] == new_key != old[0].data["key_id"]
    assert len(list((tmp_path / "audit").glob("*.key"))) == 2
    assert audit._entries(0) == old


async def test_tampered_or_incomplete_audit_history_stops_delivery_without_overwrite(tmp_path):
    ledger, audit = await _services(tmp_path)
    await _decision(ledger, "first")
    await audit.deliver_one()
    path = audit._path(0)
    lines = path.read_text().splitlines()
    data = json.loads(lines[-1])
    data["data"]["decision"] = "allow"
    lines[-1] = json.dumps(data)
    tampered = "\n".join(lines) + "\n"
    path.write_text(tampered)
    await _decision(ledger, "second")
    with pytest.raises(AuditIntegrityError, match="verification"):
        await audit.deliver_one()
    assert path.read_text() == tampered
