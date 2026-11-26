"""Explicit destination-scoped export configuration and existing credential reuse."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from backend.services.action_policy.credential_filter import filter_credentials
from backend.services.credentials.encryption import derive_export_identity_key

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.config import ExportConfig
    from backend.persistence.credential_repo import CredentialRepository


class ExportConfigurationError(ValueError):
    """Invalid optional configuration, never a reason to fail local work."""


def destination_scope(endpoint: str, protocol: str) -> str:
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or protocol not in {"http/protobuf", "grpc"}:
        raise ExportConfigurationError("invalid_destination")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ExportConfigurationError("invalid_destination") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or any(ord(character) < 33 or ord(character) == 127 for character in endpoint)
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or filter_credentials(endpoint).redacted_semantics
        or (protocol == "grpc" and parsed.path not in {"", "/"})
    ):
        raise ExportConfigurationError("invalid_destination")
    effective_port = port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    return f"{protocol}|{parsed.scheme}://{parsed.hostname.lower()}:{effective_port}{path}"


def credential_headers(secret: str) -> dict[str, str]:
    if len(secret.encode("utf-8")) > 8192:
        raise ExportConfigurationError("credential_representation_too_large")
    try:
        headers = json.loads(secret)
    except (ValueError, RecursionError) as exc:
        raise ExportConfigurationError("invalid_credential_headers") from exc
    if not isinstance(headers, dict) or len(headers) > 32:
        raise ExportConfigurationError("invalid_credential_headers")
    forbidden = {
        "host",
        "content-type",
        "content-length",
        "connection",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
    }
    normalized = {}
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", name) is None
            or not isinstance(value, str)
            or not value.isascii()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or name.lower() in forbidden
            or name.lower() in normalized
        ):
            raise ExportConfigurationError("invalid_credential_headers")
        normalized[name.lower()] = value
    return normalized


def encode_export_credential(protocol: str, headers_json: str) -> str:
    if protocol not in {"http/protobuf", "grpc"}:
        raise ExportConfigurationError("invalid_protocol")
    return json.dumps({"version": 1, "protocol": protocol, "headers": credential_headers(headers_json)})


def decode_export_credential(secret: str, protocol: str) -> dict[str, str]:
    if len(secret.encode()) > 16 * 1024:
        raise ExportConfigurationError("credential_representation_too_large")
    try:
        envelope = json.loads(secret)
    except (ValueError, RecursionError) as exc:
        raise ExportConfigurationError("invalid_export_credential") from exc
    if not isinstance(envelope, dict) or envelope.get("version") != 1 or envelope.get("protocol") != protocol:
        raise ExportConfigurationError("credential_protocol_mismatch")
    return credential_headers(json.dumps(envelope.get("headers")))


@dataclass(frozen=True, slots=True)
class ExportDestination:
    endpoint: str
    protocol: str
    headers: Mapping[str, str]
    credential_id: str | None


@dataclass(frozen=True, slots=True)
class ExportBinding:
    trace: ExportDestination
    metrics: ExportDestination | None
    identity_key: bytes
    approved_models: frozenset[str]


async def resolve_binding(config: ExportConfig, credentials: CredentialRepository) -> ExportBinding | None:
    if config.enabled is False:
        return None
    if config.enabled is not True:
        raise ExportConfigurationError("invalid_enablement")

    async def destination(endpoint: str, protocol: str, credential_id: str | None) -> ExportDestination:
        scope = destination_scope(endpoint, protocol)
        headers: dict[str, str] = {}
        if credential_id is not None:
            if not isinstance(credential_id, str) or not credential_id:
                raise ExportConfigurationError("invalid_credential_reference")
            credential = await credentials.get(credential_id)
            if credential is None or credential["provider"] != "otlp":
                raise ExportConfigurationError("unavailable_export_credential")
            if destination_scope(credential["base_url"], protocol) != scope:
                raise ExportConfigurationError("credential_destination_mismatch")
            secret = await credentials.resolve_secret(credential_id)
            if secret is None:
                raise ExportConfigurationError("unavailable_export_credential")
            headers = decode_export_credential(secret, protocol)
        return ExportDestination(endpoint, protocol, MappingProxyType(headers), credential_id)

    trace = await destination(config.endpoint, config.protocol, config.credential_id)
    metrics = None
    if config.metrics_endpoint:
        metrics = await destination(config.metrics_endpoint, "grpc", config.metrics_credential_id)
    elif config.metrics_credential_id is not None:
        raise ExportConfigurationError("metrics_destination_required")
    if not isinstance(config.approved_models, list) or any(
        not isinstance(model, str) or not model or len(model) > 128 for model in config.approved_models
    ):
        raise ExportConfigurationError("invalid_model_allowlist")
    return ExportBinding(
        trace,
        metrics,
        derive_export_identity_key(destination_scope(config.endpoint, config.protocol)),
        frozenset(config.approved_models),
    )
