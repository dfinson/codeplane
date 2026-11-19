"""Per-job admission fencing shared by runtime and permission creation."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass
class _Fence:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    owner: asyncio.Task[object] | None = None
    users: int = 0


class ExecutionControl:
    def __init__(self) -> None:
        self._fences: dict[str, _Fence] = {}

    @asynccontextmanager
    async def hold(self, job_id: str) -> AsyncIterator[None]:
        fence = self._fences.setdefault(job_id, _Fence())
        task = asyncio.current_task()
        if fence.owner is task and task is not None:
            yield
            return
        fence.users += 1
        try:
            async with fence.lock:
                fence.owner = task
                try:
                    yield
                finally:
                    fence.owner = None
        finally:
            fence.users -= 1
            if fence.users == 0:
                self._fences.pop(job_id, None)
