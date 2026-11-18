"""Deliver bounded acquired records through the existing canonical import path.

Snapshots contain stable completed records: an accepted identity may not change
or disappear. A complete journal is re-read and diffed by physical record
position. Mutable/provisional producer data must remain unaccepted until its
source contract supplies stable identity/content. These helpers are deliberately
not schedulers, provider wrappers, or launch/credential admission mechanisms.
Pages and checkpoint metadata are bounded independently of source lifetime.
Existing atomic receipts retain all accepted identities; lookup loads only the
current page's matches, never an ever-growing in-memory identity inventory.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from backend.models.domain import StateConflictError
from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.acquisition_receipt_repo import AcquisitionReceiptRepository
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
    from backend.services.ingest.mapping_registry import MappingRegistry

_STATE_CONTRACTS = {"native-acquisition-records-v1", "native-acquisition-records-v2"}


class AcquisitionChannel:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        *,
        binding_id: str,
        source_key: str,
        mapping_registry: MappingRegistry | None = None,
    ) -> None:
        if not source_key or len(source_key) > 128:
            raise ValueError("Acquisition source key must contain between 1 and 128 characters")
        self._sessions = session_factory
        self._bus = event_bus
        self.binding_id = binding_id
        self.source_key = source_key
        self._mapping_registry = mapping_registry

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
        if not isinstance(state, dict) or state.get("contract") not in _STATE_CONTRACTS:
            raise StateConflictError("Acquisition state contract requires explicit reconciliation")
        value = state.get("last_event_id")
        if value is not None and not isinstance(value, str):
            raise StateConflictError("Invalid durable SSE reconnect identity")
        return value

    async def accept(self, page: AcquiredPage, *, end_session: bool = False) -> ObservationRecordReceipt | None:
        """Return None only for a valid duplicate/no-change acquisition."""
        if len(page.records) > MAX_RECORDS:
            raise ValueError("Acquisition exceeds 1000 records")
        if end_session and page.incomplete_tail:
            raise ValueError("Final acquisition still has an incomplete tail")
        page = deepcopy(page)
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
                    or prior_state.get("contract") not in _STATE_CONTRACTS
                    or not isinstance(prior_state.get("records"), list)
                ):
                    raise StateConflictError("Acquisition state contract requires explicit reconciliation")
            for record in page.records:
                if not isinstance(record.identity, str) or not record.identity or len(record.identity) > 512:
                    raise ValueError("Acquisition requires a bounded stable record identity")
            receipts = AcquisitionReceiptRepository(
                session,
                job_id=binding.job_id,
                binding_id=self.binding_id,
                source_key=self.source_key,
                source_identity=page.source_digest,
            )
            accepted_count = await receipts.identity_count()
            if accepted_count != (checkpoint.record_count if checkpoint else 0):
                await reject("acquisition_history_incomplete", "Acquisition receipt history requires reconciliation")
            known = await receipts.lookup([record.identity for record in page.records])
            previously_seen = len(known)
            inventory: list[dict[str, Any]] = []
            encountered = set()
            new_records = []
            total = 0
            for record in page.records:
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
            if page.mode == "snapshot" and previously_seen != accepted_count:
                await reject("acquisition_snapshot_truncated", "Snapshot truncated previously accepted records")
            prior_physical = prior["state"].get("physical_count") if prior else None
            if (
                page.mode == "snapshot"
                and page.physical_count is not None
                and prior_physical is not None
                and page.physical_count < prior_physical
            ):
                await reject("acquisition_snapshot_truncated", "Snapshot physical record count was truncated")
            if not new_records and (not end_session or (prior and prior["state"].get("closed"))):
                return None
            state: dict[str, Any] = {
                "contract": "native-acquisition-records-v2",
                "records": inventory,
                "physical_count": page.physical_count,
                "incomplete_tail": page.incomplete_tail,
            }
            if page.source.get("transport") == "sse":
                committed_id = prior["state"].get("last_event_id") if prior else None
                state["last_event_id"] = page.records[-1].identity if page.records else committed_id
            if end_session:
                state["closed"] = True
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
                end_session=end_session,
            )
            return await ObservationRecords(
                bindings, JobRepository(session), self._bus, mapping_registry=self._mapping_registry
            ).append(self.binding_id, batch, acquisition=acquisition)

    async def accept_queue(self, source: QueueSource) -> ObservationRecordReceipt | None:
        async with source.delivery_lock:
            page = await source.read()
            receipt = await self.accept(page)
            source.acknowledge()
            return receipt

    async def finish(self, *, final_page: AcquiredPage | None = None) -> ObservationRecordReceipt | None:
        """Explicit completion; a supplied final page and closure commit atomically.

        Completion is never implied by polling or HTTP EOF.
        """
        if final_page is not None:
            return await self.accept(final_page, end_session=True)
        async with self._sessions() as session:
            bindings = IntegrationRepository(session)
            checkpoint = await bindings.checkpoint(self.binding_id, self.source_key, PARSER_REVISION)
            saved = checkpoint.buffer.get("acquisition") if checkpoint else None
            if not isinstance(saved, dict) or checkpoint is None:
                raise StateConflictError("Cannot finish an acquisition without an accepted source checkpoint")
            state = deepcopy(saved["state"])
            if state.get("closed"):
                return None
            state["contract"] = "native-acquisition-records-v2"
            state["records"] = []
            state["closed"] = True
            acquisition = AcquisitionCheckpoint(
                version=saved["version"],
                source_identity_sha256=saved["source_identity_sha256"],
                expected_checkpoint_version=checkpoint.version,
                state=state,
            )
            return await ObservationRecords(
                bindings, JobRepository(session), self._bus, mapping_registry=self._mapping_registry
            ).append(
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
