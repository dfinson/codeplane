"""Database engine, session management, and migration runner."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from backend import config as backend_config

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# SQLite busy_timeout: how long a connection waits for a locked database
# before raising OperationalError. 15s provides headroom for write queue
# draining under burst traffic while the application-level write lock
# serializes concurrent writers.
_SQLITE_BUSY_TIMEOUT_MS = 15_000

# SQLAlchemy connection pool sizing for the async SQLite engine.
# SQLite supports unlimited concurrent readers in WAL mode, but only one
# writer.  Writes are serialized through the global write lock.
# SQLite connections are cheap file handles — there is no reason to cap
# overflow; max_overflow=-1 lets the pool grow on demand so background
# recovery, event-bus subscribers, and API requests never starve each other.
_POOL_SIZE = 5
_POOL_MAX_OVERFLOW = -1
_POOL_TIMEOUT_S = 60


def get_database_url(db_path: Path | None = None) -> str:
    """Build the async SQLite database URL."""
    path = db_path or (backend_config.get_codeplane_dir() / "data.db")
    return f"sqlite+aiosqlite:///{path}"


def _set_sqlite_pragmas(dbapi_conn: Any, _connection_record: Any) -> None:
    """Enable WAL mode and foreign keys for every connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def create_engine(db_path: Path | None = None) -> AsyncEngine:
    """Create an async SQLAlchemy engine."""
    url = get_database_url(db_path)
    engine = create_async_engine(
        url,
        echo=False,
        pool_size=_POOL_SIZE,
        max_overflow=_POOL_MAX_OVERFLOW,
        pool_timeout=_POOL_TIMEOUT_S,
    )
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Global write serializer
# ---------------------------------------------------------------------------
# SQLite supports only ONE concurrent writer (even in WAL mode). Rather than
# relying on busy_timeout to queue writers at the OS level (which produces
# opaque OperationalError on timeout), we serialize all writes at the
# application layer through a single asyncio.Lock. This eliminates lock
# contention entirely — writers queue cooperatively in Python.

_write_lock: asyncio.Lock | None = None


def get_write_lock() -> asyncio.Lock:
    """Return the global SQLite write lock (created lazily, once per process)."""
    global _write_lock  # noqa: PLW0603
    if _write_lock is None:
        _write_lock = asyncio.Lock()
    return _write_lock


@asynccontextmanager
async def serialized_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Acquire the global write lock and yield a session that commits on exit.

    All database writes should go through this context manager to avoid
    SQLite lock contention. The session is committed on clean exit and
    rolled back on exception.
    """
    async with get_write_lock(), session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session; rolls back on exception, always closes."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def run_migrations(db_path: Path | None = None) -> None:
    """Run Alembic migrations programmatically at startup."""
    backend_config.get_codeplane_dir().mkdir(parents=True, exist_ok=True)

    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config()
    repo_root = Path(__file__).resolve().parents[2]
    alembic_cfg.set_main_option("script_location", str(repo_root / "alembic"))
    db_url = f"sqlite:///{db_path or (backend_config.get_codeplane_dir() / 'data.db')}"
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(alembic_cfg, "head")
