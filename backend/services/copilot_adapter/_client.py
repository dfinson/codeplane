"""Shared Copilot client construction helpers."""

from __future__ import annotations

import asyncio
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from copilot import CopilotClient

log = structlog.get_logger()


def create_copilot_client() -> CopilotClient:
    """Build a Copilot client."""
    from copilot import CopilotClient

    return CopilotClient()


async def stop_copilot_client(client: CopilotClient, *, timeout: float) -> None:
    """Bound both graceful and forced SDK process teardown."""
    from copilot._jsonrpc import JsonRpcError, ProcessExitedError
    from copilot.client import StopError

    cancelled = False
    try:
        await asyncio.wait_for(client.stop(), timeout=timeout)
        return
    except asyncio.CancelledError:
        cancelled = True
    except TimeoutError:
        log.warning("copilot_client_stop_timed_out", timeout_seconds=timeout)
    except (ProcessExitedError, JsonRpcError, OSError):
        log.warning("copilot_client_stop_failed_forcing", exc_info=True)
    except ExceptionGroup as exc:
        _, unexpected = exc.split(StopError)
        if unexpected is not None:
            raise
        log.warning("copilot_client_stop_failed_forcing", exc_info=True)
    try:
        await asyncio.wait_for(client.force_stop(), timeout=timeout)
    except (TimeoutError, TimeoutExpired, ProcessExitedError, JsonRpcError, OSError):
        log.error("copilot_client_force_stop_failed", exc_info=True)
        raise
    finally:
        if cancelled:
            raise asyncio.CancelledError
