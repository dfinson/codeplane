"""Shared Copilot client construction helpers."""

from __future__ import annotations

import asyncio
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
    cancelled = False
    try:
        await asyncio.wait_for(client.stop(), timeout=timeout)
        return
    except asyncio.CancelledError:
        cancelled = True
    except TimeoutError:
        log.warning("copilot_client_stop_timed_out", timeout_seconds=timeout)
    except Exception:
        log.warning("copilot_client_stop_failed_forcing", exc_info=True)
    try:
        await asyncio.wait_for(client.force_stop(), timeout=timeout)
    except TimeoutError:
        log.error("copilot_client_force_stop_timed_out", timeout_seconds=timeout)
    except Exception:
        log.error("copilot_client_force_stop_failed", exc_info=True)
    finally:
        if cancelled:
            raise asyncio.CancelledError
