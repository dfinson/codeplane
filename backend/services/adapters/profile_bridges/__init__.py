"""Version-qualified native request bridges, never managed SDK sessions."""

from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType

from backend.services.adapters.contracts import AdapterReadiness
from backend.services.adapters.native_gates.bridge_contract import BridgeSpec

BRIDGES = MappingProxyType(
    {
        "autogen": BridgeSpec(
            id="autogen",
            module="backend.services.adapters.profile_bridges.autogen",
            dependencies=(("autogen-core", "0.7.5"),),
            operations=frozenset({"send_message", "function_call"}),
            observation_mapping=True,
        ),
        "llamaindex": BridgeSpec(
            id="llamaindex",
            module="backend.services.adapters.profile_bridges.llamaindex",
            dependencies=(("llama-index-core", "0.14.26"),),
            operations=frozenset({"query", "chat", "function_call"}),
            observation_mapping=True,
        ),
        "google-adk": BridgeSpec(
            id="google-adk",
            module="backend.services.adapters.profile_bridges.google_adk",
            dependencies=(("google-adk", "2.14.1"), ("google-genai", "2.34.0")),
            operations=frozenset({"run", "model_request", "function_call"}),
            observation_mapping=True,
        ),
        "flowise": BridgeSpec(
            id="flowise",
            module="backend.services.adapters.profile_bridges.flowise",
            dependencies=(("agent-governance-toolkit-core", "5.0.0"), ("httpx", "0.28.1")),
            operations=frozenset({"invoke", "gate", "observe", "stop-waiting"}),
            observation_mapping=True,
        ),
        "a2a": BridgeSpec(
            id="a2a",
            module="backend.services.adapters.profile_bridges.a2a",
            dependencies=(("agent-governance-toolkit-core", "5.0.0"), ("httpx", "0.28.1")),
            operations=frozenset({"invoke", "observe", "steer", "cancel", "gate"}),
            observation_mapping=True,
        ),
        "mcp": BridgeSpec(
            id="mcp",
            module="backend.services.adapters.profile_bridges.mcp",
            dependencies=(
                ("mcp", "1.27.1"),
                ("agent-governance-toolkit-core", "5.0.0"),
                ("httpx", "0.28.1"),
                ("jsonschema", "4.26.0"),
            ),
            operations=frozenset({"invoke", "gate", "observe", "cancel"}),
            observation_mapping=True,
        ),
    }
)


def dependency_readiness(profile_id: str) -> AdapterReadiness:
    try:
        specification = BRIDGES[profile_id]
    except KeyError as exc:
        raise ValueError(f"No app-owned runnable bridge is registered for {profile_id}") from exc
    installed = []
    for package, qualified in specification.dependencies:
        try:
            found = version(package)
        except PackageNotFoundError:
            return AdapterReadiness(False, f"Missing bridge dependency: {package}")
        installed.append(found)
        if found != qualified:
            return AdapterReadiness(False, f"Unsupported {package}; qualified bridge release is {qualified}", found)
    return AdapterReadiness(True, installed_version="/".join(installed))
