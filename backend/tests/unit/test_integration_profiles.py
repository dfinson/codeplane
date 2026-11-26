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
    missing_input = next(op for op in profiles["swarm"].operations if op.id == "steer")
    assert missing_input.supported and not missing_input.implemented
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


def test_qualified_request_bridges_report_actual_protocol_evidence():
    rows = {row.profile_id: row for row in AdapterRegistry().list_profiles().profiles}
    for profile in ("flowise", "mcp", "a2a"):
        row = rows[profile]
        assert not row.admission_enabled and not row.live_ready
        assert row.supported_versions and row.hooks
        observed = next(operation for operation in row.operations if operation.id == "observe")
        assert observed.implemented and observed.validation_status == "local-fixture-qualified"
    mcp = rows["mcp"]
    assert all("stdio" in " ".join(operation.limitations) for operation in mcp.operations)
    assert "TrustGatedMCPServer.invoke_tool" in mcp.hooks
    assert all(
        "backend/tests/native_gates/qualify_mcp_transports.py" in operation.evidence for operation in mcp.operations
    )
    assert not rows["flowise"].utility_completion and not rows["mcp"].utility_completion
    a2a = rows["a2a"]
    cancel = next(operation for operation in a2a.operations if operation.id == "cancel")
    assert cancel.producer.endswith("A2ATarget.cancel_task")
    assert "only stops local waiting" in " ".join(cancel.limitations)


def test_external_gate_is_blocking_only_and_readiness_requires_its_modules(monkeypatch):
    from backend.services.adapters import profile_catalog
    from backend.services.adapters.profile_bridges import BRIDGES

    row = next(row for row in AdapterRegistry().list_profiles().profiles if row.profile_id == "external-gate")
    operations = {operation.id: operation for operation in row.operations}
    assert set(operations) == {"gate", "observe"} and "external-gate" not in BRIDGES
    assert operations["gate"].producer.endswith("ExternalGates.before_call")
    assert operations["observe"].producer.endswith("ExternalGates.reported")
    assert all(operation.dependency.ready and operation.implemented for operation in operations.values())
    assert not row.admission_enabled and not row.live_ready and row.binding_enabled is None
    assert row.supported_versions == ["codeplane-external-gate-v1"]
    assert "Reports are observational, not accounting proof" in operations["observe"].limitations

    class Absent:
        def joinpath(self, path):
            return self

        def is_file(self):
            return False

    monkeypatch.setattr(profile_catalog, "files", lambda package: Absent())
    absent = next(row for row in AdapterRegistry().list_profiles().profiles if row.profile_id == "external-gate")
    assert all(not operation.implemented and not operation.dependency.ready for operation in absent.operations)


def test_retained_gate_boundary_input_is_not_unconditional_live_steering():
    from backend.services.adapters.profile_catalog import _GATES

    rows = {row.profile_id: row for row in AdapterRegistry().list_profiles().profiles}
    for profile in _GATES:
        operation = next(operation for operation in rows[profile].operations if operation.id == "steer")
        assert operation.implemented and operation.producer.endswith("consume_native_input")
        assert "input_handler registration" in " ".join(operation.limitations)
        assert operation.evidence == ["backend/tests/native_gates/qualify_boundary_input.py"]
        assert not rows[profile].live_ready and rows[profile].binding_enabled is None
