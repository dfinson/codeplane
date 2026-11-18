import sys

import pytest


def test_fixed_bridge_registration_cannot_promote_identity_or_observation_profiles():
    before = set(sys.modules)
    from backend.services.adapters.profile_bridges import BRIDGES, dependency_readiness

    for profile in BRIDGES:
        assert dependency_readiness(profile).ready
    for profile in ("ai-card", "generic-otel", "native-formats", "m365-agents"):
        assert profile not in BRIDGES
        with pytest.raises(ValueError, match="No app-owned runnable bridge"):
            dependency_readiness(profile)
    assert not any(name.startswith(("agentmesh", "mcp.client")) for name in set(sys.modules) - before)
    with pytest.raises(TypeError):
        BRIDGES["ai-card"] = BRIDGES["mcp"]
