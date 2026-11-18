"""Read-only, bounded JSONL observation with exact accepted-prefix integrity."""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from pathlib import Path

MAX_RECORD_BYTES = 1024 * 1024


class NativeJsonlSource:
    def __init__(self, path: Path, *, checkpoint: dict[str, Any] | None = None) -> None:
        self.path = path
        self._file: BinaryIO | None = None
        self._offset = 0
        self._digest = hashlib.sha256()
        self._identity: tuple[int, int] | None = None
        self._observed_size = 0
        self._stamp: tuple[int, int, int] | None = None
        self._checkpoint = checkpoint

    async def __aenter__(self) -> NativeJsonlSource:
        await asyncio.to_thread(self._open)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._file is not None:
            self._file.close()

    def _open(self) -> None:
        self._file = self.path.open("rb")
        try:
            stat = os.fstat(self._file.fileno())
            self._identity = (stat.st_dev, stat.st_ino)
            self._observed_size = stat.st_size
            if self._checkpoint is not None:
                saved = self._checkpoint
                if saved["path"] != str(self.path.resolve()) or [*self._identity] != saved["identity"]:
                    raise RuntimeError("Native source rotated or its path changed before observation")
                size = saved["byte_count"]
                if not isinstance(size, int) or size < 0 or stat.st_size < size:
                    raise RuntimeError("Native source truncated before its accepted checkpoint")
                if self._hash_prefix(size) != saved["sha256"]:
                    raise RuntimeError("Native source rewritten before its accepted checkpoint")
            self._verify()
        except BaseException:
            self._file.close()
            raise

    def _hash_prefix(self, size: int) -> str:
        assert self._file is not None
        self._file.seek(0)
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            chunk = self._file.read(min(remaining, 64 * 1024))
            if not chunk:
                raise RuntimeError("Native source truncated during observation")
            digest.update(chunk)
            remaining -= len(chunk)
        return digest.hexdigest()

    def _verify(self) -> None:
        assert self._file is not None
        stat = self.path.stat()
        if (stat.st_dev, stat.st_ino) != self._identity:
            raise RuntimeError("Native source rotated during observation")
        if stat.st_size < self._observed_size:
            raise RuntimeError("Native source truncated during observation")
        stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if stamp != self._stamp:
            if self._hash_prefix(self._offset) != self._digest.hexdigest():
                raise RuntimeError("Native source rewritten during observation")
            self._stamp = stamp
        self._observed_size = stat.st_size

    async def verify(self) -> None:
        await asyncio.to_thread(self._verify)

    def checkpoint(self) -> dict[str, Any]:
        return {
            "path": str(self.path.resolve()),
            "identity": list(self._identity or ()),
            "byte_count": self._offset,
            "sha256": self._digest.hexdigest(),
        }

    def _read_line(self) -> bytes | None:
        assert self._file is not None
        self._verify()
        self._file.seek(self._offset)
        line = self._file.readline(MAX_RECORD_BYTES + 2)
        if len(line) > MAX_RECORD_BYTES:
            raise ValueError("Native source record exceeds 1 MiB")
        # A writer may have flushed an incomplete UTF-8/JSON suffix. Do not
        # count or decode it until the newline commits the physical record.
        if not line.endswith(b"\n"):
            return None
        self._offset += len(line)
        self._digest.update(line)
        return line

    def __aiter__(self) -> NativeJsonlSource:
        return self

    async def __anext__(self) -> bytes:
        while True:
            line = await asyncio.to_thread(self._read_line)
            if line is not None:
                return line
            await asyncio.sleep(0.1)
