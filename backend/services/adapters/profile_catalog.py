"""Metadata-only view of the existing producers and approved profile contracts."""

from __future__ import annotations

from dataclasses import asdict
from importlib.util import find_spec
from typing import TYPE_CHECKING

import yaml

from backend.models.schemas.integrations import (
    IntegrationDependencyReadiness,
    IntegrationOperation,
    IntegrationProfileList,
    IntegrationProfileResponse,
)
from backend.services.adapters.contracts import AdapterReadiness
from backend.services.adapters.native_gates.readiness import PACKAGES
from backend.services.adapters.profile_contracts import PROFILES, SPECIFICATION_SHA256, ProfileContract
from backend.services.ingest.mapping import NATIVE_FORMATS, resolve_mapping_path
from backend.services.ingest.otel_observation import OTEL_FORMAT
from backend.services.ingest.otlp_codec import QUALIFIED_CODEC, protobuf_readiness

if TYPE_CHECKING:
    from backend.services.adapters.adapter_registry import AdapterRegistry

_GATES = {
    "langchain": "langchain",
    "langgraph": "langgraph",
    "crewai": "crewai",
    "openai-agents": "openai_agents",
    "semantic-kernel": "semantic_kernel",
    "microsoft-agent-framework": "maf",
    "smolagents": "smolagents",
    "pydantic-ai": "pydantic_ai",
}
_CLI = {
    "copilot-cli": "backend.services.ingest.copilot_source.SessionStateWatcher",
    "claude-cli": "backend.services.ingest.claude_source.ClaudeSessionStateWatcher",
}
_HISTORY = "backend.services.ingest.record_service.ObservationRecords.append"


def _native_readiness(profile: ProfileContract) -> AdapterReadiness:
    if find_spec("backend.services.adapters.native_gates") is None:
        return AdapterReadiness(False, "Native gate/Runtime producer is not present in this build")
    from backend.services.adapters.native_gates import dependency_readiness

    return dependency_readiness(_GATES[profile.id])


def _formats(profile: ProfileContract) -> dict[str, str]:
    result = {}
    for name in sorted(NATIVE_FORMATS) if profile.id == "native-formats" else profile.formats:
        path = resolve_mapping_path(name)
        if path is not None:
            result[name] = str(yaml.safe_load(path.read_text(encoding="utf-8"))["framework_version"])
    if profile.id == "generic-otel":
        result[OTEL_FORMAT] = "OTLP HTTP/JSON; GenAI attributes retained by the local span decoder"
    return result


def _operation(
    registry: AdapterRegistry, profile: ProfileContract, operation: str, formats: dict[str, str]
) -> IntegrationOperation:
    producer = None
    readiness = AdapterReadiness(False, "Selected operation has no integrated producer in this build")
    evidence: list[str] = []
    limitations: list[str] = []
    if profile.adapter_id is not None:
        producer = f"AdapterRegistry.get_adapter({profile.adapter_id!r})"
        readiness = registry.dependency_readiness(profile.adapter_id)
        limitations.append("Live credentialed managed-session acceptance is separate from descriptor readiness")
    elif operation == "observe" and formats:
        producer = _HISTORY
        readiness = AdapterReadiness(True)
        evidence = ["backend/tests/unit/test_native_format_conformance.py"]
        limitations.append("Historical record import is not a live SDK callback subscription")
        if profile.id == "generic-otel":
            producer = f"{_HISTORY}; ObservationRecords.append_otlp"
            evidence = ["backend/tests/integration/test_api_otlp.py"]
            limitations = [
                "HTTP/protobuf requires the separately version-qualified optional codec",
                "Live HTTP intake requires an enabled source credential and non-Copilot-only admission",
            ]
    elif profile.id in _CLI and operation in {"steer", "cancel"}:
        producer = _CLI[profile.id]
        readiness = AdapterReadiness(True)
        limitations.append("Requires the existing native source and current published target or hook queue")
        if operation == "cancel":
            limitations.append("Request admission is not a source-confirmed stop")
    elif profile.id == "ai-card":
        from backend.services.adapters.ai_card import dependency_readiness

        producer = "backend.services.adapters.ai_card.ProjectIdentityRegistry"
        readiness = dependency_readiness() if operation == "discover" else AdapterReadiness(True)
        evidence = ["backend/tests/unit/test_ai_card.py"]
        limitations.append("Identity evidence grants no execution, steering, tool or cancellation authority")
        limitations.append("Registration verifies supplied cards; it does not fetch arbitrary discovery URLs")
    elif profile.id in _GATES and operation in {"invoke", "gate", "cancel"}:
        readiness = _native_readiness(profile)
        if find_spec("backend.services.adapters.native_gates") is not None:
            producer = (
                "backend.services.adapters.native_gates.NativeGates"
                if operation == "gate"
                else "backend.services.runtime.RuntimeService.start_native"
                if operation == "invoke"
                else "backend.services.runtime.RuntimeService.detach_native"
            )
        limitations.append("Requires an actual Runtime-owned task, persisted Job and native execution identity")
        if operation == "cancel":
            limitations.append("Cancellation remains requested until the owned invocation ends")
    return IntegrationOperation(
        id=operation,
        implemented=producer is not None,
        producer=producer,
        dependency=IntegrationDependencyReadiness(**asdict(readiness)),
        evidence=evidence,
        limitations=limitations,
        validation_status="local-fixture-qualified" if evidence else "not-qualified",
    )


def list_profiles(registry: AdapterRegistry) -> IntegrationProfileList:
    profiles = []
    for profile in PROFILES:
        managed = registry.describe(profile.adapter_id) if profile.adapter_id is not None else None
        formats = _formats(profile)
        channels = sorted(managed.channels) if managed else ["historical-records"] if formats else []
        if profile.id == "generic-otel":
            channels.extend(("otlp-http-json", "otlp-http-protobuf"))
        operations = [_operation(registry, profile, operation, formats) for operation in profile.operations]
        if profile.id == "generic-otel":
            operations.append(
                IntegrationOperation(
                    id="observe-http-protobuf",
                    implemented=True,
                    producer="backend.services.ingest.otlp_codec.protobuf_to_json; ObservationRecords.append_otlp",
                    dependency=IntegrationDependencyReadiness(**asdict(protobuf_readiness())),
                    evidence=["backend/tests/integration/qualify_otlp_protobuf.py"],
                    validation_status="local-fixture-qualified",
                    limitations=[f"Requires {package}=={pin}" for package, pin in QUALIFIED_CODEC],
                )
            )
        profiles.append(
            IntegrationProfileResponse(
                profile_id=profile.id,
                name=profile.name,
                adapter_id=profile.adapter_id or (profile.id if profile.id in {"generic-otel", "ai-card"} else None),
                provider_family=managed.provider_family if managed else profile.id,
                acceptance_kind=profile.acceptance_kind,
                supported_versions=list(managed.supported_versions)
                if managed
                else (
                    [f"{package}=={pin}" for package, pin in PACKAGES[_GATES[profile.id]]]
                    if profile.id in _GATES
                    else ["agent-governance-toolkit-core==5.0.0"]
                    if profile.id == "ai-card"
                    else []
                ),
                native_formats=formats,
                hooks=list(managed.hooks) if managed else [],
                observation_channels=channels,
                declared_ownership=profile.ownership,
                declared_steering=profile.steering,
                declared_cancellation=profile.cancellation,
                declared_interception=profile.interception,
                identity_sources=list(managed.identity_sources)
                if managed
                else (
                    ["pinned-did", "pinned-signing-key", "pinned-service-endpoints"]
                    if profile.id == "ai-card"
                    else ["binding-id", "source-session-id", "native-source-identities"]
                    if formats
                    else []
                ),
                usage_units=sorted(managed.usage_units)
                if managed
                else (["input_tokens", "output_tokens"] if profile.id == "generic-otel" else []),
                utility_completion=managed.utility if managed else False,
                operations=operations,
                admission_enabled=registry.policy.enabled("copilot" if profile.id.startswith("copilot-") else "claude"),
                historical_import_available=bool(formats),
                source_reference=f"sha256:{SPECIFICATION_SHA256}#integration-fixtures; {profile.upstream}",
            )
        )
    return IntegrationProfileList(specification_sha256=SPECIFICATION_SHA256, profiles=profiles)
