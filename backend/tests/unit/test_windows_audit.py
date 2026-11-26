import os
import sys

import pytest

from backend.persistence.governance_repo import GovernanceLedger
from backend.services.action_policy.audit import GovernanceAudit
from backend.services.action_policy.windows_audit import protect


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows DPAPI and DACL qualification")
async def test_native_user_protected_key_restart_rotation_and_signed_delivery(tmp_path):
    ledger = GovernanceLedger(tmp_path / "governance.db", application_database=tmp_path / "data.db")
    await ledger.initialize()
    directory = tmp_path / "audit"
    audit = GovernanceAudit(directory, ledger)
    await audit.initialize()
    old_id = audit._active_key()
    encrypted = (directory / f"{old_id}.key").read_bytes()
    secret = audit._key(old_id)
    assert len(secret) == 32
    assert encrypted != secret and len(encrypted) > len(secret)
    assert protect(encrypted, decrypt=True) == secret
    await ledger.enqueue_decision(
        decision_id="first",
        job_id="job",
        epoch="epoch",
        action_digest="a" * 64,
        decision="allow",
        metadata={"contract": "native"},
    )
    assert await audit.deliver_one()
    prior = audit._entries(0)
    restored = GovernanceAudit(directory, ledger)
    await restored.initialize()
    assert restored._key(old_id) == secret
    new_id = await restored.rotate_key()
    assert new_id != old_id and restored._key(new_id) != secret
    await ledger.enqueue_decision(
        decision_id="second",
        job_id="job",
        epoch="epoch",
        action_digest="b" * 64,
        decision="reject",
        metadata={"contract": "native"},
    )
    assert await restored.deliver_one()
    assert restored._entries(1)[0].data["previous_segment_hash"] == prior[-1].content_hash
    assert (directory / f"{old_id}.key").read_bytes() == encrypted
    assert restored._entries(0) == prior


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows DPAPI tamper qualification")
def test_native_dpapi_rejects_modified_secret_without_plaintext_fallback():
    original = protect(os.urandom(32))
    changed = bytearray(original)
    changed[-1] ^= 0x80
    with pytest.raises(OSError, match="CryptUnprotectData"):
        protect(bytes(changed), decrypt=True)
