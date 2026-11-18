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
_CLIENTS = {"openai-client", "anthropic-client", "gemini-client", "mistral-client", "bedrock-client"}


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
    elif profile.id in _CLIENTS:
        from backend.services.adapters.native_gates.selection import select_installation

        selected = select_installation(profile.id)
        if operation in {"invoke", "gate", "observe", "cancel", "steer"}:
            readiness = selected.readiness()
            producer = (
                "backend.services.runtime.RuntimeService.start_native"
                if operation == "invoke"
                else f"{selected.module}.install"
                if operation in {"gate", "steer"}
                else "backend.services.runtime.native_observation.open_native_observation"
                if operation == "observe"
                else "backend.services.runtime.RuntimeService.detach_native"
            )
            evidence = [
                "backend/tests/native_gates/qualify_client_profiles.py",
                "backend/tests/native_gates/qualify_client_usage.py",
            ]
        limitations.extend(
            (
                "Requires an explicitly supplied pinned SDK client and current persisted Runtime ownership",
                "Only configured requests and supplied local functions are gated; hosted provider tools remain remote",
                "Steering is a new admitted native request/turn; no unacknowledged live-input callback is claimed",
                "Retries must be disabled; missing usage/IDs remain unknown and currency is never inferred",
            )
        )
        if profile.id == "openai-client":
            limitations.append(
                "Explicit native runs.cancel retains the returned run state; Runtime detach only stops local waiting"
            )
        else:
            limitations.append("Cancellation stops local waiting, not unconfirmed remote provider execution")
        if profile.id == "bedrock-client":
            limitations.append(
                "invoke_agent owns consumption of up to 1000 events/1 MiB and returns finite native-event replay"
            )
        else:
            limitations.append("Streaming is refused before dispatch; only complete response requests are qualified")
        if profile.id == "gemini-client":
            limitations.append("Native automatic function calling must be explicitly disabled")
    elif profile.id in {"haystack", "langflow", "swarm"}:
        from backend.services.adapters.native_gates.selection import select_installation

        selected = select_installation(profile.id)
        readiness = selected.readiness()
        if operation in selected.operations:
            producer = (
                "backend.services.runtime.RuntimeService.start_native"
                if operation == "invoke"
                else "backend.services.runtime.RuntimeService.detach_native"
                if operation == "cancel"
                else f"{selected.module}.install"
            )
            evidence = [f"backend/tests/native_gates/qualify_{profile.id}.py"]
        limitations.extend(
            (
                "The caller-owned host schedules components with blocking trust and policy",
                "Sync and async handlers remain owned until completion; revocation is not stop acknowledgment",
                "No live-input callback or inferred model usage is provided by the component bridge",
            )
        )
        if profile.id == "langflow":
            limitations.append("LFX host execution is required; exported trust configuration is not a runnable")
        if profile.id == "swarm":
            limitations.append("Original Swarm dispatch checks exact bound peers before handoff context transfer")
            limitations.append("Only source-present completion usage is retained; missing stream usage stays unknown")
    elif profile.id in {"flowise", "mcp", "a2a"}:
        from backend.services.adapters.native_gates.selection import select_installation

        selected = select_installation(profile.id)
        readiness = selected.readiness()
        if operation in selected.operations:
            producer = (
                "backend.services.runtime.RuntimeService.start_native"
                if operation == "invoke"
                else f"{selected.module}.install"
                if operation == "gate"
                else "backend.services.runtime.native_observation.open_native_observation"
                if operation == "observe"
                else "backend.services.runtime.RuntimeService.detach_native"
            )
            if profile.id == "a2a" and operation in {"steer", "cancel"}:
                method = "send_message" if operation == "steer" else "cancel_task"
                producer = f"{selected.module}.A2ATarget.{method}"
            evidence = [
                f"backend/tests/unit/test_{profile.id}_bridge.py",
                "backend/tests/unit/test_profile_bridge_revocation.py",
            ]
        limitations.append(
            "Requires the existing persisted Runtime admission, exact execution identity and native binding"
        )
        limitations.append("Raw native results are retained; undocumented token or currency conversion is not inferred")
        if profile.id == "flowise":
            limitations.append("Stop is local: in-flight synchronous requests drain without claiming remote abort")
        elif profile.id == "mcp":
            limitations.append(
                "Streamable HTTP client qualified; stdio and upstream server trust middleware are not qualified"
            )
            limitations.append(
                "MCP cancellation is requested until native acknowledgment or a separately observed result"
            )
        else:
            limitations.append("Task input requires a source-confirmed input-required task/context")
            limitations.append(
                "Explicit tasks/cancel records the returned native state; Runtime revocation only stops local waiting"
            )
            limitations.append(
                "Uncertain request delivery is not automatically retried; tasks/get reconciles known task IDs"
            )
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
        bridge_versions: list[str] = []
        bridge_hooks: list[str] = []
        if profile.id in {"flowise", "mcp", "a2a"}:
            from backend.services.adapters.native_gates.selection import select_installation

            selected = select_installation(profile.id)
            bridge_versions = [f"{package}=={pin}" for package, pin in selected.dependencies]
            bridge_hooks = (
                ["predict", "upsert"]
                if profile.id == "flowise"
                else ["ClientSession.call_tool"]
                if profile.id == "mcp"
                else ["message/send", "message/stream", "tasks/get", "tasks/cancel"]
            )
            if profile.id == "a2a":
                bridge_versions.append("A2A JSON-RPC 0.3.0")
            channels.extend(("native-request-boundary", "pinned-canonical-observations"))
        if profile.id in _CLIENTS:
            from backend.services.adapters.native_gates.selection import select_installation

            bridge_versions = [f"{package}=={pin}" for package, pin in select_installation(profile.id).dependencies]
            channels.append("native-client-request-and-local-function")
        if profile.id in {"haystack", "langflow", "swarm"}:
            from backend.services.adapters.native_gates.selection import select_installation

            selected = select_installation(profile.id)
            bridge_versions = [f"{package}=={pin}" for package, pin in selected.dependencies]
            bridge_hooks = (
                ["Pipeline.run", "Pipeline.run_async", "component.run", "component.run_async"]
                if profile.id == "haystack"
                else ["Graph.async_start", "Component output methods", "TrustGatedFlow.verify_connection"]
                if profile.id == "langflow"
                else ["Swarm.run", "Swarm.run_and_stream", "handle_tool_calls", "HandoffVerifier.verify_handoff"]
            )
            channels.extend(("native-component-boundary", "pinned-canonical-observations"))
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
                    else bridge_versions
                ),
                native_formats=formats,
                hooks=list(managed.hooks) if managed else bridge_hooks,
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
                else (
                    ["input_tokens", "output_tokens"] if profile.id == "generic-otel" or profile.id in _CLIENTS else []
                ),
                utility_completion=managed.utility if managed else False,
                operations=operations,
                admission_enabled=registry.policy.enabled("copilot" if profile.id.startswith("copilot-") else "claude"),
                historical_import_available=bool(formats),
                source_reference=f"sha256:{SPECIFICATION_SHA256}#integration-fixtures; {profile.upstream}",
            )
        )
    return IntegrationProfileList(specification_sha256=SPECIFICATION_SHA256, profiles=profiles)
