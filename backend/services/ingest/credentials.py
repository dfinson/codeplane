"""Single-purpose source credentials, separate from operator authentication."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from typing import TYPE_CHECKING

from backend.services.ingest.otel_observation import OTEL_FORMAT

if TYPE_CHECKING:
    from backend.config import CPLConfig
    from backend.persistence.integration_repo import IntegrationBinding, IntegrationRepository

INGEST_TOKEN_HEADER = "x-codeplane-ingest-token"


class IngestCredentials:
    def __init__(self, bindings: IntegrationRepository, config: CPLConfig) -> None:
        self._bindings = bindings
        self._config = config

    async def _binding(self, binding_id: str) -> IntegrationBinding:
        binding = await self._bindings.get(binding_id)
        if (
            binding is None
            or binding.adapter_id != "generic-otel"
            or binding.mode != "observation"
            or binding.job_id is None
            or binding.config.get("format") != OTEL_FORMAT
            or not isinstance(binding.config.get("external_session_id"), str)
        ):
            raise PermissionError("A committed OTLP source binding is required")
        return binding

    @staticmethod
    def _scope(binding: IntegrationBinding) -> dict[str, str]:
        assert binding.job_id is not None
        return {
            "binding_id": binding.id,
            "project_id": binding.project_id,
            "job_id": binding.job_id,
            "source_id": binding.config["external_session_id"],
            "format": OTEL_FORMAT,
        }

    async def issue(self, binding_id: str, *, enabled: bool) -> str:
        binding = await self._binding(binding_id)
        token = secrets.token_urlsafe(32)
        credential = {
            **self._scope(binding),
            "id": uuid.uuid4().hex,
        }
        await self._bindings.set_ingest_credential(
            binding, credential, enabled=enabled, credential_hash=hashlib.sha256(token.encode("ascii")).hexdigest()
        )
        return token

    async def revoke(self, binding_id: str) -> None:
        binding = await self._binding(binding_id)
        await self._bindings.set_ingest_credential(binding, None, enabled=False)

    async def authorize(self, binding_id: str, token: str | None) -> IntegrationBinding:
        if self._config.runtime.copilot_only:
            raise PermissionError(
                "Live OTLP intake is disabled by runtime.copilot_only; historical import remains available"
            )
        binding = await self._binding(binding_id)
        credential = binding.config.get("ingest_credential")
        if not binding.enabled or not isinstance(credential, dict) or not token or len(token) > 256:
            raise PermissionError("Ingest credential is missing, invalid or disabled")
        expected = binding.credential_hash
        if (
            not isinstance(expected, str)
            or not hmac.compare_digest(hashlib.sha256(token.encode("utf-8")).hexdigest(), expected)
            or any(credential.get(key) != value for key, value in self._scope(binding).items())
        ):
            raise PermissionError("Ingest credential does not own this source")
        return binding
