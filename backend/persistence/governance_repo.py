"""Standalone governance ledger; never runs vendor migrations on application data."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from backend.models.usage import UsageAmount


@dataclass(frozen=True, slots=True)
class GovernanceTotals:
    executed_calls: int
    destructive_calls: int
    verified_usd: Decimal
    unaccounted: int
    verified_usd_present: bool

    @property
    def budget_accountable(self) -> bool:
        return self.unaccounted == 0


class GovernanceLedger:
    def __init__(self, path: Path, *, application_database: Path) -> None:
        if path.resolve() == application_database.resolve() or (
            path.exists() and application_database.exists() and path.samefile(application_database)
        ):
            raise ValueError("Governance ledger must be separate from the application database")
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            with connection:
                yield connection
        finally:
            connection.close()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

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
            """)

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
        return await asyncio.to_thread(
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
        return await asyncio.to_thread(self._record_usage, event_id, usage)

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
        return await asyncio.to_thread(self._totals, job_id, epoch)

    def _totals(self, job_id: str, epoch: str) -> GovernanceTotals:
        with self._connect() as connection:
            connection.execute("BEGIN")
            counts = connection.execute(
                "SELECT executed_calls, destructive_calls FROM cp_governance_totals WHERE job_id=? AND epoch=?",
                (job_id, epoch),
            ).fetchone()
            costs = connection.execute(
                "SELECT amount FROM cp_usage WHERE job_id=? AND epoch=? AND unit='USD' AND verified=1",
                (job_id, epoch),
            ).fetchall()
            missing = connection.execute(
                "SELECT COUNT(*) FROM cp_unaccounted_tools WHERE job_id=? AND epoch=?",
                (job_id, epoch),
            ).fetchone()[0]
            return GovernanceTotals(
                counts["executed_calls"] if counts else 0,
                counts["destructive_calls"] if counts else 0,
                sum((Decimal(row["amount"]) for row in costs), Decimal(0)),
                missing,
                bool(costs),
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
        await asyncio.to_thread(self._enqueue_decision, decision_id, job_id, epoch, action_digest, decision, encoded)

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
