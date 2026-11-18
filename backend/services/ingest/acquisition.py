"""Deliver bounded acquired records through the existing canonical import path.

Snapshots contain stable completed records: an accepted identity may not change
or disappear. A complete journal is re-read and diffed by physical record
position. Mutable/provisional producer data must remain unaccepted until its
source contract supplies stable identity/content. These helpers are deliberately
not schedulers, provider wrappers, or launch/credential admission mechanisms.
Pages and retained identity inventories are bounded. Exhaustion fails explicitly;
identities are never evicted to make a later replay look like fresh input.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from backend.models.domain import StateConflictError
from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.services.ingest.acquisition_checkpoint import AcquisitionCheckpoint
from backend.services.ingest.acquisition_sources import MAX_BYTES, MAX_RECORDS, AcquiredPage, QueueSource
from backend.services.ingest.mapping import PARSER_REVISION, canonical_record
from backend.services.ingest.otel_decoder import decode_json_body
from backend.services.ingest.record_service import ObservationRecords

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.schemas.integrations import ObservationRecordReceipt
    from backend.services.events.event_bus import EventBus


class AcquisitionChannel:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        *,
        binding_id: str,
        source_key: str,
    ) -> None:
        if not source_key or len(source_key) > 128:
            raise ValueError("Acquisition source key must contain between 1 and 128 characters")
        self._sessions = session_factory
        self._bus = event_bus
        self.binding_id = binding_id
        self.source_key = source_key

    async def last_event_id(self) -> str | None:
        async with self._sessions() as session:
            checkpoint = await IntegrationRepository(session).checkpoint(
                self.binding_id, self.source_key, PARSER_REVISION
            )
        if checkpoint is None:
            return None
        if checkpoint.state != "accepted":
            raise StateConflictError("Acquisition source requires reconciliation")
        acquisition = checkpoint.buffer.get("acquisition")
        if not isinstance(acquisition, dict):
            raise StateConflictError("Acquisition checkpoint requires explicit reconciliation")
        state = acquisition.get("state")
        if not isinstance(state, dict) or state.get("contract") != "native-acquisition-records-v1":
            raise StateConflictError("Acquisition state contract requires explicit reconciliation")
        value = state.get("last_event_id")
        if value is not None and not isinstance(value, str):
            raise StateConflictError("Invalid durable SSE reconnect identity")
        return value

    async def accept(self, page: AcquiredPage) -> ObservationRecordReceipt | None:
        """Return None only for a valid duplicate/no-change acquisition."""
        page = deepcopy(page)
        if len(page.records) > MAX_RECORDS:
            raise ValueError("Acquisition exceeds 1000 records")
        async with self._sessions() as session:
            bindings = IntegrationRepository(session)
            binding = await bindings.get(self.binding_id)
            if binding is None or binding.mode != "observation" or binding.job_id is None:
                raise StateConflictError("Acquisition requires an existing observation binding")
            checkpoint = await bindings.checkpoint(self.binding_id, self.source_key, PARSER_REVISION)
            prior: dict[str, Any] | None = checkpoint.buffer.get("acquisition") if checkpoint else None

            async def reject(reason: str, message: str) -> None:
                if checkpoint is not None:
                    projection = await bindings.projection(self.binding_id)
                    await bindings.require_reconciliation(
                        self.binding_id,
                        self.source_key,
                        PARSER_REVISION,
                        reason,
                        expected_version=checkpoint.version,
                        projection_version=projection.version if projection else None,
                    )
                raise StateConflictError(message)

            if checkpoint is not None:
                if checkpoint.state != "accepted":
                    raise StateConflictError("Acquisition source requires reconciliation")
                if not isinstance(prior, dict):
                    raise StateConflictError("Untracked source progress requires explicit acquisition reconciliation")
                if prior.get("version") != 1 or prior.get("source_identity_sha256") != page.source_digest:
                    await reject("acquisition_source_changed", "Acquisition source identity or version changed")
                prior_state = prior.get("state")
                if (
                    not isinstance(prior_state, dict)
                    or prior_state.get("contract") != "native-acquisition-records-v1"
                    or not isinstance(prior_state.get("records"), list)
                ):
                    raise StateConflictError("Acquisition state contract requires explicit reconciliation")
            inventory: list[dict[str, Any]] = deepcopy(prior["state"]["records"]) if prior else []
            known = {item["identity"]: item for item in inventory}
            encountered = set()
            new_records = []
            total = 0
            for record in page.records:
                if not isinstance(record.identity, str) or not record.identity or len(record.identity) > 512:
                    raise ValueError("Acquisition requires a bounded stable record identity")
                if record.sequence is not None and (
                    isinstance(record.sequence, bool) or not isinstance(record.sequence, (str, int))
                ):
                    raise ValueError("Invalid native acquisition sequence")
                if not isinstance(record.raw, dict):
                    raise ValueError("Acquisition payload must be an object")
                encoded = canonical_record(record.raw)
                total += len(encoded)
                if total > MAX_BYTES:
                    raise ValueError("Acquisition exceeds 1 MiB of raw records")
                decode_json_body(encoded)
                item = {
                    "identity": record.identity,
                    "sequence": record.sequence,
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                }
                previous = known.get(record.identity)
                if previous is not None and previous != item:
                    await reject(
                        "acquisition_record_rewritten", "Accepted acquisition record was rewritten or resequenced"
                    )
                if record.identity in encountered and page.mode == "snapshot":
                    raise ValueError("Snapshot contains duplicate record identity")
                encountered.add(record.identity)
                if previous is None:
                    inventory.append(item)
                    known[record.identity] = item
                    new_records.append(record.raw)
            if page.mode == "snapshot" and any(item["identity"] not in encountered for item in inventory):
                await reject("acquisition_snapshot_truncated", "Snapshot truncated previously accepted records")
            prior_physical = prior["state"].get("physical_count") if prior else None
            if page.physical_count is not None and prior_physical is not None and page.physical_count < prior_physical:
                await reject("acquisition_snapshot_truncated", "Snapshot physical record count was truncated")
            if len(inventory) > MAX_RECORDS:
                raise ValueError("Acquisition identity inventory exceeds its 1000-record bound")
            if not new_records:
                return None
            state: dict[str, Any] = {
                "contract": "native-acquisition-records-v1",
                "records": inventory,
                "physical_count": page.physical_count,
                "incomplete_tail": page.incomplete_tail,
            }
            if page.source.get("transport") == "sse":
                state["last_event_id"] = page.records[-1].identity
            acquisition = AcquisitionCheckpoint(
                source_identity_sha256=page.source_digest,
                expected_checkpoint_version=checkpoint.version if checkpoint else 0,
                state=state,
            )
            batch = ObservationRecordBatch(
                source_key=self.source_key,
                start_index=checkpoint.record_count if checkpoint else 0,
                prefix_sha256=checkpoint.prefix_sha256 if checkpoint else "0" * 64,
                records=new_records,
            )
            return await ObservationRecords(bindings, JobRepository(session), self._bus).append(
                self.binding_id, batch, acquisition=acquisition
            )

    async def accept_queue(self, source: QueueSource) -> ObservationRecordReceipt | None:
        async with source.delivery_lock:
            page = await source.read()
            receipt = await self.accept(page)
            source.acknowledge()
            return receipt

    async def finish(self) -> ObservationRecordReceipt | None:
        """Explicit producer completion, never implied by polling or HTTP EOF."""
        async with self._sessions() as session:
            bindings = IntegrationRepository(session)
            checkpoint = await bindings.checkpoint(self.binding_id, self.source_key, PARSER_REVISION)
            saved = checkpoint.buffer.get("acquisition") if checkpoint else None
            if not isinstance(saved, dict) or checkpoint is None:
                raise StateConflictError("Cannot finish an acquisition without an accepted source checkpoint")
            state = deepcopy(saved["state"])
            if state.get("closed"):
                return None
            state["closed"] = True
            acquisition = AcquisitionCheckpoint(
                version=saved["version"],
                source_identity_sha256=saved["source_identity_sha256"],
                expected_checkpoint_version=checkpoint.version,
                state=state,
            )
            return await ObservationRecords(bindings, JobRepository(session), self._bus).append(
                self.binding_id,
                ObservationRecordBatch(
                    source_key=self.source_key,
                    start_index=checkpoint.record_count,
                    prefix_sha256=checkpoint.prefix_sha256,
                    records=[],
                    end_session=True,
                ),
                acquisition=acquisition,
            )
