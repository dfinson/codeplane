"""Read retained conversion evidence; old-parser execution belongs to the transition release."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.domain import StateConflictError
from backend.services.ingest.mapping import canonical_record

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger()


def conversion_evidence(checkpoint: dict[str, Any]) -> dict[str, Any] | None:
    """Validate an immutable transition receipt without rerunning either parser."""
    record = checkpoint.get("conversion")
    if record is None:
        return None
    if not isinstance(record, dict):
        raise StateConflictError("Malformed native checkpoint conversion evidence")
    evidence = record.get("evidence")
    if (
        not isinstance(evidence, dict)
        or evidence.get("contract") != "codeplane-native-conversion-v1"
        or record.get("sha256") != hashlib.sha256(canonical_record(evidence)).hexdigest()
        or record.get("status") not in {"converted", "rolled_back"}
        or not isinstance(evidence.get("original_checkpoint"), dict)
        or not isinstance(evidence.get("converted_checkpoint"), dict)
        or "conversion" in evidence["original_checkpoint"]
        or "conversion" in evidence["converted_checkpoint"]
    ):
        raise StateConflictError("Native checkpoint conversion evidence requires reconciliation")
    return deepcopy(record)


async def convert_native_checkpoint(
    factory: async_sessionmaker[AsyncSession], *, job_id: str, source_path: Path, rollback: bool = False
) -> dict[str, Any]:
    """Refuse mutation in the final runtime, even if an old package happens to be installed."""
    log.warning("legacy_checkpoint_conversion_retired", job_id=job_id, rollback=rollback)
    raise StateConflictError(
        "Use preserved transition release 287483a7 for locked-prefix conversion or rollback. "
        "This final runtime only resumes reconciled version-2 checkpoints and never resets an old offset."
    )
