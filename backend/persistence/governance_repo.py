"""Standalone governance ledger; never runs vendor migrations on application data."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from backend.models.domain import StateConflictError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from concurrent.futures import Future
    from pathlib import Path

    from backend.models.usage import UsageAmount


@dataclass(frozen=True, slots=True)
class GovernanceTotals:
    executed_calls: int
    destructive_calls: int
    verified_usd: Decimal
    unaccounted: int
    verified_usd_present: bool
    legacy_estimated_usd: Decimal | None = None
    legacy_currency_unverified: bool = False

    @property
    def budget_accountable(self) -> bool:
        return self.unaccounted == 0


@dataclass(frozen=True, slots=True)
class AuditDecision:
    id: str
    job_id: str
    epoch: str
    action_digest: str
    decision: str
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class AuditHead:
    segment: int
    key_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class GovernanceAdmission:
    generation: int
    blocked: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ReasonGrant:
    reason: str
    issuance_id: str
    source_id: str
    expires_at: datetime

    @property
    def current(self) -> bool:
        return datetime.now(UTC) < self.expires_at


class GovernanceLedger:
    def __init__(self, path: Path, *, application_database: Path) -> None:
        if path.resolve() == application_database.resolve() or (
            path.exists() and application_database.exists() and path.samefile(application_database)
        ):
            raise ValueError("Governance ledger must be separate from the application database")
        self.path = path
        self._io_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="codeplane-governance")
        self._io_lock = threading.Lock()
        self._io_pending = 0
        self._io_closing = False
        self._io_closed = False
        self._io_drained = asyncio.Event()
        self._io_close_loop: asyncio.AbstractEventLoop | None = None
        self._io_close_task: asyncio.Task[None] | None = None
        self._grant_lock = asyncio.Lock()
        self._grant_error: str | None = None

    async def _io[T](self, operation: Callable[..., T], *arguments: object) -> T:
        context = copy_context()
        with self._io_lock:
            if self._io_closing:
                raise RuntimeError("Governance ledger I/O is closed")
            future: Future[T] = self._io_executor.submit(context.run, operation, *arguments)
            self._io_pending += 1

        def finished(_future: Future[T]) -> None:
            with self._io_lock:
                self._io_pending -= 1
                if self._io_closing and self._io_pending == 0:
                    assert self._io_close_loop is not None
                    self._io_close_loop.call_soon_threadsafe(self._io_drained.set)

        future.add_done_callback(finished)
        # A cancelled waiter must not discard an admitted outcome/audit write.
        return await asyncio.shield(asyncio.wrap_future(future))

    async def close(self) -> None:
        """Stop new I/O, drain admitted work and join its owned worker without the default executor."""
        if self._io_closed:
            return
        if self._io_close_task is None:
            async with self._grant_lock:
                if self._io_close_task is None:
                    with self._io_lock:
                        self._io_closing = True
                        self._io_close_loop = asyncio.get_running_loop()
                        if self._io_pending == 0:
                            self._io_drained.set()
                    self._io_close_task = asyncio.create_task(self._close_io(), name="governance-io-drain")
        interrupted = False
        while not self._io_close_task.done():
            try:
                await asyncio.shield(self._io_close_task)
            except asyncio.CancelledError:
                interrupted = True
        self._io_close_task.result()
        if interrupted:
            raise asyncio.CancelledError

    async def _close_io(self) -> None:
        await self._io_drained.wait()
        self._io_executor.shutdown(wait=True)
        self._io_closed = True

    @contextmanager
    def _connect(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        target = self.path.resolve().as_uri() + "?mode=ro" if read_only else str(self.path)
        connection = sqlite3.connect(target, timeout=5, uri=read_only)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            with connection:
                yield connection
        finally:
            connection.close()

    async def initialize(self) -> None:
        await self._io(self._initialize)

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS cp_governance_revision (
                    revision INTEGER PRIMARY KEY CHECK(revision = 1)
                );
                INSERT OR IGNORE INTO cp_governance_revision VALUES(1);
                CREATE TABLE IF NOT EXISTS cp_executed_tools (
                    job_id TEXT NOT NULL, epoch TEXT NOT NULL, tool_call_id TEXT NOT NULL,
                    action_digest TEXT NOT NULL, outcome TEXT NOT NULL, destructive INTEGER NOT NULL,
                    PRIMARY KEY(job_id, epoch, tool_call_id)
                );
                CREATE TABLE IF NOT EXISTS cp_governance_totals (
                    job_id TEXT NOT NULL, epoch TEXT NOT NULL,
                    executed_calls INTEGER NOT NULL DEFAULT 0,
                    destructive_calls INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(job_id, epoch)
                );
                CREATE TABLE IF NOT EXISTS cp_unaccounted_tools (
                    job_id TEXT NOT NULL, epoch TEXT NOT NULL, event_id TEXT NOT NULL, reason TEXT NOT NULL,
                    PRIMARY KEY(job_id, epoch, event_id)
                );
                CREATE TABLE IF NOT EXISTS cp_usage (
                    job_id TEXT NOT NULL, epoch TEXT NOT NULL, event_id TEXT NOT NULL,
                    unit TEXT NOT NULL, role TEXT NOT NULL, producer TEXT NOT NULL,
                    source TEXT NOT NULL, model TEXT NOT NULL, amount TEXT NOT NULL, verified INTEGER NOT NULL,
                    PRIMARY KEY(job_id, epoch, event_id, unit, role, producer, source, model)
                );
                CREATE TABLE IF NOT EXISTS cp_decision_outbox (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, epoch TEXT NOT NULL,
                    action_digest TEXT NOT NULL, decision TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS cp_decision_job ON cp_decision_outbox(job_id);
                CREATE TABLE IF NOT EXISTS cp_reason_grants (
                    job_id TEXT NOT NULL, policy_scope TEXT NOT NULL, reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    execution_epoch TEXT NOT NULL, source_id TEXT NOT NULL,
                    PRIMARY KEY(job_id, policy_scope, reason_code)
                );
                CREATE TABLE IF NOT EXISTS cp_reason_grant_issuances (
                    job_id TEXT NOT NULL, policy_scope TEXT NOT NULL, reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    execution_epoch TEXT NOT NULL, source_id TEXT NOT NULL, issuance_id TEXT NOT NULL,
                    PRIMARY KEY(job_id, policy_scope, reason_code, issuance_id)
                );
                CREATE TABLE IF NOT EXISTS cp_audit_head (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    segment INTEGER NOT NULL, key_id TEXT NOT NULL, content_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cp_event_cursor (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    storage_cursor INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cp_admission_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    generation INTEGER NOT NULL CHECK(generation >= 0),
                    blocked INTEGER NOT NULL CHECK(blocked IN (0, 1)),
                    reason TEXT NOT NULL
                );
                INSERT OR IGNORE INTO cp_admission_control VALUES(1,0,0,'');
            """)

    async def has_decisions(self, job_id: str) -> bool:
        def read() -> bool:
            with self._connect(read_only=True) as connection:
                return (
                    connection.execute("SELECT 1 FROM cp_decision_outbox WHERE job_id=? LIMIT 1", (job_id,)).fetchone()
                    is not None
                )

        return await self._io(read)

    async def active_reason_grants(self, job_id: str, policy_scope: str) -> frozenset[str]:
        return frozenset(await self.reason_grants(job_id, policy_scope))

    async def reason_grants(self, job_id: str, policy_scope: str) -> dict[str, ReasonGrant]:
        def read() -> dict[str, ReasonGrant]:
            with self._connect(read_only=True) as connection:
                rows = connection.execute(
                    """SELECT reason_code,source_id,expires_at,'legacy:' || source_id AS issuance_id
                       FROM cp_reason_grants WHERE job_id=? AND policy_scope=?
                       UNION ALL
                       SELECT reason_code,source_id,expires_at,issuance_id
                       FROM cp_reason_grant_issuances WHERE job_id=? AND policy_scope=?""",
                    (job_id, policy_scope, job_id, policy_scope),
                )
                result: dict[str, ReasonGrant] = {}
                for row in rows:
                    grant = ReasonGrant(
                        row["reason_code"],
                        row["issuance_id"],
                        row["source_id"],
                        datetime.fromisoformat(row["expires_at"]),
                    )
                    prior = result.get(grant.reason)
                    if grant.current and (prior is None or prior.expires_at < grant.expires_at):
                        result[grant.reason] = grant
                return result

        async with self._grant_lock:
            if self._grant_error is not None:
                raise StateConflictError(f"Grant issuance requires reconciliation: {self._grant_error}")
            return await self._io(read)

    async def grant_reasons(
        self,
        job_id: str,
        policy_scope: str,
        reasons: frozenset[str],
        *,
        epoch: str,
        source_id: str,
        is_current: Callable[[], Awaitable[bool]] | None = None,
    ) -> str | None:
        if not all((job_id, policy_scope, epoch, source_id)) or not reasons or len(reasons) > 32:
            raise ValueError("A reason grant requires an exact Job, policy scope and issuing source")

        issuance_id = str(uuid.uuid4())

        def write() -> None:
            now = datetime.now(UTC)
            with self._connect() as connection:
                connection.executemany(
                    "INSERT INTO cp_reason_grant_issuances VALUES(?,?,?,?,?,?,?,?)",
                    [
                        (
                            job_id,
                            policy_scope,
                            reason,
                            now.isoformat(),
                            (now + timedelta(hours=24)).isoformat(),
                            epoch,
                            source_id,
                            issuance_id,
                        )
                        for reason in sorted(reasons)
                    ],
                )

        async with self._grant_lock:
            if self._grant_error is not None:
                raise StateConflictError(f"Grant issuance requires reconciliation: {self._grant_error}")
            if is_current is not None and not await is_current():
                return None
            try:
                await self._io(write)
                if is_current is not None and not await is_current():
                    await self._io(self._revoke_reason_grants, job_id, issuance_id)
                    return None
                return issuance_id
            except BaseException as error:
                # Readers share this lock. The admitted write and its exact
                # compensation must finish before any waiter can observe it.
                cleanup = asyncio.create_task(self._io(self._revoke_reason_grants, job_id, issuance_id))
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                try:
                    cleanup.result()
                except BaseException as cleanup_error:
                    self._grant_error = str(cleanup_error)
                    raise BaseExceptionGroup("Grant issuance rollback failed", [error, cleanup_error]) from None
                raise

    def _revoke_reason_grants(self, job_id: str, issuance_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM cp_reason_grant_issuances WHERE job_id=? AND issuance_id=?", (job_id, issuance_id)
            )

    async def revoke_reason_grants(self, job_id: str, issuance_id: str) -> None:
        async with self._grant_lock:
            await self._io(self._revoke_reason_grants, job_id, issuance_id)

    async def admission_state(self) -> GovernanceAdmission:
        return await self._io(self._admission_state)

    def _admission_state(self) -> GovernanceAdmission:
        if not self.path.is_file():
            raise StateConflictError("Governance admission gate has not been initialized")
        with self._connect(read_only=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cp_admission_control'"
            ).fetchone()
            row = (
                connection.execute(
                    "SELECT generation,blocked,reason FROM cp_admission_control WHERE singleton=1"
                ).fetchone()
                if exists
                else None
            )
        if row is None:
            raise StateConflictError("Governance admission gate has not been initialized")
        return GovernanceAdmission(row["generation"], bool(row["blocked"]), row["reason"])

    async def require_admission(self) -> None:
        state = await self.admission_state()
        if state.blocked:
            raise StateConflictError(f"New governance admission is blocked: {state.reason}")

    async def require_policy_scope(self, job_id: str, policy_id: str) -> None:
        await self._io(self._require_policy_scope, job_id, policy_id)

    def _require_policy_scope(self, job_id: str, policy_id: str) -> None:
        with self._connect(read_only=True) as connection:
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cp_legacy_grants'"
            ).fetchone():
                return
            grants = connection.execute("SELECT policy_id,expires_at FROM cp_legacy_grants WHERE job_id=?", (job_id,))
            now = datetime.now(UTC)
            for grant in grants:
                if datetime.fromisoformat(grant["expires_at"]) > now and grant["policy_id"] != policy_id:
                    raise StateConflictError(
                        "An unexpired legacy grant belongs to another policy; explicit reconciliation is required"
                    )

    async def set_admission_control(
        self, *, expected_generation: int, blocked: bool, reason: str
    ) -> GovernanceAdmission:
        """Fence new admissions without interrupting existing outcome/audit writes."""
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
            or not isinstance(blocked, bool)
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 1024
        ):
            raise ValueError("Admission control requires a generation, boolean state and bounded reason")
        return await self._io(self._set_admission_control, expected_generation, blocked, reason)

    def _set_admission_control(self, expected_generation: int, blocked: bool, reason: str) -> GovernanceAdmission:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE cp_admission_control SET generation=generation+1, blocked=?, reason=?
                   WHERE singleton=1 AND generation=?""",
                (int(blocked), reason, expected_generation),
            )
            if changed.rowcount != 1:
                raise StateConflictError("Governance admission generation changed; reread before retrying")
        return GovernanceAdmission(expected_generation + 1, blocked, reason)

    async def record_completion(
        self,
        *,
        job_id: str,
        epoch: str,
        event_id: str,
        tool_call_id: str | None,
        source_id: str | None,
        action_digest: str,
        executed: bool,
        succeeded: bool,
        destructive: bool,
    ) -> bool:
        return await self._io(
            self._record_completion,
            job_id,
            epoch,
            event_id,
            tool_call_id,
            source_id,
            action_digest,
            executed,
            succeeded,
            destructive,
        )

    def _record_completion(
        self,
        job_id: str,
        epoch: str,
        event_id: str,
        tool_call_id: str | None,
        source_id: str | None,
        action_digest: str,
        executed: bool,
        succeeded: bool,
        destructive: bool,
    ) -> bool:
        if not job_id or not epoch or not event_id or not action_digest:
            raise ValueError("Completion accounting requires Job, epoch, event and action identities")
        if not executed:
            return False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not tool_call_id or not tool_call_id.strip() or not source_id or not source_id.strip():
                connection.execute(
                    "INSERT OR IGNORE INTO cp_unaccounted_tools VALUES(?,?,?,?)",
                    (
                        job_id,
                        epoch,
                        event_id,
                        "missing_tool_call_id" if not tool_call_id else "missing_source_identity",
                    ),
                )
                return False
            # Only storage identity is qualified; the canonical event's vendor
            # tool_call_id remains unchanged on the wire and in history.
            identity = json.dumps(["cp-tool-v1", source_id, tool_call_id], separators=(",", ":"))
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cp_legacy_rows'"
            ).fetchone():
                imported = connection.execute(
                    """SELECT 1 FROM cp_legacy_rows WHERE table_name='processed_events'
                       AND source_key=? AND json_extract(row_json,'$.session_id')=?""",
                    (json.dumps([f"exec:{tool_call_id}"], separators=(",", ":")), job_id),
                ).fetchone()
                if imported:
                    raise ValueError("Imported processed identity cannot be recounted; reconcile its scope")
            legacy = connection.execute(
                "SELECT 1 FROM cp_executed_tools WHERE job_id=? AND epoch=? AND tool_call_id=?",
                (job_id, epoch, tool_call_id),
            ).fetchone()
            if legacy is not None:
                raise ValueError("Unqualified historical tool identity requires reconciliation")
            previous = connection.execute(
                """SELECT action_digest, outcome, destructive FROM cp_executed_tools
                   WHERE job_id=? AND epoch=? AND tool_call_id=?""",
                (job_id, epoch, identity),
            ).fetchone()
            outcome = "succeeded" if succeeded else "failed"
            if previous is not None:
                if (
                    previous["action_digest"] != action_digest
                    or previous["outcome"] != outcome
                    or previous["destructive"] != int(destructive)
                ):
                    raise ValueError("Conflicting executed tool identity requires reconciliation")
                return False
            connection.execute(
                "INSERT INTO cp_executed_tools VALUES(?,?,?,?,?,?)",
                (job_id, epoch, identity, action_digest, outcome, int(destructive)),
            )
            connection.execute(
                """INSERT INTO cp_governance_totals VALUES(?,?,1,?)
                   ON CONFLICT(job_id,epoch) DO UPDATE SET
                   executed_calls=executed_calls+1, destructive_calls=destructive_calls+excluded.destructive_calls""",
                (job_id, epoch, int(destructive)),
            )
            return True

    async def record_usage(self, event_id: str, usage: UsageAmount) -> bool:
        if not usage.job_id or not usage.execution_epoch or not event_id:
            raise ValueError("Usage accounting requires Job, epoch and source event identity")
        return await self._io(self._record_usage, event_id, usage)

    def _record_usage(self, event_id: str, usage: UsageAmount) -> bool:
        values = (
            usage.job_id,
            usage.execution_epoch,
            event_id,
            usage.unit,
            usage.role,
            usage.producer,
            usage.source,
            usage.model or "",
            str(usage.amount),
            int(usage.verified),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                """SELECT amount, verified FROM cp_usage WHERE job_id=? AND epoch=? AND event_id=?
                   AND unit=? AND role=? AND producer=? AND source=? AND model=?""",
                values[:8],
            ).fetchone()
            if previous is not None:
                if Decimal(previous["amount"]) != usage.amount or previous["verified"] != int(usage.verified):
                    raise ValueError("Conflicting usage identity requires reconciliation")
                return False
            connection.execute("INSERT INTO cp_usage VALUES(?,?,?,?,?,?,?,?,?,?)", values)
            return True

    async def totals(self, job_id: str, epoch: str) -> GovernanceTotals:
        return await self._io(self._totals, job_id, epoch)

    async def job_totals(self, job_id: str) -> GovernanceTotals:
        """Carry the Job budget across process epochs and continuation admissions."""
        if not job_id:
            raise ValueError("Budget accounting requires a Job identity")
        return await self._io(self._totals, job_id, None)

    async def record_uncertainty(self, job_id: str, epoch: str, event_id: str, reason: str) -> None:
        if not all((job_id, epoch, event_id, reason)):
            raise ValueError("Unresolved accounting requires exact source context")
        await self._io(self._record_uncertainty, job_id, epoch, event_id, reason)

    def _record_uncertainty(self, job_id: str, epoch: str, event_id: str, reason: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO cp_unaccounted_tools VALUES(?,?,?,?)",
                (job_id, epoch, event_id, reason),
            )

    async def event_cursor(self) -> int:
        return await self._io(self._event_cursor)

    def _event_cursor(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT storage_cursor FROM cp_event_cursor WHERE singleton=1").fetchone()
            return int(row[0]) if row is not None else 0

    async def advance_event_cursor(self, previous: int, current: int) -> None:
        if not 0 <= previous < current:
            raise ValueError("Governance event cursor must advance monotonically")
        await self._io(self._advance_event_cursor, previous, current)

    def _advance_event_cursor(self, previous: int, current: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT storage_cursor FROM cp_event_cursor WHERE singleton=1").fetchone()
            retained = int(row[0]) if row is not None else 0
            if retained != previous:
                raise ValueError("Concurrent governance event replay requires rereading its cursor")
            connection.execute(
                "INSERT INTO cp_event_cursor VALUES(1,?) "
                "ON CONFLICT(singleton) DO UPDATE SET storage_cursor=excluded.storage_cursor",
                (current,),
            )

    async def authorized_tool(
        self,
        job_id: str,
        epoch: str,
        execution_token: str,
        tool_call_id: str,
    ) -> AuditDecision | None:
        return await self._io(self._authorized_tool, job_id, epoch, execution_token, tool_call_id)

    def _authorized_tool(
        self,
        job_id: str,
        epoch: str,
        execution_token: str,
        tool_call_id: str,
    ) -> AuditDecision | None:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,job_id,epoch,action_digest,decision,metadata_json FROM cp_decision_outbox
                   WHERE job_id=? AND epoch=?
                   AND json_extract(metadata_json,'$.execution.execution_token')=?
                   AND json_extract(metadata_json,'$.tool_call_id')=? ORDER BY rowid DESC LIMIT 2""",
                (job_id, epoch, execution_token, tool_call_id),
            ).fetchall()
        if len(rows) != 1:
            return None
        row = rows[0]
        return AuditDecision(
            row["id"],
            row["job_id"],
            row["epoch"],
            row["action_digest"],
            row["decision"],
            json.loads(row["metadata_json"]),
        )

    def _totals(self, job_id: str, epoch: str | None) -> GovernanceTotals:
        scope = "job_id=?" if epoch is None else "job_id=? AND epoch=?"
        parameters = (job_id,) if epoch is None else (job_id, epoch)
        with self._connect() as connection:
            connection.execute("BEGIN")
            counts = connection.execute(
                f"SELECT SUM(executed_calls), SUM(destructive_calls) FROM cp_governance_totals WHERE {scope}",
                parameters,
            ).fetchone()
            costs = connection.execute(
                f"SELECT amount FROM cp_usage WHERE {scope} AND unit='USD' AND verified=1",
                parameters,
            ).fetchall()
            missing = connection.execute(
                f"SELECT COUNT(*) FROM cp_unaccounted_tools WHERE {scope}",
                parameters,
            ).fetchone()[0]
            legacy = None
            if (
                epoch is None
                and connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cp_legacy_budget'"
                ).fetchone()
            ):
                legacy = connection.execute("SELECT * FROM cp_legacy_budget WHERE job_id=?", (job_id,)).fetchone()
            retained = None
            if (
                epoch is None
                and connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cp_retained_forward'"
                ).fetchone()
            ):
                retained = connection.execute(
                    """SELECT after_json FROM cp_retained_forward
                       WHERE job_id=? AND status='committed' ORDER BY rowid DESC LIMIT 1""",
                    (job_id,),
                ).fetchone()
            retained_calls = retained_destructive = 0
            if retained is not None:
                snapshot = json.loads(retained["after_json"])
                retained_calls = next(
                    row["total_tool_calls"] for row in snapshot["session_state"] if row["session_id"] == job_id
                )
                retained_destructive = next(
                    (
                        row["count"]
                        for row in snapshot["budget_counters"]
                        if row["session_id"] == job_id and row["dimension"] == "effect" and row["key"] == "destructive"
                    ),
                    0,
                )
            return GovernanceTotals(
                (counts[0] or 0) + (retained_calls if retained else legacy["executed_calls"] if legacy else 0),
                (counts[1] or 0) + (retained_destructive if retained else legacy["destructive_calls"] if legacy else 0),
                sum((Decimal(row["amount"]) for row in costs), Decimal(0)),
                missing,
                bool(costs),
                Decimal(legacy["estimated_usd"]) if legacy and legacy["estimated_usd"] is not None else None,
                legacy is not None,
            )

    async def enqueue_decision(
        self,
        *,
        decision_id: str,
        job_id: str,
        epoch: str,
        action_digest: str,
        decision: str,
        metadata: dict[str, object],
    ) -> None:
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > 32 * 1024:
            raise ValueError("Decision audit metadata exceeds 32 KiB")
        await self._io(self._enqueue_decision, decision_id, job_id, epoch, action_digest, decision, encoded)

    def _enqueue_decision(
        self, decision_id: str, job_id: str, epoch: str, action_digest: str, decision: str, encoded: str
    ) -> None:
        values = (decision_id, job_id, epoch, action_digest, decision, encoded)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id,job_id,epoch,action_digest,decision,metadata_json FROM cp_decision_outbox WHERE id=?",
                (decision_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != values:
                    raise ValueError("Decision identity cannot be reused for another action")
                return
            connection.execute(
                "INSERT INTO cp_decision_outbox(id,job_id,epoch,action_digest,decision,metadata_json) "
                "VALUES(?,?,?,?,?,?)",
                values,
            )

    async def deliver_audit(self, write: Callable[[AuditDecision, AuditHead | None], AuditHead]) -> bool:
        """Serialize delivery across processes; the writer reconciles an unacknowledged file tail."""
        return await self._io(self._deliver_audit, write)

    def _deliver_audit(self, write: Callable[[AuditDecision, AuditHead | None], AuditHead]) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id,job_id,epoch,action_digest,decision,metadata_json "
                "FROM cp_decision_outbox WHERE delivered=0 ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            stored_head = connection.execute(
                "SELECT segment,key_id,content_hash FROM cp_audit_head WHERE singleton=1"
            ).fetchone()
            head = AuditHead(*tuple(stored_head)) if stored_head is not None else None
            decision = AuditDecision(
                row["id"],
                row["job_id"],
                row["epoch"],
                row["action_digest"],
                row["decision"],
                json.loads(row["metadata_json"]),
            )
            advanced = write(decision, head)
            connection.execute(
                "INSERT INTO cp_audit_head VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
                "segment=excluded.segment,key_id=excluded.key_id,content_hash=excluded.content_hash",
                (advanced.segment, advanced.key_id, advanced.content_hash),
            )
            connection.execute("UPDATE cp_decision_outbox SET delivered=1 WHERE id=?", (decision.id,))
            return True
