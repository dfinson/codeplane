"""Final package reads conversion receipts but never executes the retired parser."""

import hashlib
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from backend.models.domain import StateConflictError
from backend.services.ingest.checkpoint_conversion import conversion_evidence, convert_native_checkpoint
from backend.services.ingest.mapping import canonical_record


def _checkpoint():
    evidence = {
        "contract": "codeplane-native-conversion-v1",
        "original_checkpoint": {"version": 1, "record_count": 3},
        "converted_checkpoint": {"version": 2, "record_count": 3},
        "history_sha256": "a" * 64,
    }
    return {
        "version": 2,
        "record_count": 5,
        "conversion": {
            "status": "converted",
            "sha256": hashlib.sha256(canonical_record(evidence)).hexdigest(),
            "evidence": evidence,
        },
    }


def test_original_conversion_proof_remains_readable_after_new_reader_progress():
    checkpoint = _checkpoint()
    before = deepcopy(checkpoint)
    result = conversion_evidence(checkpoint)
    assert result == checkpoint["conversion"]
    result["evidence"]["original_checkpoint"]["record_count"] = 99
    assert checkpoint == before


@pytest.mark.parametrize("defect", ["digest", "status", "nested"])
def test_malformed_conversion_proof_requires_reconciliation(defect):
    checkpoint = _checkpoint()
    if defect == "digest":
        checkpoint["conversion"]["evidence"]["history_sha256"] = "b" * 64
    elif defect == "status":
        checkpoint["conversion"]["status"] = "accepted-without-proof"
    else:
        evidence = checkpoint["conversion"]["evidence"]
        evidence["original_checkpoint"]["conversion"] = {}
        checkpoint["conversion"]["sha256"] = hashlib.sha256(canonical_record(evidence)).hexdigest()
    with pytest.raises(StateConflictError):
        conversion_evidence(checkpoint)


@pytest.mark.parametrize("rollback", [False, True])
async def test_old_parser_conversion_is_explicitly_retired_before_any_storage_access(tmp_path, rollback):
    factory = MagicMock(side_effect=AssertionError("Final conversion must not access or modify storage"))
    with pytest.raises(StateConflictError, match="transition release 287483a7"):
        await convert_native_checkpoint(factory, job_id="old-job", source_path=tmp_path / "source", rollback=rollback)
    factory.assert_not_called()
