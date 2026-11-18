import pytest

from backend.models.domain import StateConflictError
from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.ingest.acquisition_checkpoint import AcquisitionCheckpoint
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.mapping import PARSER_REVISION, NativeMappingDecoder
from backend.services.ingest.record_service import ObservationRecords
from backend.tests.unit.test_approval_service import session_factory as session_factory


async def test_acquisition_state_and_provenance_commit_with_records_and_exact_retry(session_factory):
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="acquired",
            project_id="proj-1",
            format_id="copilot",
            external_session_id="native",
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, EventBus())
        batch = ObservationRecordBatch(
            source_key="sqlite",
            start_index=0,
            records=[{"type": "assistant.message", "data": {"content": "same text"}}],
        )
        acquisition = AcquisitionCheckpoint(
            source_identity_sha256="a" * 64,
            expected_checkpoint_version=0,
            state={"last_row": 1},
        )
        first = await service.append("acquired", batch, acquisition=acquisition)
        assert (await bindings.checkpoint("acquired", "sqlite", PARSER_REVISION)).buffer == {
            "acquisition": acquisition.saved(),
        }
        assert (await service.append("acquired", batch, acquisition=acquisition)).replay
        next_batch = batch.model_copy(update={"start_index": 1, "prefix_sha256": first.prefix_sha256})
        next_acquisition = acquisition.model_copy(update={"expected_checkpoint_version": 1, "state": {"last_row": 2}})
        await service.append("acquired", next_batch, acquisition=next_acquisition)
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        messages = [event for event in events if event.kind == "message.assistant"]
        assert len(messages) == 2 and messages[0].id != messages[1].id
        receipts = [event for event in events if event.kind == "integration.batch.accepted"]
        assert [event.raw_event["acquisition"]["state"]["last_row"] for event in receipts] == [1, 2]


@pytest.mark.parametrize("kind", ["missing", "source", "version"])
async def test_acquisition_ownership_is_checked_before_decode_without_erasing_progress(
    session_factory,
    monkeypatch,
    kind,
):
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="acquired",
            project_id="proj-1",
            format_id="copilot",
            external_session_id="native",
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, EventBus())
        first = await service.append(
            "acquired",
            ObservationRecordBatch(source_key="sse", start_index=0, records=[{"type": "session.start"}]),
            acquisition=AcquisitionCheckpoint(
                source_identity_sha256="a" * 64,
                expected_checkpoint_version=0,
                state={"last_event_id": "1"},
            ),
        )
        before = await bindings.checkpoint("acquired", "sse", PARSER_REVISION)

        def unexpected(*args):
            raise AssertionError("Invalid acquisition reached decoding")

        monkeypatch.setattr(NativeMappingDecoder, "decode", unexpected)
        acquisition = (
            None
            if kind == "missing"
            else AcquisitionCheckpoint(
                source_identity_sha256=("b" if kind == "source" else "a") * 64,
                expected_checkpoint_version=0 if kind == "version" else 1,
                state={"last_event_id": "2"},
            )
        )
        with pytest.raises(StateConflictError):
            await service.append(
                "acquired",
                ObservationRecordBatch(
                    source_key="sse",
                    start_index=1,
                    prefix_sha256=first.prefix_sha256,
                    records=[{"type": "session.start"}],
                ),
                acquisition=acquisition,
            )
        assert await bindings.checkpoint("acquired", "sse", PARSER_REVISION) == before
