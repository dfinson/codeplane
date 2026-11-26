"""Read-only, bounded JSONL observation with exact accepted-prefix integrity."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from pathlib import Path

MAX_RECORD_BYTES = 1024 * 1024


def _open_observed_file(path: Path) -> BinaryIO:
    if sys.platform != "win32":
        return path.open("rb", buffering=0)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Read-only observation must not prevent the producer from rotating its file.
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, "rb", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


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
        # Integrity checks must not reuse BufferedReader read-ahead after a
        # same-length in-place rewrite.
        self._file = _open_observed_file(self.path)
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
            if (
                self._checkpoint is not None
                and self._hash_prefix(self._checkpoint["byte_count"]) != self._checkpoint["sha256"]
            ):
                raise RuntimeError("Native accepted source prefix rewritten during reconstruction")
            if self._hash_prefix(self._offset) != self._digest.hexdigest():
                raise RuntimeError("Native source rewritten during observation")
            self._stamp = stamp
        self._observed_size = stat.st_size

    async def verify(self) -> None:
        await asyncio.to_thread(self._verify)

    def _verify_reconstruction(self) -> None:
        self._verify()
        if self._hash_prefix(self._offset) != self._digest.hexdigest():
            raise RuntimeError("Native consumed source prefix rewritten during reconstruction")
        if self._checkpoint is not None:
            saved = self._checkpoint
            if self._offset != saved["byte_count"]:
                raise RuntimeError("Native accepted source byte count changed during reconstruction")
            if self._digest.hexdigest() != saved["sha256"] or self._hash_prefix(saved["byte_count"]) != saved["sha256"]:
                raise RuntimeError("Native accepted source prefix rewritten during reconstruction")

    async def verify_reconstruction(self) -> None:
        """Verify both replayed bytes and fresh file contents before new progress."""
        await asyncio.to_thread(self._verify_reconstruction)

    async def read_available(self) -> bytes | None:
        """Read one complete record without polling beyond the current file boundary."""
        return await asyncio.to_thread(self._read_line)

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
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = self._file.read(min(64 * 1024, MAX_RECORD_BYTES + 1 - size))
            newline = chunk.find(b"\n")
            if newline >= 0:
                # Discard read-ahead: the next record must seek and read fresh
                # bytes after checking the accepted prefix again.
                chunk = chunk[: newline + 1]
            size += len(chunk)
            if size > MAX_RECORD_BYTES:
                raise ValueError("Native source record exceeds 1 MiB")
            if not chunk:
                # An incomplete UTF-8/JSON suffix has no accepted progress.
                return None
            chunks.append(chunk)
            if newline >= 0:
                line = b"".join(chunks)
                self._offset += size
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
