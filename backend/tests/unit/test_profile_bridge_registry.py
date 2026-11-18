import sys

import pytest


def test_fixed_bridge_registration_cannot_promote_identity_or_observation_profiles():
    before = set(sys.modules)
    from backend.services.adapters.profile_bridges import BRIDGES, dependency_readiness

    for profile in ("flowise", "mcp"):
        assert dependency_readiness(profile).ready
    for profile in ("ai-card", "generic-otel", "native-formats", "m365-agents"):
        assert profile not in BRIDGES
        with pytest.raises(ValueError, match="No app-owned runnable bridge"):
            dependency_readiness(profile)
    assert not any(name.startswith(("agentmesh", "mcp.client")) for name in set(sys.modules) - before)
    with pytest.raises(TypeError):
        BRIDGES["ai-card"] = BRIDGES["mcp"]


@pytest.mark.parametrize(
    "profile,package",
    [
        ("autogen", "autogen-core"),
        ("llamaindex", "llama-index-core"),
        ("google-adk", "google-adk"),
    ],
)
def test_optional_native_entry_is_metadata_not_a_passing_sdk_claim(monkeypatch, profile, package):
    from backend.services.adapters import profile_bridges

    def missing(package):
        raise profile_bridges.PackageNotFoundError(package)

    monkeypatch.setattr(profile_bridges, "version", missing)
    readiness = profile_bridges.dependency_readiness(profile)
    assert not readiness.ready and package in readiness.reason
