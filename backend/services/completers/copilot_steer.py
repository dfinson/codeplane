"""Thin wrapper around the GitHub Copilot steer API.

Sends steering commands (operator messages, abort) to a Copilot CLI
``--remote`` session via the GitHub cloud relay.
"""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger()

_STEER_BASE = "https://api.enterprise.githubcopilot.com/agents/tasks"


class NativeDeliveryError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


class CopilotSteerClient:
    """Sends steering commands to a Copilot CLI --remote session."""

    def __init__(self, github_token: str) -> None:
        self._token = github_token
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
            headers={
                "Authorization": f"Bearer {github_token}",
                "Content-Type": "application/json",
            },
        )

    async def send_message(self, task_id: str, message: str) -> None:
        """POST /agents/tasks/{task_id}/steer with type=user."""
        url = f"{_STEER_BASE}/{task_id}/steer"
        try:
            resp = await self._client.post(url, json={"content": message, "type": "user"})
            resp.raise_for_status()
            log.info("copilot_steer_message_sent", task_id=task_id)
        except httpx.HTTPStatusError:
            log.warning("copilot_steer_message_failed", task_id=task_id, exc_info=True)
            raise NativeDeliveryError("Copilot rejected the message") from None
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise NativeDeliveryError("Copilot message was not admitted") from None
        except httpx.HTTPError:
            log.warning("copilot_steer_message_error", task_id=task_id, exc_info=True)
            raise NativeDeliveryError("Copilot message delivery is unknown", uncertain=True) from None

    async def abort(self, task_id: str) -> None:
        """POST /agents/tasks/{task_id}/steer with type=abort."""
        url = f"{_STEER_BASE}/{task_id}/steer"
        try:
            resp = await self._client.post(url, json={"type": "abort"})
            resp.raise_for_status()
            log.info("copilot_steer_abort_sent", task_id=task_id)
        except httpx.HTTPStatusError:
            log.warning("copilot_steer_abort_failed", task_id=task_id, exc_info=True)
            raise NativeDeliveryError("Copilot rejected cancellation") from None
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise NativeDeliveryError("Copilot cancellation was not admitted") from None
        except httpx.HTTPError:
            log.warning("copilot_steer_abort_error", task_id=task_id, exc_info=True)
            raise NativeDeliveryError("Copilot cancellation delivery is unknown", uncertain=True) from None

    async def check_alive(self, task_id: str) -> bool | None:
        """GET /agents/tasks/{task_id} — returns True if the session exists (200), False on 404."""
        url = f"{_STEER_BASE}/{task_id}"
        try:
            resp = await self._client.get(url)
            if resp.status_code == 404:
                return False
            return True if resp.status_code == 200 else None
        except httpx.HTTPError:
            log.debug("copilot_steer_check_alive_error", task_id=task_id, exc_info=True)
            # Network error — assume alive to avoid false positives
            return None

    async def close(self) -> None:
        await self._client.aclose()
