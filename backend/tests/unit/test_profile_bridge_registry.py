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
        ("haystack", "haystack-ai"),
        ("langflow", "lfx"),
        ("swarm", "swarm"),
    ],
)
def test_optional_native_entry_is_metadata_not_a_passing_sdk_claim(monkeypatch, profile, package):
    from backend.services.adapters import profile_bridges

    def missing(package):
        raise profile_bridges.PackageNotFoundError(package)

    monkeypatch.setattr(profile_bridges, "version", missing)
    readiness = profile_bridges.dependency_readiness(profile)
    assert not readiness.ready and package in readiness.reason


@pytest.mark.parametrize("lfx_version", ["0.4.6", "1.13.3"])
def test_langflow_readiness_requires_the_core_compatible_qualified_release(monkeypatch, lfx_version):
    from backend.services.adapters import profile_bridges

    installed_version = profile_bridges.version
    monkeypatch.setattr(
        profile_bridges, "version", lambda package: lfx_version if package == "lfx" else installed_version(package)
    )
    readiness = profile_bridges.dependency_readiness("langflow")
    assert readiness.ready is (lfx_version == "0.4.6")
    if not readiness.ready:
        assert readiness.reason == "Unsupported lfx; qualified bridge release is 0.4.6"


@pytest.mark.parametrize("profile,module", [("haystack", "haystack"), ("langflow", "lfx"), ("swarm", "swarm")])
def test_host_catalogue_does_not_load_optional_host_or_invent_live_input(monkeypatch, profile, module):
    from backend.services.adapters import profile_bridges
    from backend.services.adapters.adapter_registry import AdapterRegistry

    def missing(package):
        raise profile_bridges.PackageNotFoundError(package)

    monkeypatch.setattr(profile_bridges, "version", missing)
    before = set(sys.modules)
    row = next(row for row in AdapterRegistry().list_profiles().profiles if row.profile_id == profile)
    operations = {operation.id: operation for operation in row.operations}
    assert operations["invoke"].implemented and not operations["invoke"].dependency.ready
    assert operations["observe"].implemented and operations["gate"].implemented
    assert not operations["steer"].implemented and not row.live_ready and not row.admission_enabled
    assert module not in set(sys.modules) - before


@pytest.mark.parametrize("profile", ["autogen", "llamaindex", "google-adk"])
def test_qualified_native_client_rows_keep_boundary_input_conditional(monkeypatch, profile):
    from backend.services.adapters import profile_bridges
    from backend.services.adapters.adapter_registry import AdapterRegistry

    def missing(package):
        raise profile_bridges.PackageNotFoundError(package)

    monkeypatch.setattr(profile_bridges, "version", missing)
    before = set(sys.modules)
    row = next(row for row in AdapterRegistry().list_profiles().profiles if row.profile_id == profile)
    operations = {operation.id: operation for operation in row.operations}
    assert all(operation.implemented and not operation.dependency.ready for operation in operations.values())
    assert operations["steer"].producer.endswith("consume_native_input")
    assert "input_handler registration" in " ".join(operations["steer"].limitations)
    assert "not live steering or managed resume" in " ".join(operations["steer"].limitations)
    assert row.hooks and row.supported_versions and not row.live_ready and row.binding_enabled is None
    assert not any(name.startswith(("autogen_core", "llama_index", "google.adk")) for name in set(sys.modules) - before)
