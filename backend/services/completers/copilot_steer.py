"""Thin wrapper around the GitHub Copilot steer API.

Relays operator messages and abort commands to a published Copilot session.
The owned session source validates its task binding and current control permission.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger()

_STEER_BASE = "https://api.enterprise.githubcopilot.com/agents/tasks"


@dataclass(frozen=True)
class NativeSessionBinding:
    """Local source identity and its separately published relay target."""

    local_session_id: str
    task_id: str


class NativeDeliveryError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.uncertain = uncertain
        self.status_code = status_code


class CopilotSteerClient:
    """Relays commands to a published task after current-control validation."""

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
            log.info("copilot_steer_message_admitted", task_id=task_id)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            log.warning("copilot_steer_message_failed", task_id=task_id, status_code=status)
            raise NativeDeliveryError(f"Copilot rejected the message (HTTP {status})", status_code=status) from None
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise NativeDeliveryError("Copilot message was not admitted") from None
        except httpx.HTTPError as exc:
            log.warning("copilot_steer_message_error", task_id=task_id, error_type=type(exc).__name__)
            raise NativeDeliveryError("Copilot message delivery is unknown", uncertain=True) from None

    async def abort(self, task_id: str) -> None:
        """POST /agents/tasks/{task_id}/steer with type=abort."""
        url = f"{_STEER_BASE}/{task_id}/steer"
        try:
            resp = await self._client.post(url, json={"type": "abort"})
            resp.raise_for_status()
            log.info("copilot_steer_abort_admitted", task_id=task_id)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            log.warning("copilot_steer_abort_failed", task_id=task_id, status_code=status)
            raise NativeDeliveryError(f"Copilot rejected cancellation (HTTP {status})", status_code=status) from None
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise NativeDeliveryError("Copilot cancellation was not admitted") from None
        except httpx.HTTPError as exc:
            log.warning("copilot_steer_abort_error", task_id=task_id, error_type=type(exc).__name__)
            raise NativeDeliveryError("Copilot cancellation delivery is unknown", uncertain=True) from None

    async def check_alive(self, task_id: str) -> bool | None:
        """Return unknown without I/O: published cloud state cannot prove CLI liveness."""
        return None

    async def close(self) -> None:
        await self._client.aclose()
