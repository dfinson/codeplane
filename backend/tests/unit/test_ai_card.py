import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.models.db import IntegrationBindingRow, JobRow
from backend.models.domain import StateConflictError
from backend.persistence.identity_repo import IdentityRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.adapters.ai_card import ProjectIdentityRegistry, verify_card
from backend.tests.unit.test_approval_service import session_factory as session_factory


def _signed():
    from agentmesh.identity import AgentIdentity
    from agentmesh.integrations.ai_card import AICard, AICardService

    identity = AgentIdentity.create(name="local-card", sponsor="fixture@example.test", capabilities=["read:fixture"])
    card = AICard.from_identity(identity, services=[AICardService(protocol="a2a", url="https://peer.example.test/a2a")])
    card.expires_at = datetime.now(UTC) + timedelta(hours=1)
    card.sign(identity)
    return identity, card


def _options(identity):
    return {
        "binding_id": "identity",
        "project_id": "proj-1",
        "expected_did": str(identity.did),
        "public_key": identity.public_key,
        "services": (("a2a", "https://peer.example.test/a2a"),),
    }


@pytest.mark.parametrize("change", ["name", "expired", "key", "signature-key", "endpoint", "algorithm"])
def test_real_signed_card_rejects_tamper_expiry_keys_and_unsigned_endpoint(change):
    identity, card = _signed()
    options = _options(identity)
    verified, _ = verify_card(card.model_dump_json().encode(), **options)
    assert verified.claimed_capabilities == ("read:fixture",)
    if change == "name":
        card.name = "tampered"
    elif change == "expired":
        card.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        card.sign(identity)
    elif change == "key":
        options["public_key"] = _signed()[0].public_key
    elif change == "signature-key":
        card.card_signature.public_key = _signed()[0].public_key
    elif change == "endpoint":
        card.services[0].url = "https://untrusted.example.test/a2a"
        assert card.verify_signature(), "Upstream signature does not cover services"
    else:
        card.identity.algorithm = "untrusted"
    with pytest.raises(ValueError):
        verify_card(card.model_dump_json().encode(), **options)


async def test_identity_registry_is_project_scoped_durable_and_creates_no_job(session_factory):
    identity, card = _signed()
    async with session_factory() as session:
        initial = await session.scalar(select(func.count()).select_from(JobRow))
        repo = IdentityRepository(session)
        registry = ProjectIdentityRegistry(repo, ProjectRepository(session))
        verified = await registry.register(card.model_dump_json().encode(), **_options(identity))
        await session.commit()
        assert await session.get(IntegrationBindingRow, "identity") is None
        assert await session.scalar(select(func.count()).select_from(JobRow)) == initial
    async with session_factory() as session:
        repo = IdentityRepository(session)
        registry = ProjectIdentityRegistry(repo, ProjectRepository(session))
        assert await registry.get("identity", "proj-1") == verified
        with pytest.raises(StateConflictError, match="another owner"):
            await registry.get("identity", "different-project")
        await registry.remove("identity", "proj-1")
        await session.commit()
        assert await repo.list_for_project("proj-1") == ()
        stored = await repo.get("identity", "proj-1")
        assert not stored.enabled and json.dumps(stored.config["card"])
        with pytest.raises(StateConflictError, match="unavailable"):
            await registry.get("identity", "proj-1")
