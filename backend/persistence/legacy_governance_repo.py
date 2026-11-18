"""Provenance-preserving import of the retained TF governance schema.

Import leaves source rows unchanged. Original evidence stays immutable when an
owned retained provider later advances the working tables. Imported counters are
aggregates, not fabricated execution receipts. Grants keep their exact TF scope.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.models.domain import StateConflictError

if TYPE_CHECKING:
    from backend.persistence.governance_ownership import GovernanceRuntimeLease

LEGACY_POLICY = "tf-policy-v1"
_TABLES = {
    "session_state": ("session_id",),
    "budget_counters": ("session_id", "dimension", "key"),
    "processed_events": ("source_event_key",),
    "trust_grants": ("session_id", "ordinal"),
}
_DIMENSIONS = {"effect", "mechanism", "scope", "role", "phase", "capability", "action", "structure"}
_GRANT_KEYS = {
    "mutating_with_network",
    "risk_score_danger",
    "mutating_locked",
    "mutating_savepoint",
    "risk_score_caution",
    "readonly_shell_locked",
}


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise StateConflictError("Legacy timestamp requires explicit reconciliation")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StateConflictError("Malformed legacy timestamp requires reconciliation") from exc
    if parsed.tzinfo is None:
        raise StateConflictError("Unscoped legacy timestamp requires reconciliation")
    return parsed.astimezone(UTC)


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise StateConflictError("Invalid legacy counter requires reconciliation")
    return value


def _rows(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(_TABLES).issubset(tables):
        raise StateConflictError("Legacy governance schema is incomplete")
    revision = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    if [tuple(row) for row in revision] != [("0001_initial",)]:
        raise StateConflictError("Legacy governance revision requires explicit reconciliation")
    return {
        table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {','.join(keys)}")]
        for table, keys in _TABLES.items()
    }


def _key(table: str, row: dict[str, Any]) -> str:
    return _encoded([row[key] for key in _TABLES[table]])


def _schema(connection: sqlite3.Connection) -> None:
    for statement in (
        """CREATE TABLE IF NOT EXISTS cp_legacy_import (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), source_digest TEXT NOT NULL,
            backup_path TEXT NOT NULL, backup_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL,
            producer TEXT NOT NULL, comparison_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS cp_legacy_rows (
            table_name TEXT NOT NULL, source_key TEXT NOT NULL, source_digest TEXT NOT NULL,
            row_sha256 TEXT NOT NULL, row_json TEXT NOT NULL,
            PRIMARY KEY(table_name,source_key))""",
        """CREATE TABLE IF NOT EXISTS cp_legacy_budget (
            job_id TEXT PRIMARY KEY, source_digest TEXT NOT NULL,
            executed_calls INTEGER NOT NULL, destructive_calls INTEGER NOT NULL,
            estimated_usd TEXT, spend_provenance TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS cp_legacy_grants (
            job_id TEXT NOT NULL, ordinal INTEGER NOT NULL, policy_id TEXT NOT NULL,
            reason_key TEXT NOT NULL, granted_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            source_digest TEXT NOT NULL, PRIMARY KEY(job_id,ordinal))""",
    ):
        connection.execute(statement)


class LegacyGovernanceRepository:
    def __init__(self, path: Path, application_database: Path) -> None:
        self.path = path.resolve()
        self.application_database = application_database.resolve()
        if self.path == self.application_database or (
            self.path.exists() and self.application_database.exists() and self.path.samefile(self.application_database)
        ):
            raise ValueError("Governance and application databases must be distinct")

    def _application_scope(self, sessions: set[str]) -> dict[str, tuple[str | None, str]]:
        with closing(sqlite3.connect(self.application_database.as_uri() + "?mode=ro", uri=True)) as app:
            app.row_factory = sqlite3.Row
            app.execute("BEGIN")
            result = {}
            for identity in sessions:
                job = app.execute("SELECT id,observation_only FROM jobs WHERE id=?", (identity,)).fetchone()
                if job is None or job["observation_only"]:
                    raise StateConflictError(f"Unmapped legacy Job scope requires reconciliation: {identity}")
                rows = app.execute(
                    "SELECT total_cost_usd FROM job_telemetry_summary WHERE job_id=? AND session_kind='job'",
                    (identity,),
                ).fetchall()
                if len(rows) > 1:
                    raise StateConflictError("Legacy spend-reader scope is ambiguous")
                amounts = [row[0] for row in rows]
                if any(
                    value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0)
                    for value in amounts
                ):
                    raise StateConflictError("Legacy spend-reader values require reconciliation")
                # This is the retained CP spend-reader aggregate, never verified currency.
                estimate = str(amounts[0]) if amounts and amounts[0] is not None else None
                result[identity] = (
                    estimate,
                    "codeplane.job_telemetry_summary:estimated" if estimate is not None else "unknown",
                )
            return result

    def import_original(self, lease: GovernanceRuntimeLease, *, now: datetime | None = None) -> dict[str, Any]:
        lease.require_exclusive()
        if lease.directory != self.path.parent or not self.path.is_file() or not self.application_database.is_file():
            raise StateConflictError("Import requires the exact stopped runtime home and both existing databases")
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError("Import time must be timezone aware")
        with closing(sqlite3.connect(self.path, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            try:
                records = _rows(connection)
                source_digest = _digest(records)
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "cp_legacy_import" in tables:
                    prior = connection.execute("SELECT * FROM cp_legacy_import WHERE singleton=1").fetchone()
                    if prior is not None:
                        if prior["source_digest"] != source_digest:
                            raise StateConflictError("Imported legacy state changed; explicit reconciliation required")
                        self._verify(connection, records, prior)
                        connection.rollback()
                        return {**dict(prior), "status": "already_imported"}
                sessions = {row["session_id"] for row in records["session_state"]}
                scope = self._application_scope(sessions)
                processed: dict[str, int] = dict.fromkeys(sessions, 0)
                for row in records["processed_events"]:
                    identity, key = row["session_id"], row["source_event_key"]
                    if (
                        identity not in sessions
                        or not isinstance(key, str)
                        or not key.startswith("exec:")
                        or len(key) <= 5
                    ):
                        raise StateConflictError("Unmappable processed identity requires reconciliation")
                    try:
                        meta = json.loads(row["session_meta_json"])
                    except (ValueError, TypeError) as exc:
                        raise StateConflictError("Malformed processed identity requires reconciliation") from exc
                    if not isinstance(meta, dict) or meta.get("reserved") or not isinstance(meta.get("budget"), dict):
                        raise StateConflictError("Reserved or incomplete processed identity requires reconciliation")
                    _timestamp(row["processed_at"])
                    processed[identity] += 1
                counts = {row["session_id"]: row for row in records["session_state"]}
                destructive = dict.fromkeys(sessions, 0)
                for identity, row in counts.items():
                    total = _count(row["total_tool_calls"])
                    _count(row["total_tokens"])
                    _count(row["event_count"])
                    _count(row["dropped_events"])
                    if total != processed[identity]:
                        raise StateConflictError("Legacy counts and processed key sets disagree")
                    if "cp_governance_totals" in tables:
                        existing = connection.execute(
                            "SELECT SUM(executed_calls) FROM cp_governance_totals WHERE job_id=?", (identity,)
                        ).fetchone()[0]
                        if existing:
                            raise StateConflictError(
                                "Legacy and replacement accounting overlap; reconcile before import"
                            )
                for row in records["budget_counters"]:
                    identity = row["session_id"]
                    if identity not in sessions or row["dimension"] not in _DIMENSIONS or not row["key"]:
                        raise StateConflictError("Unknown budget dimension or scope requires reconciliation")
                    count = _count(row["count"])
                    if count > counts[identity]["total_tool_calls"]:
                        raise StateConflictError("Legacy dimension exceeds its aggregate counter")
                    if row["dimension"] == "effect" and row["key"] == "destructive":
                        destructive[identity] = count
                grants = []
                for row in records["trust_grants"]:
                    if row["session_id"] not in sessions:
                        raise StateConflictError("Unmapped legacy grant scope requires reconciliation")
                    _count(row["ordinal"])
                    granted = _timestamp(row["granted_at"])
                    ttl = row["ttl_seconds"]
                    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl < 0:
                        raise StateConflictError("Invalid legacy grant lifetime requires reconciliation")
                    try:
                        expires = granted + timedelta(seconds=ttl)
                    except OverflowError as exc:
                        raise StateConflictError("Legacy grant expiry is out of range") from exc
                    if granted > instant:
                        raise StateConflictError("Future legacy grant requires reconciliation")
                    if expires > instant:
                        if row["key"] not in _GRANT_KEYS:
                            raise StateConflictError("Unknown or mandatory-gate grant cannot be imported as authority")
                        grants.append((row, expires))
                backup, backup_sha = self._backup(source_digest)
                lease.require_exclusive()
                _schema(connection)
                for table, rows in records.items():
                    for row in rows:
                        connection.execute(
                            "INSERT INTO cp_legacy_rows VALUES(?,?,?,?,?)",
                            (table, _key(table, row), source_digest, _digest(row), _encoded(row)),
                        )
                for identity, row in counts.items():
                    connection.execute(
                        "INSERT INTO cp_legacy_budget VALUES(?,?,?,?,?,?)",
                        (identity, source_digest, row["total_tool_calls"], destructive[identity], *scope[identity]),
                    )
                for row, expires in grants:
                    connection.execute(
                        "INSERT INTO cp_legacy_grants VALUES(?,?,?,?,?,?,?)",
                        (
                            row["session_id"],
                            row["ordinal"],
                            LEGACY_POLICY,
                            row["key"],
                            row["granted_at"],
                            expires.isoformat(),
                            source_digest,
                        ),
                    )
                comparison = {
                    table: {"count": len(rows), "keys_sha256": _digest([_key(table, row) for row in rows])}
                    for table, rows in records.items()
                }
                comparison["projections"] = self._projection_digest(connection)
                connection.execute(
                    "INSERT INTO cp_legacy_import VALUES(1,?,?,?,?,?,?)",
                    (
                        source_digest,
                        str(backup),
                        backup_sha,
                        instant.isoformat(),
                        "traceforge:legacy-schema/0001_initial",
                        _encoded(comparison),
                    ),
                )
                receipt = connection.execute("SELECT * FROM cp_legacy_import WHERE singleton=1").fetchone()
                self._verify(connection, records, receipt)
                connection.commit()
                return {**dict(receipt), "status": "imported", "active_scoped_grants": len(grants)}
            except BaseException:
                connection.rollback()
                raise

    def _backup(self, source_digest: str) -> tuple[Path, str]:
        destination = self.path.parent / "governance-backups"
        destination.mkdir(mode=0o700, exist_ok=True)
        backup = destination / f"original-{source_digest}.db"
        if not backup.exists():
            descriptor, candidate_name = tempfile.mkstemp(prefix=".original-", suffix=".db", dir=destination)
            os.close(descriptor)
            candidate = Path(candidate_name)
            try:
                with (
                    closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source,
                    closing(sqlite3.connect(candidate)) as target,
                ):
                    source.backup(target)
                with candidate.open("r+b") as stream:
                    os.fsync(stream.fileno())
                with suppress(FileExistsError):
                    os.link(candidate, backup)
            finally:
                candidate.unlink(missing_ok=True)
            backup.chmod(0o400)
            if hasattr(os, "O_DIRECTORY"):
                descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as source:
            source.row_factory = sqlite3.Row
            if _digest(_rows(source)) != source_digest:
                raise StateConflictError("Original governance backup changed or conflicts")
        if backup.stat().st_mode & 0o222:
            raise StateConflictError("Original governance backup must remain read-only")
        return backup, hashlib.sha256(backup.read_bytes()).hexdigest()

    @staticmethod
    def _projection_digest(connection: sqlite3.Connection) -> dict[str, Any]:
        return {
            table: _digest([dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}")])
            for table, order in (("cp_legacy_budget", "job_id"), ("cp_legacy_grants", "job_id,ordinal"))
        }

    @staticmethod
    def _verify(connection: sqlite3.Connection, records: dict[str, list[dict[str, Any]]], receipt: sqlite3.Row) -> None:
        backup = Path(receipt["backup_path"])
        if (
            not backup.is_file()
            or backup.stat().st_mode & 0o222
            or hashlib.sha256(backup.read_bytes()).hexdigest() != receipt["backup_sha256"]
        ):
            raise StateConflictError("Retained original backup is missing or changed")
        comparison = json.loads(receipt["comparison_json"])
        if comparison.get("projections") != LegacyGovernanceRepository._projection_digest(connection):
            raise StateConflictError("Imported budget or grant projections changed; writer cannot activate")
        for table, rows in records.items():
            actual = {
                row["source_key"]: (row["source_digest"], row["row_sha256"], row["row_json"])
                for row in connection.execute("SELECT * FROM cp_legacy_rows WHERE table_name=?", (table,))
            }
            expected = {_key(table, row): (receipt["source_digest"], _digest(row), _encoded(row)) for row in rows}
            if actual != expected:
                raise StateConflictError("Imported counts, keys or source provenance differ; writer cannot activate")
            if comparison.get(table) != {
                "count": len(rows),
                "keys_sha256": _digest([_key(table, row) for row in rows]),
            }:
                raise StateConflictError("Imported comparison evidence differs; writer cannot activate")

    def require_verified(self) -> None:
        if not self.path.is_file():
            return
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "cp_legacy_import" in tables:
                receipt = connection.execute("SELECT * FROM cp_legacy_import WHERE singleton=1").fetchone()
                records = _rows(connection)
                from backend.persistence.governance_provider_repo import GovernanceProviderRepository

                if receipt is not None and GovernanceProviderRepository.verify_forward_state(connection, records):
                    self.verify_original_import(connection)
                    return
                if receipt is None or receipt["source_digest"] != _digest(records):
                    raise StateConflictError(
                        "Legacy governance import requires reconciliation before writer activation"
                    )
                self._verify(connection, records, receipt)
                return
            if any(table.startswith("cp_legacy_") for table in tables):
                raise StateConflictError("Legacy import evidence is incomplete; reconciliation required")
            if not set(_TABLES).intersection(tables):
                if any(
                    table in tables and connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                    for table in ("cp_retained_forward", "cp_retained_head")
                ):
                    raise StateConflictError("Retained governance source schema is incomplete")
                return
            records = _rows(connection)
            from backend.persistence.governance_provider_repo import GovernanceProviderRepository

            if GovernanceProviderRepository.verify_forward_state(connection, records):
                return
            if not any(records.values()):
                return
            raise StateConflictError("Legacy governance requires explicit verified import before writer activation")

    @staticmethod
    def verify_original_import(connection: sqlite3.Connection) -> None:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "cp_legacy_import" not in tables:
            if any(table.startswith("cp_legacy_") for table in tables):
                raise StateConflictError("Legacy import evidence is incomplete")
            return
        receipt = connection.execute("SELECT * FROM cp_legacy_import WHERE singleton=1").fetchone()
        if receipt is None:
            raise StateConflictError("Legacy import evidence is incomplete")
        with closing(sqlite3.connect(Path(receipt["backup_path"]).as_uri() + "?mode=ro", uri=True)) as backup:
            backup.row_factory = sqlite3.Row
            original = _rows(backup)
        if receipt["source_digest"] != _digest(original):
            raise StateConflictError("Original import backup provenance changed")
        LegacyGovernanceRepository._verify(connection, original, receipt)
