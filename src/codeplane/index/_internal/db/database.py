"""Database engine and bulk writer with Read-After-Write pattern.

This module provides:
- Database: Connection manager with WAL mode for concurrent access
- BulkWriter: High-performance bulk inserts with FK resolution
- Session utilities for ORM and serializable transactions

The hybrid pattern:
- Use ORM sessions for low-volume operations (config, job management)
- Use BulkWriter for high-volume operations (files, symbols, occurrences)
- Use immediate_transaction for RepoState updates (prevents races)
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

if TYPE_CHECKING:
    from sqlalchemy import Engine


class Database:
    """
    Database connection manager with WAL mode and hybrid access patterns.

    Configures SQLite for concurrent access:
    - WAL mode for concurrent readers with queued writers
    - 30-second busy timeout to handle contention
    - Foreign keys enabled for referential integrity

    Usage::

        db = Database(Path("index.db"))
        db.create_all()

        # ORM access (low volume)
        with db.session() as session:
            context = session.get(Context, 1)

        # Serializable writes (RepoState)
        with db.immediate_transaction() as session:
            repo_state = session.get(RepoState, 1)
            repo_state.last_seen_head = new_head

        # Bulk access (high volume)
        with db.bulk_writer() as writer:
            ids = writer.insert_many_returning_ids(File, file_dicts, ["path"])
            writer.insert_many(Symbol, symbol_dicts)
    """

    def __init__(self, db_path: Path) -> None:
        """Initialize database with path to SQLite file."""
        self.db_path = db_path
        self.engine = self._create_engine()

    def _create_engine(self) -> Engine:
        """Create SQLAlchemy engine with proper configuration."""
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
    def immediate_transaction(self) -> Generator[Session, None, None]:
        """
        Session with BEGIN IMMEDIATE for serializable writes.

        Use for RepoState updates to prevent race conditions.
        BEGIN IMMEDIATE acquires a RESERVED lock immediately,
        blocking other writers but allowing readers.

        The session auto-commits on successful exit and rolls back
        on exception.
        """
        with Session(self.engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

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
    """
    High-performance bulk insert using Core SQL.

    Bypasses ORM object instantiation overhead for high-volume tables.
    Uses SQLModel table metadata to avoid hardcoded SQL.

    For tables with foreign keys, use insert_many_returning_ids() to get
    parent IDs before inserting child records (Read-After-Write pattern).

    Example::

        with db.bulk_writer() as writer:
            # Insert files, get path -> id mapping
            path_to_id = writer.insert_many_returning_ids(
                File,
                [{"path": "a.py", "content_hash": "abc"}, ...],
                ["path"]
            )

            # Now insert symbols with resolved file_id
            symbols = [
                {"file_id": path_to_id[("a.py",)], "name": "foo", ...},
                ...
            ]
            writer.insert_many(Symbol, symbols)
    """

    def __init__(self, engine: Engine) -> None:
        """Initialize bulk writer with database engine."""
        self.engine = engine
        self.conn = engine.connect()
        self.transaction = self.conn.begin()

    def insert_many(self, model_class: type[SQLModel], records: list[dict[str, Any]]) -> int:
        """
        Bulk insert records into table defined by model_class.

        Args:
            model_class: SQLModel class (e.g., File, Symbol)
            records: List of dicts matching model fields

        Returns:
            Number of records inserted
        """
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
        """
        Bulk insert and return mapping of key columns to generated IDs.

        Use this for parent tables (File, Symbol) before inserting child
        tables (Occurrence, Export) that reference them via foreign keys.

        Args:
            model_class: SQLModel class
            records: List of dicts to insert
            key_columns: Columns that form the unique lookup key

        Returns:
            Dict mapping tuple(key_values) -> id

        Example::

            # Insert files, get path -> id mapping
            path_to_id = writer.insert_many_returning_ids(
                File,
                [{"path": "a.py", "content_hash": "abc"}, ...],
                ["path"]
            )
            # path_to_id = {("a.py",): 1, ("b.py",): 2, ...}

            # Now insert symbols with file_id
            symbols = [
                {"file_id": path_to_id[("a.py",)], "name": "foo", ...},
                ...
            ]
            writer.insert_many(Symbol, symbols)
        """
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
        """
        Bulk upsert (insert or update on conflict).

        Args:
            model_class: SQLModel class
            records: List of dicts
            conflict_columns: Columns that define uniqueness
            update_columns: Columns to update on conflict

        Returns:
            Number of records processed
        """
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
        """
        Bulk delete with condition.

        Returns:
            Number of rows affected
        """
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
