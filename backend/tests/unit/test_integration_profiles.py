import sys
from unittest.mock import patch

import pytest

from backend.config import CPLConfig, RuntimeConfig
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.profile_contracts import PROFILES, SPECIFICATION_SHA256
from backend.services.ingest.mapping import NATIVE_FORMATS


def test_selected_inventory_does_not_rename_adapters_or_construct_clients():
    registry = AdapterRegistry()
    before = set(sys.modules)
    with patch.object(registry, "_create", side_effect=AssertionError("metadata constructed a client")):
        catalog = registry.list_profiles()
    assert catalog.specification_sha256 == SPECIFICATION_SHA256
    assert len(catalog.profiles) == len({row.profile_id for row in catalog.profiles}) == 32
    assert {row.profile_id for row in catalog.profiles} == {profile.id for profile in PROFILES}
    assert [row.profile_id for row in catalog.profiles if row.admission_enabled] == ["copilot-sdk", "copilot-cli"]
    assert {descriptor.id for descriptor in registry.list_descriptors()} == {"copilot", "claude"}
    assert registry._adapters == {}
    assert not any(
        name.startswith(("crewai", "langchain_core", "langgraph", "smolagents", "agentmesh", "agents", "anthropic"))
        for name in set(sys.modules) - before
    )
    profiles = {row.profile_id: row for row in catalog.profiles}
    assert profiles["copilot-sdk"].adapter_id == "copilot"
    assert profiles["claude-sdk"].adapter_id == "claude"
    assert set(profiles["native-formats"].native_formats) == NATIVE_FORMATS
    assert set(profiles["m365-agents"].native_formats) == {"maf", "maf_transcript"}
    assert profiles["microsoft-agent-framework"].native_formats == {}
    assert all(not row.live_ready and row.binding_enabled is None for row in catalog.profiles)
    with pytest.raises(ValueError, match="Unknown adapter"):
        registry.describe("copilot-sdk")


def test_support_implementation_dependency_validation_enablement_are_independent(monkeypatch):
    from backend.services.adapters.native_gates import readiness

    def missing(package):
        raise readiness.PackageNotFoundError(package)

    monkeypatch.setattr(readiness, "version", missing)
    config = CPLConfig(runtime=RuntimeConfig(copilot_only=True))
    registry = AdapterRegistry(config=config)
    profiles = {row.profile_id: row for row in registry.list_profiles().profiles}
    gate = next(op for op in profiles["langchain"].operations if op.id == "gate")
    assert gate.supported and gate.implemented and not gate.dependency.ready
    assert gate.validation_status == "not-qualified"
    assert not profiles["langchain"].admission_enabled
    assert profiles["langchain"].historical_import_available
    missing_bridge = next(op for op in profiles["swarm"].operations if op.id == "invoke")
    assert missing_bridge.supported and not missing_bridge.implemented and not missing_bridge.dependency.ready
    for profile in ("ai-card", "generic-otel", "m365-agents", "native-formats"):
        assert not {"invoke", "steer", "gate", "cancel"}.intersection(op.id for op in profiles[profile].operations)
    config.runtime.copilot_only = False
    assert all(row.admission_enabled for row in registry.list_profiles().profiles)
    assert registry._adapters == {}
    monkeypatch.setattr(readiness, "version", lambda package: "999.0.0")
    gate = next(
        op
        for row in registry.list_profiles().profiles
        if row.profile_id == "langchain"
        for op in row.operations
        if op.id == "gate"
    )
    assert not gate.dependency.ready and "Unsupported" in gate.dependency.reason


@pytest.mark.parametrize("installed", [None, "0.0.0"])
def test_optional_protobuf_readiness_fails_before_loading_codec(monkeypatch, installed):
    from backend.services.ingest import otlp_codec

    def version(package):
        if installed is None:
            raise otlp_codec.PackageNotFoundError(package)
        return installed

    monkeypatch.setattr(otlp_codec, "version", version)
    before = set(sys.modules)
    assert not otlp_codec.protobuf_readiness().ready
    with pytest.raises(otlp_codec.OtlpCodecUnavailableError):
        otlp_codec.protobuf_to_json(b"")
    assert not any(name.startswith(("opentelemetry", "google.protobuf")) for name in set(sys.modules) - before)


def test_qualified_request_bridges_are_reported_without_claiming_unqualified_transports():
    rows = {row.profile_id: row for row in AdapterRegistry().list_profiles().profiles}
    for profile in ("flowise", "mcp"):
        row = rows[profile]
        assert not row.admission_enabled and not row.live_ready
        assert row.supported_versions and row.hooks
        observed = next(operation for operation in row.operations if operation.id == "observe")
        assert observed.implemented and observed.validation_status == "local-fixture-qualified"
    mcp = rows["mcp"]
    assert all("stdio" in " ".join(operation.limitations) for operation in mcp.operations)
    assert not rows["flowise"].utility_completion and not rows["mcp"].utility_completion
