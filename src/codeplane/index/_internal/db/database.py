"""Database engine and bulk writer with Read-After-Write pattern.

This module provides:
- Database: Connection manager with WAL mode for concurrent access
- BulkWriter: High-performance bulk inserts with FK resolution
- Session utilities for ORM and serializable transactions
- Retry logic for SQLite busy timeout handling

The hybrid pattern:
- Use ORM sessions for low-volume operations (config, job management)
- Use BulkWriter for high-volume operations (files, symbols, occurrences)
- Use immediate_transaction for RepoState updates (prevents races)
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = structlog.get_logger()

# Retry configuration for SQLite busy handling
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 0.1  # 100ms base
DEFAULT_RETRY_MAX_DELAY = 2.0  # 2s max


def _is_database_locked_error(error: Exception) -> bool:
    """Check if error is a SQLite database locked error."""
    error_str = str(error).lower()
    return "database is locked" in error_str or "database is busy" in error_str


class Database:
    """SQLite connection manager with WAL mode for concurrent access.

    Includes retry logic with exponential backoff for handling
    SQLite busy timeouts during concurrent writes.
    """

    def __init__(
        self,
        db_path: Path,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY,
        retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY,
    ) -> None:
        self.db_path = db_path
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self.engine = self._create_engine()

    def _create_engine(self) -> Engine:
        engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
        event.listen(engine, "connect", _configure_pragmas)
        return engine

    def create_all(self) -> None:
        """Create all tables from SQLModel metadata."""
        SQLModel.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        """Drop all tables. Use with caution."""
        SQLModel.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """ORM session for low-volume operations."""
        with Session(self.engine) as session:
            yield session

    @contextmanager
    def immediate_transaction(
        self,
        max_retries: int | None = None,
    ) -> Generator[Session, None, None]:
        """
        Session with BEGIN IMMEDIATE for serializable writes.

        Includes retry logic with exponential backoff for handling
        SQLite busy timeouts.

        Use for RepoState updates to prevent race conditions.
        BEGIN IMMEDIATE acquires a RESERVED lock immediately,
        blocking other writers but allowing readers.

        The session auto-commits on successful exit and rolls back
        on exception.

        Args:
            max_retries: Override default max retries (default: 3)
        """
        retries = max_retries if max_retries is not None else self._max_retries
        last_error: Exception | None = None

        for attempt in range(retries + 1):  # +1 for initial attempt
            try:
                with Session(self.engine) as session:
                    session.execute(text("BEGIN IMMEDIATE"))
                    try:
                        yield session
                        session.commit()
                        return  # Success - exit generator
                    except Exception:
                        session.rollback()
                        raise
            except OperationalError as e:
                if _is_database_locked_error(e) and attempt < retries:
                    # Calculate backoff delay with jitter
                    delay = min(
                        self._retry_base_delay * (2**attempt),
                        self._retry_max_delay,
                    )
                    logger.warning(
                        "sqlite_busy_retry",
                        attempt=attempt + 1,
                        max_retries=retries,
                        delay_sec=delay,
                    )
                    time.sleep(delay)
                    last_error = e
                    continue
                raise
            except Exception:
                raise

        # Should not reach here, but handle edge case
        if last_error:
            raise last_error

    @contextmanager
    def bulk_writer(self) -> Generator[BulkWriter, None, None]:
        """
        Bulk writer for high-volume inserts.

        Auto-commits on successful exit, rolls back on exception.
        """
        writer = BulkWriter(self.engine)
        try:
            yield writer
            writer.commit()
        except Exception:
            writer.rollback()
            raise
        finally:
            writer.close()

    def execute_raw(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Execute raw SQL for complex queries."""
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            conn.commit()
            return result

    def checkpoint(self, mode: str = "PASSIVE") -> None:
        """Run WAL checkpoint.

        Args:
            mode: PASSIVE (default), FULL, RESTART, or TRUNCATE
        """
        valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
        if mode.upper() not in valid_modes:
            raise ValueError(f"Invalid checkpoint mode: {mode}. Must be one of {valid_modes}")

        with self.engine.connect() as conn:
            conn.execute(text(f"PRAGMA wal_checkpoint({mode.upper()})"))
            logger.debug("wal_checkpoint_completed", mode=mode)


def _configure_pragmas(dbapi_conn: Any, _connection_record: Any) -> None:
    """Configure SQLite for concurrent access and performance."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")  # 30 second wait
    cursor.execute("PRAGMA synchronous=NORMAL")  # Safe with WAL
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
    cursor.close()


class BulkWriter:
    """High-performance bulk insert using Core SQL, bypassing ORM overhead."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.conn = engine.connect()
        self.transaction = self.conn.begin()

    def insert_many(self, model_class: type[SQLModel], records: list[dict[str, Any]]) -> int:
        """Bulk insert records into table, returning count inserted."""
        if not records:
            return 0

        table = model_class.__table__  # type: ignore[attr-defined]
        self.conn.execute(table.insert(), records)
        return len(records)

    def insert_many_returning_ids(
        self,
        model_class: type[SQLModel],
        records: list[dict[str, Any]],
        key_columns: list[str],
    ) -> dict[tuple[Any, ...], int]:
        """Bulk insert and return mapping of key columns to generated IDs for FK resolution."""
        if not records:
            return {}

        table = model_class.__table__  # type: ignore[attr-defined]

        # Bulk insert
        self.conn.execute(table.insert(), records)

        # Build key values for lookup
        key_values = [tuple(r[k] for k in key_columns) for r in records]

        # Read back IDs - use IN clause for efficiency
        key_cols_sql = ", ".join(key_columns)

        params: dict[str, Any]
        if len(key_columns) == 1:
            # Simple case: single column key
            placeholders = ", ".join(f":k{i}" for i in range(len(key_values)))
            sql = f"SELECT id, {key_cols_sql} FROM {table.name} WHERE {key_columns[0]} IN ({placeholders})"
            params = {f"k{i}": kv[0] for i, kv in enumerate(key_values)}
        else:
            # Compound key: use OR of ANDs
            conditions = []
            params = {}
            for i, kv in enumerate(key_values):
                conds = []
                for j, col in enumerate(key_columns):
                    param_name = f"k{i}_{j}"
                    conds.append(f"{col} = :{param_name}")
                    params[param_name] = kv[j]
                conditions.append(f"({' AND '.join(conds)})")
            where_clause = " OR ".join(conditions)
            sql = f"SELECT id, {key_cols_sql} FROM {table.name} WHERE {where_clause}"

        result = self.conn.execute(text(sql), params)

        # Build mapping: tuple(key_values) -> id
        return {tuple(row[1:]) if len(key_columns) > 1 else (row[1],): row[0] for row in result}

    def upsert_many(
        self,
        model_class: type[SQLModel],
        records: list[dict[str, Any]],
        conflict_columns: list[str],
        update_columns: list[str],
    ) -> int:
        """Bulk upsert (insert or update on conflict), returning count processed."""
        if not records:
            return 0

        table = model_class.__table__  # type: ignore[attr-defined]

        conflict_cols = ", ".join(conflict_columns)
        update_sets = ", ".join(f"{col} = excluded.{col}" for col in update_columns)

        columns = list(records[0].keys())
        col_names = ", ".join(columns)
        placeholders = ", ".join(f":{col}" for col in columns)

        sql = f"""
            INSERT INTO {table.name} ({col_names})
            VALUES ({placeholders})
            ON CONFLICT ({conflict_cols})
            DO UPDATE SET {update_sets}
        """

        for record in records:
            self.conn.execute(text(sql), record)

        return len(records)

    def delete_where(
        self,
        model_class: type[SQLModel],
        condition: str,
        params: dict[str, Any],
    ) -> int:
        """Bulk delete rows matching condition, returning count affected."""
        table = model_class.__table__  # type: ignore[attr-defined]
        sql = f"DELETE FROM {table.name} WHERE {condition}"
        result = self.conn.execute(text(sql), params)
        return int(result.rowcount)

    def update_where(
        self,
        model_class: type[SQLModel],
        updates: dict[str, Any],
        condition: str,
        params: dict[str, Any],
    ) -> int:
        """
        Bulk update with condition.

        Returns:
            Number of rows affected
        """
        table = model_class.__table__  # type: ignore[attr-defined]
        set_clause = ", ".join(f"{k} = :upd_{k}" for k in updates)
        sql = f"UPDATE {table.name} SET {set_clause} WHERE {condition}"
        update_params = {f"upd_{k}": v for k, v in updates.items()}
        result = self.conn.execute(text(sql), {**update_params, **params})
        return int(result.rowcount)

    def commit(self) -> None:
        """Commit the current transaction."""
        self.transaction.commit()

    def rollback(self) -> None:
        """Rollback the current transaction."""
        self.transaction.rollback()

    def close(self) -> None:
        """Close the connection."""
        self.conn.close()
