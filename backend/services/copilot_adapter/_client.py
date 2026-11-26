"""Shared Copilot client construction helpers."""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from copilot import CopilotClient

log = structlog.get_logger()
_explicit_teardown: ContextVar[bool] = ContextVar("copilot_explicit_teardown", default=False)


class _TeardownDiagnosticFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _explicit_teardown.get():
            record.msg = "Copilot SDK client diagnostic withheld for explicit inference account"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


for _sdk_logger in ("copilot.client", "copilot._jsonrpc"):
    logging.getLogger(_sdk_logger).addFilter(_TeardownDiagnosticFilter())


def create_copilot_client(*, github_token: str | None = None) -> CopilotClient:
    """Build a Copilot client."""
    from copilot import CopilotClient

    if github_token is not None:
        return CopilotClient(github_token=github_token, use_logged_in_user=False)
    return CopilotClient()


async def stop_copilot_client(client: CopilotClient, *, timeout: float, explicit_account: bool = False) -> None:
    """Bound both graceful and forced SDK process teardown."""
    token = _explicit_teardown.set(explicit_account)
    try:
        await _stop_copilot_client(client, timeout=timeout, explicit_account=explicit_account)
    finally:
        _explicit_teardown.reset(token)


async def _stop_copilot_client(client: CopilotClient, *, timeout: float, explicit_account: bool) -> None:
    cancelled = False
    try:
        await asyncio.wait_for(client.stop(), timeout=timeout)
        return
    except asyncio.CancelledError:
        cancelled = True
    except TimeoutError:
        log.warning("copilot_client_stop_timed_out", timeout_seconds=timeout)
    except Exception:
        log.warning("copilot_client_stop_failed_forcing", exc_info=not explicit_account)
    try:
        await asyncio.wait_for(client.force_stop(), timeout=timeout)
    except TimeoutError:
        log.error("copilot_client_force_stop_timed_out", timeout_seconds=timeout)
        if explicit_account:
            raise TimeoutError("Copilot client shutdown timed out") from None
        raise
    except Exception:
        log.error("copilot_client_force_stop_failed", exc_info=not explicit_account)
        if explicit_account:
            raise RuntimeError("Copilot client shutdown failed") from None
        raise
    finally:
        if cancelled:
            raise asyncio.CancelledError
