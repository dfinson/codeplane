"""Copilot model listing with tolerant billing parsing.

The Copilot API occasionally omits the ``multiplier`` field from billing
objects in model list responses.  The SDK's ``ModelBilling.from_dict`` treats
it as required and raises ``ValueError``, which crashes ``list_models()`` and
leaves the model cache empty.

``fetch_copilot_models_raw`` fetches the raw JSON-RPC response directly,
applies ``.setdefault("multiplier", 1.0)`` before parsing, and skips
individual malformed models rather than aborting the whole list.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from backend.services.adapters.base_adapter import CLIENT_STOP_TIMEOUT_S
from backend.services.copilot_adapter._client import create_copilot_client, stop_copilot_client

log = structlog.get_logger(__name__)


async def fetch_copilot_models_raw(*, github_token: str | None = None) -> list[dict[str, Any]]:
    """Start a CopilotClient, fetch raw model dicts, and stop it.

    Tolerates missing ``billing.multiplier`` by defaulting it to ``1.0``
    before handing each dict to the SDK's ``ModelInfo.from_dict``.  Models
    that still fail to parse are skipped individually rather than aborting the
    whole list.

    Returns a list of dicts suitable for storing in ``CachedModelsBySdk``.
    """
    if os.environ.get("CODEPLANE_E2E_FAKE_AGENT") == "1":
        return [
            {
                "id": "claude-sonnet-4-5-20250514",
                "name": "Claude Sonnet 4.5",
                "billing": {"multiplier": 1.0},
            }
        ]

    from copilot.client import ModelInfo

    client = create_copilot_client(github_token=github_token) if github_token else create_copilot_client()
    try:
        await client.start()
        # pylint: disable=protected-access  # JsonRpcClient not exposed publicly
        if not client._client:  # noqa: SLF001
            raise RuntimeError("CopilotClient started but internal RPC client is not set")
        response = await client._client.request("models.list", {})  # noqa: SLF001
        models: list[dict[str, Any]] = []
        for raw in response.get("models", []):
            if isinstance(raw.get("billing"), dict):
                raw["billing"].setdefault("multiplier", 1.0)
            try:
                models.append(ModelInfo.from_dict(raw).to_dict())
            except (ValueError, KeyError, TypeError) as exc:
                log.debug("copilot_model_parse_skipped", model_id=raw.get("id"), error=str(exc))
        return models
    finally:
        await stop_copilot_client(client, timeout=CLIENT_STOP_TIMEOUT_S, explicit_account=bool(github_token))
