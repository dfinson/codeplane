"""Database engine, session management, and migration runner."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from backend.config import CODEPLANE_DIR, DEFAULT_DB_PATH

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def get_database_url(db_path: Path | None = None) -> str:
    """Build the async SQLite database URL."""
    path = db_path or DEFAULT_DB_PATH
    return f"sqlite+aiosqlite:///{path}"


def _set_sqlite_pragmas(dbapi_conn: Any, _connection_record: Any) -> None:
    """Enable WAL mode and foreign keys for every connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine(db_path: Path | None = None) -> AsyncEngine:
    """Create an async SQLAlchemy engine."""
    url = get_database_url(db_path)
    engine = create_async_engine(url, echo=False)
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


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
    CODEPLANE_DIR.mkdir(parents=True, exist_ok=True)

    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config()
    repo_root = Path(__file__).resolve().parents[2]
    alembic_cfg.set_main_option("script_location", str(repo_root / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path or DEFAULT_DB_PATH}")
    command.upgrade(alembic_cfg, "head")
