"""Pinned AI Card evidence using the actual upstream schema and verifier.

An upstream card signature does not cover service endpoints. Endpoint pins are
therefore independent trusted configuration, not permissions inferred from a card.
"""

from __future__ import annotations

import base64
import binascii
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from backend.models.domain import StateConflictError
from backend.services.action_policy.credential_filter import filter_credentials
from backend.services.adapters.contracts import AdapterReadiness
from backend.services.ingest.otel_decoder import decode_json_body
from backend.validators import validate_integration_binding_id

if TYPE_CHECKING:
    from backend.persistence.identity_repo import IdentityRepository
    from backend.persistence.project_repo import ProjectRepository


def dependency_readiness() -> AdapterReadiness:
    try:
        installed = version("agent-governance-toolkit-core")
    except PackageNotFoundError:
        return AdapterReadiness(False, "Missing agent-governance-toolkit-core")
    ready = installed == "5.0.0"
    return AdapterReadiness(ready, None if ready else "AI Card bridge requires qualified core 5.0.0", installed)


@dataclass(frozen=True, slots=True)
class VerifiedCard:
    binding_id: str
    project_id: str
    did: str
    version: str
    expires_at: datetime
    public_key: str
    services: tuple[tuple[str, str], ...]
    claimed_capabilities: tuple[str, ...]


def _key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("AI Card key must be canonical base64 Ed25519") from exc
    if len(decoded) != 32:
        raise ValueError("AI Card key must be 32-byte Ed25519")
    return decoded


def verify_card(
    document: bytes,
    *,
    binding_id: str,
    project_id: str,
    expected_did: str,
    public_key: str,
    services: tuple[tuple[str, str], ...],
    now: datetime | None = None,
) -> tuple[VerifiedCard, dict[str, Any]]:
    validate_integration_binding_id(binding_id)
    readiness = dependency_readiness()
    if not readiness.ready:
        raise RuntimeError(readiness.reason)
    from agentmesh.integrations.ai_card import AICard, AICardDiscovery

    card = AICard.model_validate(decode_json_body(document))
    instant = now if now is not None else datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("Card verification requires an aware UTC instant")
    if (
        card.expires_at is None
        or card.expires_at.tzinfo is None
        or card.expires_at <= instant
        or card.created_at.tzinfo is None
        or card.created_at > instant
    ):
        raise ValueError("AI Card must have a current, explicit signed validity interval")
    if card.identity is None or card.card_signature is None or card.identity.did != expected_did:
        raise ValueError("AI Card does not match the pinned identity")
    expected_key = _key(public_key)
    if (
        card.identity.algorithm != "Ed25519"
        or card.card_signature.algorithm != "Ed25519"
        or not hmac.compare_digest(_key(card.identity.public_key), expected_key)
        or not hmac.compare_digest(_key(card.card_signature.public_key), expected_key)
    ):
        raise ValueError("AI Card does not match the pinned signing key")
    actual_services = tuple(sorted((service.protocol, service.url) for service in card.services))
    if len(set(actual_services)) != len(actual_services) or actual_services != tuple(sorted(services)):
        raise ValueError("AI Card services do not match independently pinned endpoints")
    for protocol, url in services:
        parsed = urlsplit(url)
        if (
            not protocol
            or parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or filter_credentials(url).redacted_semantics
        ):
            raise ValueError("Pinned AI Card service is not a credential-free HTTP endpoint")
    discovery = AICardDiscovery()
    if not discovery.register(card, verify=True):
        raise ValueError("AI Card signature verification failed")
    return (
        VerifiedCard(
            binding_id,
            project_id,
            expected_did,
            card.version,
            card.expires_at,
            public_key,
            actual_services,
            tuple(sorted(item.capability for item in card.verifiable.capability_attestations.values())),
        ),
        card.model_dump(mode="json"),
    )


class ProjectIdentityRegistry:
    def __init__(self, identities: IdentityRepository, projects: ProjectRepository) -> None:
        self._identities = identities
        self._projects = projects

    async def register(
        self,
        document: bytes,
        *,
        binding_id: str,
        project_id: str,
        expected_did: str,
        public_key: str,
        services: tuple[tuple[str, str], ...],
    ) -> VerifiedCard:
        if await self._projects.get(project_id) is None:
            raise ValueError("Identity registration requires an existing Project")
        verified, card = verify_card(
            document,
            binding_id=binding_id,
            project_id=project_id,
            expected_did=expected_did,
            public_key=public_key,
            services=services,
        )
        await self._identities.put(
            binding_id,
            project_id,
            {"card": card, "expected_did": expected_did, "public_key": public_key, "services": services},
        )
        return verified

    async def get(self, binding_id: str, project_id: str) -> VerifiedCard:
        from backend.services.ingest.mapping import canonical_record

        binding = await self._identities.get(binding_id, project_id)
        if binding is None or not binding.enabled:
            raise StateConflictError("Identity binding is unavailable")
        config = binding.config
        verified, _ = verify_card(
            canonical_record(config["card"]),
            binding_id=binding_id,
            project_id=project_id,
            expected_did=config["expected_did"],
            public_key=config["public_key"],
            services=tuple(tuple(service) for service in config["services"]),
        )
        return verified

    async def remove(self, binding_id: str, project_id: str) -> None:
        await self._identities.disable(binding_id, project_id)
