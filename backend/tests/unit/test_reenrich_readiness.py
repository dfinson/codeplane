"""Default-core historical re-enrichment readiness without optional TF imports."""

import pytest

from backend.models.events import new_event
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.services.events.reenrich import LegacyEnrichmentUnavailableError, reenrich_job_events
from backend.tests.unit.test_approval_service import session_factory as session_factory


async def test_optional_reenrichment_readiness_precedes_forced_marker_deletion(session_factory, monkeypatch):
    import builtins

    original_import = builtins.__import__

    def no_legacy(name, *args, **kwargs):
        if name == "traceforge" or name.startswith("traceforge."):
            raise ModuleNotFoundError("legacy deliberately unavailable", name="traceforge")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_legacy)
    marker = new_event("job-1", "reenrich.complete", {"updated_count": 1})
    async with serialized_write(session_factory) as session:
        await EventRepository(session).append(marker)
    with pytest.raises(LegacyEnrichmentUnavailableError, match="legacy-traceforge"):
        await reenrich_job_events("job-1", session_factory, force=True)
    async with session_factory() as session:
        assert await EventRepository(session).get(marker.id) == marker
