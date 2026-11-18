"""Runtime-lifetime versus offline governance-maintenance ownership."""

from __future__ import annotations

import errno
import os
import stat
import sys
from typing import TYPE_CHECKING

from backend.models.domain import StateConflictError

if TYPE_CHECKING:
    from pathlib import Path


class GovernanceRuntimeLease:
    def __init__(self, directory: Path, *, exclusive: bool = False) -> None:
        self.directory = directory.resolve()
        self.exclusive = exclusive
        self.path = self.directory / ".governance-runtime.lock"
        self._descriptor: int | None = None

    def acquire(self) -> GovernanceRuntimeLease:
        if self._descriptor is not None:
            raise RuntimeError("Governance ownership is already held")
        self.directory.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        retained = False
        try:
            information = os.fstat(descriptor)
            current = self.path.lstat()
            if (
                not stat.S_ISREG(information.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or (information.st_dev, information.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise StateConflictError("Governance ownership requires a regular lock file")
            if information.st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    mode = msvcrt.LK_NBLCK if self.exclusive else msvcrt.LK_NBRLCK
                    msvcrt.locking(descriptor, mode, 1)
                elif os.name == "posix":
                    import fcntl

                    mode = fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH
                    fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
                else:
                    raise StateConflictError("Governance ownership is unsupported on this operating system")
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise StateConflictError("A runtime or governance maintenance owner is still active") from exc
                raise
            self._descriptor = descriptor
            self._require_current()
            retained = True
            return self
        finally:
            if not retained:
                self._descriptor = None
                os.close(descriptor)

    def _require_current(self) -> None:
        if self._descriptor is None:
            raise StateConflictError("Governance ownership has not been acquired")
        held = os.fstat(self._descriptor)
        current = self.path.lstat()
        if not stat.S_ISREG(current.st_mode) or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise StateConflictError("Governance ownership file changed; reconciliation is required")

    def require_exclusive(self) -> None:
        self._require_current()
        if not self.exclusive:
            raise StateConflictError("Governance maintenance requires exclusive stopped-runtime ownership")

    def close(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> GovernanceRuntimeLease:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.close()
