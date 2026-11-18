"""Atomic operator imports with original records retained on batch receipts.

The receipt's versioned raw_event envelope retains physical inputs even when
preprocessing emits nothing or tool pairing consumes an intermediate event.
It is readable as operator history, not published as a producer event.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from backend.models.domain import JobNotFoundError, StateConflictError
from backend.models.events import TRANSCRIPT_KINDS, SessionEvent
from backend.models.schemas.integrations import ObservationRecordBatch, ObservationRecordReceipt
from backend.models.session_event import EventMetadata
from backend.services.events.activity_projection import ActivityProjection
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.events.observation import (
    OBSERVATION_NAMESPACE,
    OBSERVATION_NOTIFICATIONS,
    is_observation_history,
)
from backend.services.events.tool_pairing import PairingCheckpoint, ToolPairing
from backend.services.ingest.mapping import (
    PARSER_REVISION,
    DecoderState,
    NativeMappingDecoder,
    canonical_record,
    resolve_mapping_path,
)
from backend.services.ingest.otel_decoder import decode_json_body
from backend.services.steps.tracker import StepTracker

if TYPE_CHECKING:
    from backend.persistence.integration_repo import IntegrationRepository
    from backend.persistence.job_repo import JobRepository


def _advance(prefix: bytes, value: bytes) -> bytes:
    return hashlib.sha256(prefix + len(value).to_bytes(8, "big") + value).digest()


def _projection_digest(decoder: NativeMappingDecoder) -> str:
    sources = {
        cls.__name__: hashlib.sha256(Path(inspect.getfile(cls)).read_bytes()).hexdigest()
        for cls in (ToolPairing, StepTracker, ActivityProjection, EventProcessor, ObservationRecords)
    }
    return hashlib.sha256(canonical_record({"decoder": decoder.mapping_digest, "projection": sources})).hexdigest()


class ObservationRecords:
    def __init__(self, bindings: IntegrationRepository, jobs: JobRepository, event_bus: EventBus) -> None:
        self._bindings = bindings
        self._jobs = jobs
        self._event_bus = event_bus

    async def append(self, binding_id: str, batch: ObservationRecordBatch) -> ObservationRecordReceipt:
        # Also applies decoder structural limits to programmatic/in-process calls.
        decode_json_body(batch.model_dump_json().encode("utf-8"))
        batch = batch.model_copy(deep=True)
        binding = await self._bindings.get(binding_id)
        if binding is None or binding.mode != "observation" or binding.job_id is None:
            raise StateConflictError("No observation source is registered for this binding")
        job = await self._jobs.get(binding.job_id)
        if job is None:
            raise JobNotFoundError("Observation Job no longer exists")
        if not job.observation_only or job.integration_binding_id != binding.id:
            raise StateConflictError("Binding does not own non-executing observation history")
        format_id = binding.config.get("format")
        external_id = binding.config.get("external_session_id")
        if not isinstance(format_id, str) or not isinstance(external_id, str):
            raise StateConflictError("Source configuration is incomplete")
        path = resolve_mapping_path(format_id)
        if path is None:
            raise StateConflictError("Configured mapping is unavailable")
        decoder = NativeMappingDecoder(
            path,
            job_id=job.id,
            external_session_id=external_id,
            binding_id=binding.id,
            anchor=job.created_at,
            framework_id=format_id,
            source_key=batch.source_key,
        )
        checkpoint = await self._bindings.checkpoint(binding_id, batch.source_key, PARSER_REVISION)
        prefix = bytes.fromhex(batch.prefix_sha256)
        for raw in batch.records:
            prefix = _advance(prefix, canonical_record(raw))
        next_index = batch.start_index + len(batch.records)
        receipt_identity = [
            "codeplane-ingest-receipt-v1",
            job.id,
            binding.id,
            batch.source_key,
            batch.start_index,
            batch.prefix_sha256,
            next_index,
            prefix.hex(),
        ]
        if batch.end_session:
            receipt_identity.append("end_session")
        receipt_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(receipt_identity, separators=(",", ":"))))
        accepted_batch = await self._bindings.accepted_batch(receipt_id)
        if accepted_batch is not None:
            if accepted_batch.session_id != job.id or accepted_batch.kind != "integration.batch.accepted":
                raise StateConflictError("Source receipt identity conflicts with retained history")
            return ObservationRecordReceipt.model_validate(accepted_batch.payload).model_copy(update={"replay": True})
        projection = await self._bindings.projection(binding.id)
        projection_digest = _projection_digest(decoder)
        if projection is None:
            if await self._bindings.has_binding_checkpoints(binding.id):
                raise StateConflictError("Existing per-source projections require explicit reconciliation")
        else:
            if projection.state != "accepted":
                raise StateConflictError("Binding projection requires reconciliation")
            if projection.parser_revision != PARSER_REVISION or projection.parser_sha256 != projection_digest:
                await self._bindings.require_projection_reconciliation(
                    binding.id,
                    "projection_changed",
                    expected_version=projection.version,
                )
                raise StateConflictError("Binding projection implementation changed")
            if projection.buffer.get("closed"):
                raise StateConflictError("The registered source session has ended")
        if checkpoint is not None:
            if checkpoint.state != "accepted":
                raise StateConflictError("Source requires reconciliation before parser progress")
            if checkpoint.parser_sha256 != decoder.mapping_digest:
                await self._bindings.require_reconciliation(
                    binding.id,
                    batch.source_key,
                    PARSER_REVISION,
                    "parser_changed",
                    expected_version=checkpoint.version,
                    projection_version=projection.version if projection else None,
                )
                raise StateConflictError("Source requires reconciliation before parser progress")
            if checkpoint.record_count != batch.start_index or checkpoint.prefix_sha256 != batch.prefix_sha256:
                await self._bindings.require_reconciliation(
                    binding.id,
                    batch.source_key,
                    PARSER_REVISION,
                    "prefix_changed",
                    expected_version=checkpoint.version,
                    projection_version=projection.version if projection else None,
                )
                raise StateConflictError("Source prefix or record position changed")
        elif await self._bindings.has_checkpoint(binding.id, batch.source_key):
            raise StateConflictError("Parser migration requires accepted-prefix and buffer comparison")
        elif batch.start_index != 0 or batch.prefix_sha256 != "0" * 64:
            raise StateConflictError("New sources must begin at a provable empty prefix")

        staged: list[SessionEvent] = []
        staged_bytes = 0
        staging_bus = EventBus()

        def scoped(event: SessionEvent, *, acquired: bool = False) -> SessionEvent:
            if event.session_id != job.id or event.metadata.integration_binding_id not in (None, binding.id):
                raise StateConflictError("Observation event belongs to another source owner")
            if not acquired and is_observation_history(event):
                return event
            return event.model_copy(
                update={
                    "metadata": event.metadata.model_copy(
                        update={
                            "integration_binding_id": binding.id,
                            "namespace": (OBSERVATION_NAMESPACE, batch.source_key, *(event.metadata.namespace or ())),
                        }
                    )
                }
            )

        async def collect(event: SessionEvent) -> None:
            nonlocal staged_bytes
            event = scoped(event)
            staged_bytes += len(event.model_dump_json().encode("utf-8"))
            if staged_bytes > 8 * 1024 * 1024:
                raise ValueError("Observation projection exceeds its bounded event output")
            staged.append(event.model_copy(deep=True))

        staging_bus.subscribe(collect, required=True)
        pairing = ToolPairing()
        tracker = StepTracker(
            staging_bus,
            source_timestamps=True,
            metadata=EventMetadata(
                integration_binding_id=binding.id,
                run_id=external_id,
                namespace=(OBSERVATION_NAMESPACE,),
            ),
        )
        activities = ActivityProjection(staging_bus)
        owner = {"binding_id": binding.id, "job_id": job.id, "external_session_id": external_id}
        if projection is not None:
            saved = projection.buffer
            if saved.get("version") != 1 or saved.get("owner") != owner:
                raise StateConflictError("Binding projection ownership or format changed")
            decoder.restore(
                DecoderState(
                    saved["decoder"]["intent"],
                    saved["decoder"]["reasoning"],
                    tuple(saved["decoder"]["references"]),
                    saved["decoder"]["turn"],
                    saved["decoder"].get("active_turn"),
                    saved["decoder"].get("preprocessor"),
                )
            )
            pending = tuple(SessionEvent.model_validate(item) for item in saved["pairing"])
            if any(
                event.session_id != job.id
                or event.metadata.integration_binding_id != binding.id
                or event.metadata.run_id != external_id
                for event in pending
            ):
                raise StateConflictError("Source buffer ownership changed")
            pairing.restore(PairingCheckpoint(pending))
            tracker.restore_snapshot(job.id, saved["steps"])
            activities.restore(saved["activities"])
        processor = EventProcessor(
            staging_bus,
            step_tracker=tracker,
            enricher=pairing,
            activity_projection=activities,
            retain_source_events=True,
            fail_on_enrichment_error=True,
        )
        # Complete validation/normalization precedes any durable event or checkpoint write.
        normalized = []
        normalized_bytes = 0
        canonical_prefix = bytes.fromhex(checkpoint.canonical_sha256) if checkpoint else bytes(32)
        for offset, raw in enumerate(batch.records):
            for event in decoder.decode(raw, batch.start_index + offset):
                event = scoped(event, acquired=True)
                normalized_bytes += len(event.model_dump_json().encode("utf-8"))
                if normalized_bytes > 8 * 1024 * 1024:
                    raise ValueError("Observation batch exceeds its normalized event bound")
                normalized.append(event)
                canonical_prefix = _advance(canonical_prefix, canonical_record(event.model_dump(mode="json")))
        closed = False
        for event in normalized:
            if closed and event.kind in TRANSCRIPT_KINDS:
                raise ValueError("Transcript records follow the source session termination")
            tracker.observe_source_time(job.id, event)
            await processor.process_event(job.id, event)
            if event.kind == "session.ended":
                await processor.on_job_terminal(job.id, "source_ended")
                await activities.close(job.id, event.timestamp)
                closed = True
        if batch.end_session and not closed:
            await processor.on_job_terminal(job.id, "source_ended")
            await activities.close(job.id, tracker.source_end_timestamp(job.id) or job.created_at)
            closed = True
        buffer = {
            "version": 1,
            "owner": owner,
            "closed": closed,
            "decoder": asdict(decoder.checkpoint()),
            "pairing": [event.model_dump(mode="json") for event in pairing.checkpoint().starts],
            "steps": tracker.snapshot(job.id),
            "activities": activities.snapshot(),
        }
        encoded = json.dumps(buffer, ensure_ascii=False)
        total_bytes = len(encoded.encode("utf-8")) + sum(
            len(event.model_dump_json().encode("utf-8")) for event in staged
        )
        projection_prefix = bytes.fromhex(projection.canonical_sha256) if projection else bytes(32)
        for event in staged:
            projection_prefix = _advance(projection_prefix, canonical_record(event.model_dump(mode="json")))
        receipt = ObservationRecordReceipt(
            accepted_records=len(batch.records),
            next_index=next_index,
            prefix_sha256=prefix.hex(),
            checkpoint_version=checkpoint.version + 1 if checkpoint else 1,
            projection_version=projection.version + 1 if projection else 1,
            session_closed=closed,
        )
        # Receipts make an older accepted batch replayable after later progress.
        # They are durable history, not transcript input or control-plane commands.
        receipt_event = SessionEvent(
            id=receipt_id,
            session_id=job.id,
            timestamp=datetime.now(UTC),
            kind="integration.batch.accepted",
            payload=receipt.model_dump(mode="json"),
            raw_event={
                "contract": "codeplane-source-batch-v1",
                "source_key": batch.source_key,
                "start_index": batch.start_index,
                "prefix_sha256": batch.prefix_sha256,
                "records": batch.records,
            },
            metadata=EventMetadata(
                integration_binding_id=binding.id,
                namespace=(OBSERVATION_NAMESPACE, batch.source_key),
            ),
        )
        total_bytes += len(receipt_event.model_dump_json().encode("utf-8"))
        if total_bytes > 8 * 1024 * 1024:
            raise ValueError("Observation projection exceeds its bounded buffer")
        await self._bindings.commit_records(
            binding_id=binding.id,
            source_key=batch.source_key,
            revision=PARSER_REVISION,
            expected_version=checkpoint.version if checkpoint else 0,
            record_count=next_index,
            prefix_sha256=prefix.hex(),
            canonical_sha256=canonical_prefix.hex(),
            parser_sha256=decoder.mapping_digest,
            buffer={},
            events=[*staged, receipt_event],
            projection_version=projection.version if projection else 0,
            projection_parser_sha256=projection_digest,
            projection_canonical_sha256=projection_prefix.hex(),
            projection_buffer=buffer,
        )
        # Imported control-looking records are history, never control-plane commands.
        for event in staged:
            if event.kind in OBSERVATION_NOTIFICATIONS:
                await self._event_bus.publish(event)
        return receipt
