"""Nonblocking native-source ownership shared by observers and explicit conversion."""

from __future__ import annotations

import errno
import hashlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine

from backend.models.domain import StateConflictError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_memory_owners: set[tuple[int, str]] = set()


@contextmanager
def hold_native_source(factory: async_sessionmaker[AsyncSession], job_id: str) -> Iterator[None]:
    """Require a quiescent observer; lock files are not deleted while peers may hold them."""
    engine = factory.kw.get("bind")
    if not isinstance(engine, AsyncEngine):
        raise ValueError("Native ownership requires an explicitly bound database engine")
    database = engine.url.database
    if database is None or database in ("", ":memory:"):
        key = (id(engine), job_id)
        if key in _memory_owners:
            raise StateConflictError("Native source is already owned by an observer or converter")
        _memory_owners.add(key)
        try:
            yield
        finally:
            _memory_owners.remove(key)
        return
    database_path = Path(database).resolve()
    directory = database_path.parent / ".codeplane-native-locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    identity = hashlib.sha256(f"{os.path.normcase(str(database_path))}\0{job_id}".encode()).hexdigest()
    with (directory / f"{identity}.lock").open("a+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                raise StateConflictError("Native source is already owned by an observer or converter") from exc
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StateConflictError("Native source is already owned by an observer or converter") from exc
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
