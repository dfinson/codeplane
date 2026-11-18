"""Approved explorer selections, distinct from stable runtime adapter IDs.

These are requirements, not proof that a producer or control is implemented.
Runtime availability is resolved separately by the integration catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass

SPECIFICATION_SHA256 = "9941bd11323740358f158baf953083d192873f03b610708d9fad10934aee29c5"


@dataclass(frozen=True, slots=True)
class ProfileContract:
    id: str
    name: str
    acceptance_kind: str
    adapter_id: str | None
    formats: tuple[str, ...]
    operations: tuple[str, ...]
    ownership: str
    steering: str
    cancellation: str
    interception: str
    upstream: str


_CALLABLE = ("observe", "invoke", "steer", "gate", "cancel")
_CALLABLE_OWNER = "caller-owned-runnable"
_CALLABLE_STEER = "declared-turn-boundary; live-input-requires-acknowledged-callback"
_CALLABLE_CANCEL = "cooperative; synchronous-cancel-requested-until-completion-or-stop-ack"


def _callable(
    id: str,
    name: str,
    *,
    formats: tuple[str, ...] = (),
    upstream: str,
    interception: str = "before-actual-callable",
    acceptance_kind: str = "blocking-callable",
) -> ProfileContract:
    return ProfileContract(
        id,
        name,
        acceptance_kind,
        None,
        formats,
        _CALLABLE,
        _CALLABLE_OWNER,
        _CALLABLE_STEER,
        _CALLABLE_CANCEL,
        interception,
        upstream,
    )


PROFILES = (
    ProfileContract(
        "copilot-sdk",
        "Copilot managed SDK",
        "blocking-managed",
        "copilot",
        (),
        ("observe", "invoke", "steer", "cancel", "gate", "complete"),
        "managed-sdk-session",
        "current-session-and-epoch",
        "provider-abort-and-owned-client-shutdown",
        "sdk-permission-callback",
        "CodePlane CopilotAdapter",
    ),
    ProfileContract(
        "copilot-cli",
        "Copilot native CLI",
        "observed-with-native-control",
        None,
        ("copilot",),
        ("observe", "steer", "cancel"),
        "externally-owned-session",
        "published-target-admission",
        "native-abort-request; outcome-source-observed",
        "none",
        "SessionStateWatcher / CopilotSteerClient",
    ),
    ProfileContract(
        "claude-sdk",
        "Claude managed SDK",
        "blocking-managed",
        "claude",
        (),
        ("observe", "invoke", "steer", "cancel", "gate", "complete"),
        "managed-sdk-session",
        "supported-session-query-boundary",
        "interrupt-and-owned-subprocess-teardown",
        "managed-permission-bridge",
        "CodePlane ClaudeAdapter",
    ),
    ProfileContract(
        "claude-cli",
        "Claude native CLI",
        "observed-with-queued-control",
        None,
        ("claude",),
        ("observe", "steer", "cancel"),
        "externally-owned-session",
        "queued-hook-delivery",
        "queued-stop-request; outcome-source-observed",
        "none",
        "ClaudeSessionStateWatcher",
    ),
    _callable(
        "langchain",
        "LangChain",
        formats=("langchain",),
        upstream="agentmesh AgentMeshTrustCallback / TrustVerifiedTool; native callbacks",
    ),
    _callable(
        "langgraph",
        "LangGraph",
        formats=("langgraph",),
        upstream="agentmesh TrustedGraphNode / TrustCheckpoint; native ToolNode and checkpointer",
    ),
    _callable(
        "crewai",
        "CrewAI",
        formats=("crewai",),
        upstream="agentmesh TrustAwareAgent / TrustAwareCrew; native before/after-tool hooks and event bus",
    ),
    _callable(
        "swarm",
        "OpenAI Swarm",
        upstream="agentmesh TrustedSwarm / TrustedAgent / HandoffVerifier",
        interception="before-function-and-before-returned-handoff-context-transfer",
    ),
    _callable(
        "langflow",
        "Langflow",
        upstream="agentmesh TrustGatedFlow / TrustVerificationComponent / IdentityComponent",
    ),
    ProfileContract(
        "flowise",
        "Flowise",
        "remote-request",
        None,
        (),
        ("observe", "invoke", "gate", "stop-waiting"),
        "remote-prediction-or-upsert",
        "subsequent-prediction-is-new-admitted-request",
        "stop-local-waiting; remote-outcome-unknown",
        "before-request",
        "agentmesh TrustGatedFlowiseClient.predict/upsert",
    ),
    _callable(
        "haystack",
        "Haystack",
        upstream="agentmesh TrustedPipeline / TrustGateComponent / TrustAgentComponent",
    ),
    _callable(
        "openai-agents",
        "OpenAI Agents SDK",
        formats=("openai_agents",),
        upstream="native FunctionTool.on_invoke_tool and Trace.export/Span.export",
    ),
    _callable(
        "semantic-kernel",
        "Semantic Kernel",
        formats=("semantic_kernel",),
        upstream="native AUTO_FUNCTION_INVOCATION filter",
    ),
    _callable(
        "microsoft-agent-framework",
        "Microsoft Agent Framework",
        upstream="native FunctionMiddleware; distinct from Microsoft 365 Agents SDK formats",
    ),
    _callable(
        "smolagents",
        "smolagents",
        formats=("smolagents",),
        upstream="native tool invocation and memory/stream steps",
    ),
    _callable(
        "pydantic-ai",
        "PydanticAI",
        formats=("pydantic_ai",),
        upstream="native leaf toolset.call_tool / before_tool_execute and message/stream events",
    ),
    ProfileContract(
        "m365-agents",
        "Microsoft 365 Agents SDK",
        "observation-only",
        None,
        ("maf", "maf_transcript"),
        ("observe", "stop-observation"),
        "externally-hosted-agent",
        "none",
        "stop-observation-only",
        "none",
        "TF 0.1.5 maf.yaml ActivityHandler spans and maf_transcript Activity records",
    ),
    ProfileContract(
        "a2a",
        "A2A protocol",
        "remote-protocol",
        None,
        (),
        ("observe", "invoke", "steer", "cancel", "gate"),
        "remote-task",
        "state-appropriate-task-context-input",
        "tasks/cancel-returned-state-or-unknown",
        "before-delegation",
        "agentmesh A2AAgentCard / A2ATrustProvider; A2A 0.3 JSON-RPC",
    ),
    ProfileContract(
        "ai-card",
        "AI Card identity",
        "identity-only",
        None,
        (),
        ("discover", "remove-identity"),
        "project-scoped-identity-only",
        "none",
        "remove-local-identity-binding",
        "identity-evidence-only",
        "agentmesh AICard / AICardDiscovery / AICardIdentity / AICardService",
    ),
    ProfileContract(
        "mcp",
        "MCP protocol",
        "tool-protocol",
        None,
        (),
        ("observe", "invoke", "gate", "cancel"),
        "bound-tool-operation",
        "protocol-requests-only",
        "supported-request-cancel; remote-outcome-explicit",
        "before-actual-handler-or-client-call",
        "standard mcp transport; agentmesh TrustGatedMCPServer",
    ),
    ProfileContract(
        "http",
        "HTTP, FastAPI, Flask and Django",
        "request-middleware",
        None,
        (),
        ("observe", "invoke", "gate", "stop-waiting"),
        "caller-owned-handler-or-remote-endpoint",
        "declared-endpoint-operations-only",
        "stop-waiting-unless-producer-acknowledges-stop",
        "authenticated-binding-before-request-preflight",
        "agentmesh TrustMiddleware / Flask/FastAPI helpers / Django AgentTrustMiddleware",
    ),
    ProfileContract(
        "generic-otel",
        "Generic GenAI OTel",
        "observation-only",
        None,
        (),
        ("observe", "stop-observation"),
        "externally-hosted-producer",
        "none",
        "stop-observation-only",
        "none",
        "OTLP HTTP JSON / optional protobuf; versioned GenAI semantic conventions",
    ),
    ProfileContract(
        "external-gate",
        "External HTTP / stdin tool hooks",
        "blocking-hook",
        None,
        (),
        ("gate", "observe"),
        "externally-hosted-tool",
        "waiting-hook-decision",
        "invalidate-pending-decision",
        "single-use-live-epoch-bound-decision",
        "TF HttpGate / SubprocessGate / external hook relay; shared CodePlane preflight",
    ),
    _callable(
        "autogen",
        "AutoGen",
        upstream="native InterventionHandler and function invoker",
        acceptance_kind="native-client-hook",
    ),
    _callable(
        "llamaindex",
        "LlamaIndex",
        upstream="native query/chat callable, callbacks and local tool invokers",
        acceptance_kind="native-client-hook",
    ),
    _callable(
        "google-adk",
        "Google ADK",
        upstream="native BasePlugin before_tool_callback / after_tool_callback",
        acceptance_kind="native-client-hook",
    ),
    ProfileContract(
        "openai-client",
        "OpenAI Assistants client",
        "native-client-hook",
        None,
        (),
        _CALLABLE,
        _CALLABLE_OWNER,
        _CALLABLE_STEER,
        "installed-client-run-cancel-if-supported; otherwise-stop-waiting",
        "before-request-and-caller-owned-function",
        "native thread/run/tool-output and run-cancel requests",
    ),
    _callable(
        "anthropic-client",
        "Anthropic Messages client",
        upstream="native messages and caller-owned tool invocation",
        interception="before-request-and-caller-owned-function",
        acceptance_kind="native-client-hook",
    ),
    _callable(
        "gemini-client",
        "Google Gemini client",
        upstream="native generate_content and caller-owned function",
        interception="before-request-and-caller-owned-function",
        acceptance_kind="native-client-hook",
    ),
    _callable(
        "mistral-client",
        "Mistral client",
        upstream="native chat and caller-owned tool invocation",
        interception="before-request-and-caller-owned-function",
        acceptance_kind="native-client-hook",
    ),
    _callable(
        "bedrock-client",
        "AWS Bedrock client",
        upstream="native invoke_agent/event-stream/local action callbacks",
        interception="before-request-and-caller-owned-function",
        acceptance_kind="native-client-hook",
    ),
    ProfileContract(
        "native-formats",
        "All released native trace formats",
        "observation-only",
        None,
        (),
        ("observe", "stop-observation"),
        "externally-owned-source",
        "none",
        "stop-observation-only",
        "none",
        "TF 0.1.5: 24 retained mapping IDs, preprocessors and Markdown readers",
    ),
)
