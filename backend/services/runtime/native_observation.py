"""Source-fenced composition of canonical processing for an actual native Job."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.models.domain import StateConflictError
from backend.models.events import SessionEvent, new_event
from backend.models.session_event import EventMetadata
from backend.services.adapters.native_gates.observation_scope import NativeObservationOwner
from backend.services.events.activity_projection import ActivityProjection
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.events.observation import OBSERVATION_NAMESPACE, OBSERVATION_NOTIFICATIONS
from backend.services.events.tool_pairing import ToolPairing
from backend.services.ingest.mapping import (
    PARSER_REVISION,
    NativeMappingDecoder,
    canonical_record,
    resolve_mapping_path,
)
from backend.services.ingest.otel_decoder import decode_json_body
from backend.services.ingest.record_service import _advance, _projection_digest
from backend.services.runtime.contract_catalog import _pin
from backend.services.steps.tracker import StepTracker

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.services.runtime.native_execution import NativeExecution, NativeExecutions


async def open_native_observation(
    registry: NativeExecutions, execution: NativeExecution, format_id: str, external_session_id: str
) -> NativeObservationOwner:
    host = registry.host
    identity = execution.identity
    if format_id != execution.gates.binding.family or not external_session_id or len(external_session_id) > 512:
        raise StateConflictError("Native observation format/source must match its actual execution family")
    for existing in execution.observation_sources:
        if existing.family == format_id and existing.source_id == external_session_id:
            await existing.require_registration(format_id)
            return existing
    source_digest = hashlib.sha256(f"{format_id}:{external_session_id}".encode()).hexdigest()[:24]
    binding_id = f"native-observation-{identity.execution_token}-{source_digest}"
    path = resolve_mapping_path(format_id)
    if path is None:
        raise StateConflictError("Native observation mapping is unavailable")
    selected = next((item for item in execution.pin.contract.mappings if item.id == format_id), None)
    if selected != _pin(format_id, [(format_id, path)]):
        raise StateConflictError("Native observation mapping differs from the original admission pin")
    owner: NativeObservationOwner
    index = version = 0
    prefix = canonical_prefix = projection_prefix = bytes(32)
    registered = closed = failed = False
    staged: list[SessionEvent] = []
    staged_bytes = 0

    def scoped(event: SessionEvent, *, output: bool) -> SessionEvent:
        if event.session_id != identity.job_id:
            raise StateConflictError("Native observation belongs to another Job")
        namespace = event.metadata.namespace or ()
        if not namespace or namespace[0] != OBSERVATION_NAMESPACE:
            namespace = (OBSERVATION_NAMESPACE, "native-callbacks", *namespace)
        metadata = event.metadata.model_copy(
            update={
                "source_framework": format_id,
                "integration_binding_id": binding_id,
                "run_id": event.metadata.run_id or external_session_id,
                "namespace": namespace,
                "execution_epoch": identity.epoch,
                "execution_token": identity.execution_token,
                "recipe_run_id": execution.metadata.recipe_run_id if output else None,
                "recipe_revision": execution.metadata.recipe_revision if output else None,
                "step_instance_id": execution.metadata.step_instance_id if output else None,
                "attempt_id": execution.metadata.attempt_id if output else None,
            }
        )
        payload = dict(event.payload)
        for key in ("agent_id", "task_id", "crew_id"):
            native = (event.raw_event or {}).get(key)
            if isinstance(native, str):
                payload.setdefault(key, native)
        return event.model_copy(update={"metadata": metadata, "payload": payload})

    async def collect(event: SessionEvent) -> None:
        nonlocal staged_bytes
        retained = scoped(event, output=True)
        staged_bytes += len(retained.model_dump_json().encode())
        if staged_bytes > 8 * 1024 * 1024:
            raise ValueError("Native observation projection exceeds its bounded output")
        staged.append(retained)

    staging_bus = EventBus()
    staging_bus.subscribe(collect, required=True)
    tracker = StepTracker(
        staging_bus,
        source_timestamps=True,
        metadata=EventMetadata(
            source_framework=format_id,
            integration_binding_id=binding_id,
            run_id=external_session_id,
            namespace=(OBSERVATION_NAMESPACE,),
            execution_epoch=identity.epoch,
            execution_token=identity.execution_token,
        ),
    )
    activities = ActivityProjection(staging_bus)
    pairing = ToolPairing()
    processor = EventProcessor(
        staging_bus,
        step_tracker=tracker,
        activity_projection=activities,
        enricher=pairing,
        retain_source_events=True,
        fail_on_enrichment_error=True,
    )
    decoder: NativeMappingDecoder
    projection_digest: str

    async def append(raw: Mapping[str, Any] | None) -> tuple[SessionEvent, ...]:
        nonlocal index, version, prefix, canonical_prefix, projection_prefix, staged_bytes, closed, failed
        if closed or failed:
            raise StateConflictError("Native observation source has ended or requires reconciliation")
        await owner.assert_retained()
        staged.clear()
        staged_bytes = 0
        next_prefix, next_canonical = prefix, canonical_prefix
        try:
            records = [dict(raw)] if raw is not None else []
            if raw is not None:
                encoded = canonical_record(dict(raw))
                decode_json_body(encoded)
                next_prefix = _advance(prefix, encoded)
                for event in decoder.decode(dict(raw), index):
                    event = scoped(event, output=False)
                    next_canonical = _advance(next_canonical, canonical_record(event.model_dump(mode="json")))
                    tracker.observe_source_time(identity.job_id, event)
                    await processor.process_event(identity.job_id, event)
            else:
                await processor.on_job_terminal(identity.job_id, "source_ended")
                await activities.close(identity.job_id, tracker.source_end_timestamp(identity.job_id) or job.created_at)
            next_projection = projection_prefix
            for event in staged:
                next_projection = _advance(next_projection, canonical_record(event.model_dump(mode="json")))
            buffer = {
                "version": 1,
                "owner": {
                    "binding_id": binding_id,
                    "job_id": identity.job_id,
                    "external_session_id": external_session_id,
                },
                "closed": raw is None,
                "decoder": asdict(decoder.checkpoint()),
                "pairing": [event.model_dump(mode="json") for event in pairing.checkpoint().starts],
                "steps": tracker.snapshot(identity.job_id),
                "activities": activities.snapshot(),
            }
            receipt = new_event(
                identity.job_id,
                "integration.batch.accepted",
                {
                    "accepted_records": len(records),
                    "next_index": index + len(records),
                    "prefix_sha256": next_prefix.hex(),
                    "checkpoint_version": version + 1,
                    "projection_version": version + 1,
                    "session_closed": raw is None,
                },
                event_id=f"{binding_id}:{version + 1}",
                raw_event={
                    "contract": "codeplane-source-batch-v1",
                    "source_key": "native-callbacks",
                    "start_index": index,
                    "prefix_sha256": prefix.hex(),
                    "records": records,
                },
                metadata=EventMetadata(
                    source_framework=format_id,
                    integration_binding_id=binding_id,
                    run_id=external_session_id,
                    namespace=(OBSERVATION_NAMESPACE, "native-callbacks"),
                    execution_epoch=identity.epoch,
                    execution_token=identity.execution_token,
                ),
            )
            size = staged_bytes + len(receipt.model_dump_json().encode()) + len(canonical_record(buffer))
            if size > 8 * 1024 * 1024:
                raise ValueError("Native observation batch exceeds its bounded buffer")
            accepted = tuple(staged)
            await registry._repository.commit_observation(
                identity,
                execution.pin,
                binding_id=binding_id,
                assert_retained=owner.assert_retained,
                closed=raw is None,
                commit={
                    "source_key": "native-callbacks",
                    "revision": PARSER_REVISION,
                    "expected_version": version,
                    "record_count": index + len(records),
                    "prefix_sha256": next_prefix.hex(),
                    "canonical_sha256": next_canonical.hex(),
                    "parser_sha256": decoder.mapping_digest,
                    "buffer": {},
                    "events": [*accepted, receipt],
                    "projection_version": version,
                    "projection_parser_sha256": projection_digest,
                    "projection_canonical_sha256": next_projection.hex(),
                    "projection_buffer": buffer,
                },
            )
            index += len(records)
            version += 1
            prefix, canonical_prefix, projection_prefix = next_prefix, next_canonical, next_projection
            closed = raw is None
            for event in accepted:
                if event.kind in OBSERVATION_NOTIFICATIONS:
                    await host._event_bus.publish(event)
            return accepted
        except BaseException:
            failed = True
            raise

    async def finish() -> None:
        try:
            if registered and not closed and not failed:
                await append(None)
        finally:
            processor.cleanup(identity.job_id)
            if registered:
                await registry._repository.retire_observation(identity, binding_id)

    owner = NativeObservationOwner(
        registry=registry,
        execution=execution,
        source_id=external_session_id,
        append=append,
        finish=finish,
    )
    await owner.require_registration(format_id)
    job = await registry._repository.bind_observation(
        identity,
        execution.pin,
        binding_id=binding_id,
        family=format_id,
        external_session_id=external_session_id,
        assert_retained=owner.assert_retained,
    )
    registered = True
    try:
        decoder = NativeMappingDecoder(
            path,
            job_id=job.id,
            external_session_id=external_session_id,
            binding_id=binding_id,
            anchor=job.created_at,
            framework_id=format_id,
            source_key="native-callbacks",
        )
        if selected != _pin(format_id, [(format_id, path)]):
            raise StateConflictError("Native observation mapping changed during source creation")
        projection_digest = hashlib.sha256(
            f"{_projection_digest(decoder)}:{hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}".encode()
        ).hexdigest()
        await owner.require_registration(format_id)
    except BaseException:
        failed = True
        await finish()
        raise
    execution.observation_sources.append(owner)
    return owner
