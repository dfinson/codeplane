"""Provider selection and attributable retained writes in the compatible ledger."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.models.db import EventRow
from backend.models.domain import StateConflictError
from backend.models.execution_contracts import ExecutionContract, ExecutionContractPin
from backend.persistence.event_repo import EventRepository
from backend.persistence.execution_contract_repo import _identity
from backend.persistence.legacy_governance_repo import LEGACY_POLICY, _digest, _encoded, _rows, _timestamp

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from backend.persistence.governance_ownership import GovernanceRuntimeLease


def _initialize(connection: sqlite3.Connection) -> None:
    for sql in (
        """CREATE TABLE IF NOT EXISTS cp_provider_selection (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), contract_json TEXT NOT NULL,
            generation INTEGER NOT NULL, selected_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS cp_provider_jobs (
            job_id TEXT PRIMARY KEY, contract_json TEXT NOT NULL, registered_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS cp_retained_forward (
            receipt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            source_key TEXT NOT NULL UNIQUE, input_json TEXT NOT NULL, before_json TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL, after_json TEXT,
            status TEXT NOT NULL CHECK(status IN ('prepared','committed')))""",
        """CREATE TABLE IF NOT EXISTS cp_retained_head (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), rows_sha256 TEXT NOT NULL)""",
    ):
        connection.execute(sql)


def _protected(rows: dict[str, list[dict[str, Any]]], job_id: str, source_key: str) -> str:
    return _digest(
        {
            table: [
                row
                for row in values
                if not (table in {"session_state", "budget_counters"} and row["session_id"] == job_id)
                and not (table == "processed_events" and row["source_event_key"] == source_key)
            ]
            for table, values in rows.items()
        }
    )


def _projection(rows: dict[str, list[dict[str, Any]]], job_id: str) -> dict[str, Any]:
    states = [dict(row) for row in rows["session_state"] if row["session_id"] == job_id]
    for row in states:
        _timestamp(row.pop("updated_at"))
    return {
        "session_state": states,
        "budget_counters": [row for row in rows["budget_counters"] if row["session_id"] == job_id],
    }


def _checked_receipt(row: sqlite3.Row) -> tuple[dict[str, Any], dict[str, Any]]:
    source, before = json.loads(row["input_json"]), json.loads(row["before_json"])
    if (
        _digest({"source": source, "before": before}) != row["receipt_sha256"]
        or _digest(source["completion"]) != row["source_sha256"]
    ):
        raise StateConflictError("Prepared retained receipt changed")
    return source, before


class GovernanceProviderRepository:
    def __init__(self, path: Path, application_database: Path) -> None:
        self.path = path.resolve()
        self.application_database = application_database.resolve()
        if self.path == self.application_database or (
            self.path.exists() and self.application_database.exists() and self.path.samefile(self.application_database)
        ):
            raise ValueError("Governance and application databases must be distinct")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            _initialize(connection)

    def selected(self, default: ExecutionContract) -> ExecutionContract:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT contract_json FROM cp_provider_selection WHERE singleton=1").fetchone()
            return ExecutionContract.model_validate_json(row[0]) if row else default

    def register_job(self, job_id: str, contract: ExecutionContract) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            retained = connection.execute(
                "SELECT contract_json FROM cp_provider_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if retained is not None:
                if ExecutionContract.model_validate_json(retained[0]) != contract:
                    raise StateConflictError("An existing Job cannot change its registered governance provider")
                return
            connection.execute(
                "INSERT INTO cp_provider_jobs VALUES(?,?,?)",
                (job_id, contract.model_dump_json(), datetime.now(UTC).isoformat()),
            )

    @staticmethod
    def _prior_contract(app: sqlite3.Connection, job_id: str) -> ExecutionContract | None:
        pins = []
        for event in app.execute(
            "SELECT event_id,payload,event_metadata FROM events WHERE job_id=? AND kind='execution.contract.pinned'",
            (job_id,),
        ):
            metadata = json.loads(event["event_metadata"])
            if (
                metadata.get("source_framework") != "codeplane"
                or metadata.get("namespace") != ["control", "execution-contract"]
                or metadata.get("integration_binding_id") is not None
            ):
                continue
            pin = ExecutionContractPin.model_validate_json(event["payload"])
            if (
                pin.job_id != job_id
                or pin.budget_id != job_id
                or event["event_id"] != _identity(job_id, pin.admission_id)
            ):
                raise StateConflictError("Existing Job pin ownership requires reconciliation")
            pins.append(pin.contract)
        if pins and any(pin != pins[0] for pin in pins):
            raise StateConflictError("Existing Job has conflicting implementation pins")
        return pins[0] if pins else None

    def owner(self, job_id: str) -> ExecutionContract | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT contract_json FROM cp_provider_jobs WHERE job_id=?", (job_id,)).fetchone()
            return ExecutionContract.model_validate_json(row[0]) if row is not None else None

    def for_job(self, job_id: str, default: ExecutionContract) -> ExecutionContract:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            retained = connection.execute(
                "SELECT contract_json FROM cp_provider_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if retained is not None:
                return ExecutionContract.model_validate_json(retained[0])
            gate = connection.execute("SELECT blocked FROM cp_admission_control WHERE singleton=1").fetchone()
            if gate is None or gate[0]:
                raise StateConflictError("New governance admission is blocked for provider transition")
            selected = connection.execute(
                "SELECT contract_json FROM cp_provider_selection WHERE singleton=1"
            ).fetchone()
            contract = ExecutionContract.model_validate_json(selected[0]) if selected else default
            connection.execute(
                "INSERT INTO cp_provider_jobs VALUES(?,?,?)",
                (job_id, contract.model_dump_json(), datetime.now(UTC).isoformat()),
            )
            return contract

    def select_new_jobs(
        self,
        lease: GovernanceRuntimeLease,
        *,
        target: ExecutionContract,
        default: ExecutionContract,
        available: Mapping[str, ExecutionContract],
        expected_generation: int,
    ) -> dict[str, Any]:
        from backend.persistence.legacy_governance_repo import LegacyGovernanceRepository

        lease.require_exclusive()
        if lease.directory != self.path.parent or available.get(target.id) != target:
            raise StateConflictError("Provider selection requires the exact home and available implementation")
        with (
            closing(sqlite3.connect(self.application_database.as_uri() + "?mode=ro", uri=True)) as app,
            closing(self._connect()) as connection,
            connection,
        ):
            app.row_factory = sqlite3.Row
            app.execute("BEGIN")
            jobs = app.execute("SELECT id,state FROM jobs WHERE observation_only=0 ORDER BY id").fetchall()
            active = [row["id"] for row in jobs if row["state"] in {"running", "waiting_for_approval"}]
            if active:
                raise StateConflictError(f"Drain, cancel or explicitly reconcile active Jobs first: {active}")
            unresolved = app.execute(
                """SELECT event_id FROM events WHERE kind='execution.native.bound' AND NOT EXISTS (
                    SELECT 1 FROM events AS ended
                    WHERE ended.event_id=REPLACE(events.event_id,':bound',':released')) LIMIT 1"""
            ).fetchone()
            if unresolved is not None:
                raise StateConflictError("Native execution ownership requires reconciliation before provider selection")
            connection.execute("BEGIN IMMEDIATE")
            LegacyGovernanceRepository(self.path, self.application_database).require_verified()
            control = connection.execute(
                "SELECT generation,blocked FROM cp_admission_control WHERE singleton=1"
            ).fetchone()
            if control is None or control["generation"] != expected_generation or not control["blocked"]:
                raise StateConflictError("Provider selection requires the current quiesced admission generation")
            pending = connection.execute(
                "SELECT receipt_id FROM cp_retained_forward WHERE status='prepared'"
            ).fetchone()
            if pending is not None:
                raise StateConflictError("A retained provider receipt requires explicit reconciliation")
            previous = connection.execute(
                "SELECT contract_json FROM cp_provider_selection WHERE singleton=1"
            ).fetchone()
            previous_contract = ExecutionContract.model_validate_json(previous[0]) if previous else default
            now = datetime.now(UTC).isoformat()
            for job in jobs:
                retained = connection.execute(
                    "SELECT contract_json FROM cp_provider_jobs WHERE job_id=?", (job["id"],)
                ).fetchone()
                prior = self._prior_contract(app, job["id"])
                if retained is not None:
                    if prior is not None and ExecutionContract.model_validate_json(retained[0]) != prior:
                        raise StateConflictError("Registered Job and existing implementation pins disagree")
                    continue
                preserved = prior or previous_contract
                connection.execute(
                    "INSERT INTO cp_provider_jobs VALUES(?,?,?)", (job["id"], preserved.model_dump_json(), now)
                )
            lease.require_exclusive()
            connection.execute(
                """INSERT INTO cp_provider_selection VALUES(1,?,?,?)
                   ON CONFLICT(singleton) DO UPDATE SET contract_json=excluded.contract_json,
                   generation=excluded.generation,selected_at=excluded.selected_at""",
                (target.model_dump_json(), expected_generation + 1, now),
            )
            connection.execute(
                "UPDATE cp_admission_control SET generation=generation+1,blocked=0,reason=? WHERE singleton=1",
                (f"New Jobs select {target.id}; existing Job providers retained",),
            )
            return {
                "selected_for_new_jobs": target.id,
                "generation": expected_generation + 1,
                "preserved_jobs": len(jobs),
            }

    def verify_source(self, connection: sqlite3.Connection, source: dict[str, Any]) -> ExecutionContract:
        from backend.persistence.legacy_governance_repo import LegacyGovernanceRepository

        LegacyGovernanceRepository.verify_original_import(connection)
        event = source["event"]
        job_id = event["session_id"]
        with closing(sqlite3.connect(self.application_database.as_uri() + "?mode=ro", uri=True)) as app:
            app.row_factory = sqlite3.Row
            row = app.execute("SELECT * FROM events WHERE event_id=?", (event["id"],)).fetchone()
            if row is None:
                raise StateConflictError("Retained outcome has no persisted canonical receipt")
            stored = EventRepository._to_domain(
                EventRow(
                    event_id=row["event_id"],
                    job_id=row["job_id"],
                    kind=row["kind"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    payload=row["payload"],
                    event_metadata=row["event_metadata"],
                    raw_event=row["raw_event"],
                )
            )
            if _digest(stored.model_dump(mode="json")) != _digest(event):
                raise StateConflictError("Retained canonical receipt changed")
        md = stored.metadata
        if md.integration_binding_id is not None or not md.execution_epoch or not md.execution_token:
            raise StateConflictError("Retained outcome has no managed execution ownership")
        call_id: object
        if stored.kind == "execution.command.result":
            if md.source_framework != "codeplane.recipe" or not md.attempt_id or not md.recipe_run_id:
                raise StateConflictError("Retained command ownership is incomplete")
            if stored.payload["result"].get("started") is not True:
                raise StateConflictError("Retained command execution is unconfirmed")
            call_id = md.execution_token
        else:
            if (
                stored.kind not in {"tool.call.completed", "tool.call.failed"}
                or md.source_framework not in {"copilot", "claude"}
                or not md.run_id
                or (stored.kind == "tool.call.failed" and stored.payload.get("executed") is not True)
            ):
                raise StateConflictError("Retained outcome is not an executed managed call")
            call_id = stored.payload.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise StateConflictError("Retained outcome has no exact call identity")
        owner = connection.execute("SELECT contract_json FROM cp_provider_jobs WHERE job_id=?", (job_id,)).fetchone()
        if owner is None:
            raise StateConflictError("Retained writes require a registered Job")
        contract = ExecutionContract.model_validate_json(owner[0])
        decisions = connection.execute(
            """SELECT decision,metadata_json FROM cp_decision_outbox WHERE job_id=? AND epoch=?
               AND json_extract(metadata_json,'$.execution.execution_token')=?
               AND json_extract(metadata_json,'$.tool_call_id')=?""",
            (job_id, md.execution_epoch, md.execution_token, call_id),
        ).fetchall()
        if len(decisions) != 1 or decisions[0]["decision"] != "allow":
            raise StateConflictError("Retained outcome requires one exact durable authorization")
        authorization = json.loads(decisions[0]["metadata_json"])
        if (
            contract.policy.id != LEGACY_POLICY
            or ExecutionContract.model_validate(authorization["contract"]) != contract
            or _digest(authorization.get("retained_action")) != _digest(source["action"])
            or authorization.get("preset") != source["preset"]
            or source["action"].get("job_id") != job_id
        ):
            raise StateConflictError("Retained provider, action or Job differs from its authorization")
        return contract

    @staticmethod
    def verify_forward_state(connection: sqlite3.Connection, rows: dict[str, list[dict[str, Any]]]) -> bool:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        journaled = (
            "cp_retained_forward" in tables
            and connection.execute("SELECT 1 FROM cp_retained_forward LIMIT 1").fetchone() is not None
        )
        if "cp_retained_head" not in tables:
            if journaled:
                raise StateConflictError("Retained forwarding evidence is incomplete")
            return False
        head = connection.execute("SELECT rows_sha256 FROM cp_retained_head WHERE singleton=1").fetchone()
        if head is None:
            if journaled:
                raise StateConflictError("Retained forwarding evidence is incomplete")
            return False
        if not journaled:
            raise StateConflictError("Retained forwarding head has no owned receipts")
        if connection.execute("SELECT 1 FROM cp_retained_forward WHERE status='prepared' LIMIT 1").fetchone():
            raise StateConflictError("Retained provider partial progress requires explicit reconciliation")
        if head[0] != _digest(rows):
            raise StateConflictError("Unknown governance writer or retained source drift requires reconciliation")
        latest = {}
        for receipt in connection.execute("SELECT * FROM cp_retained_forward ORDER BY rowid"):
            _checked_receipt(receipt)
            latest[receipt["job_id"]] = json.loads(receipt["after_json"])
        if any(_digest(_projection(rows, job_id)) != _digest(projection) for job_id, projection in latest.items()):
            raise StateConflictError("Retained projected counters differ from verified source state")
        return True

    def prepare_forward(
        self,
        *,
        receipt_id: str,
        job_id: str,
        source_key: str,
        source: dict[str, Any],
        expected: dict[str, Any],
    ) -> bool:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self.verify_source(connection, source)
            fingerprint = _digest(source["completion"])
            prior = connection.execute(
                "SELECT * FROM cp_retained_forward WHERE receipt_id=? OR source_key=?", (receipt_id, source_key)
            ).fetchone()
            if prior is not None:
                _checked_receipt(prior)
                if (
                    prior["job_id"] != job_id
                    or prior["source_key"] != source_key
                    or prior["source_sha256"] != fingerprint
                ):
                    raise StateConflictError("Conflicting retained receipt identity requires reconciliation")
                if prior["status"] != "committed":
                    raise StateConflictError("Retained receipt was interrupted; explicit reconciliation required")
                self.verify_forward_state(connection, _rows(connection))
                return False
            rows = _rows(connection)
            if not self.verify_forward_state(connection, rows):
                original = (
                    connection.execute("SELECT source_digest FROM cp_legacy_import WHERE singleton=1").fetchone()
                    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='cp_legacy_import'").fetchone()
                    else None
                )
                if any(rows.values()) and (original is None or original[0] != _digest(rows)):
                    raise StateConflictError("Unregistered retained source changes require reconciliation")
                connection.execute("INSERT INTO cp_retained_head VALUES(1,?)", (_digest(rows),))
            if any(row["source_event_key"] == source_key for row in rows["processed_events"]):
                raise StateConflictError("Retained source key already exists without its exact receipt")
            before = {
                "protected_sha256": _protected(rows, job_id, source_key),
                "projection": _projection(rows, job_id),
                "expected": expected,
            }
            connection.execute(
                "INSERT INTO cp_retained_forward VALUES(?,?,?,?,?,?,?,NULL,'prepared')",
                (
                    receipt_id,
                    job_id,
                    fingerprint,
                    source_key,
                    _encoded(source),
                    _encoded(before),
                    _digest({"source": source, "before": before}),
                ),
            )
            return True

    def finish_forward(self, receipt_id: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            pending = connection.execute(
                "SELECT * FROM cp_retained_forward WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            if pending is None:
                raise StateConflictError("Retained write has no prepared ownership receipt")
            if pending["status"] == "committed":
                self.verify_forward_state(connection, _rows(connection))
                return
            source, before = _checked_receipt(pending)
            after = _rows(connection)
            job_id, key = pending["job_id"], pending["source_key"]
            self.verify_source(connection, source)
            actual_events = {row["source_event_key"]: row for row in after["processed_events"]}
            if key not in actual_events or _protected(after, job_id, key) != before["protected_sha256"]:
                raise StateConflictError("Retained source changed outside its prepared receipt")
            event = actual_events[key]
            meta = json.loads(event["session_meta_json"])
            if (
                event["session_id"] != job_id
                or not isinstance(meta, dict)
                or meta.get("reserved")
                or meta.get("dead_lettered")
            ):
                raise StateConflictError("Retained result is reserved or has conflicting ownership")
            projection = _projection(after, job_id)
            if _digest(projection) != _digest(before["expected"]["projection"]):
                raise StateConflictError("Retained state or dimensional counters differ from the prepared transition")
            if _digest(meta.get("budget")) != _digest(before["expected"]["budget"]):
                raise StateConflictError("Retained result budget differs from its exact state transition")
            connection.execute(
                "UPDATE cp_retained_forward SET after_json=?,status='committed' WHERE receipt_id=?",
                (_encoded(projection), receipt_id),
            )
            connection.execute("UPDATE cp_retained_head SET rows_sha256=? WHERE singleton=1", (_digest(after),))

    def pending(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [
                dict(row) for row in connection.execute("SELECT * FROM cp_retained_forward WHERE status='prepared'")
            ]

    def recovery_source(self, receipt_id: str) -> dict[str, Any] | None:
        """Validate a prepared transition before repeating only retained accounting."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            pending = connection.execute(
                "SELECT * FROM cp_retained_forward WHERE receipt_id=? AND status='prepared'", (receipt_id,)
            ).fetchone()
            if pending is None:
                raise StateConflictError("Retained reconciliation requires a prepared receipt")
            source, before = _checked_receipt(pending)
            self.verify_source(connection, source)
            rows = _rows(connection)
            job_id, key = pending["job_id"], pending["source_key"]
            if _protected(rows, job_id, key) != before["protected_sha256"]:
                raise StateConflictError("Unknown governance writer requires reconciliation")
            event = next((row for row in rows["processed_events"] if row["source_event_key"] == key), None)
            projection = _projection(rows, job_id)
            if event is None:
                if _digest(projection) != _digest(before["projection"]):
                    raise StateConflictError("Unreserved retained state changed")
                return source
            meta = json.loads(event["session_meta_json"])
            if event["session_id"] != job_id or _digest(projection) != _digest(before["expected"]["projection"]):
                raise StateConflictError("Retained recovery state differs from its prepared transition")
            if meta.get("reserved"):
                if _digest(meta.get("snapshot")) != _digest(before["expected"]["snapshot"]):
                    raise StateConflictError("Retained reservation has a conflicting event-time snapshot")
                return source
            return None

    def run_forward(self, receipt_id: str, write: Callable[[], object]) -> None:
        write()
        self.finish_forward(receipt_id)
