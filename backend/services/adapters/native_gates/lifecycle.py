"""Drain owned work before propagating caller cancellation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


async def retain_task[T](task: asyncio.Task[T], *, on_cancel: Callable[[], None] | None = None) -> T:
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            interrupted = True
            if on_cancel is not None:
                on_cancel()
    result = task.result()
    if interrupted:
        raise asyncio.CancelledError
    return result
