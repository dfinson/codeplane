"""Dependency-free translation of the released HttpGate and hook relay envelopes.

Wire contracts were checked against traceforge-toolkit 0.1.5. No TraceForge
runtime, policy evaluator, IPC registry or fail-open parser is imported.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.models.schemas.external_gate import ExternalGatePreflight

DIALECTS = (
    "json",
    "claude-code",
    "copilot-cli",
    "codex",
    "gemini",
    "cline",
    "cursor",
    "amazon-q",
    "opencode",
    "openhands",
)


def decode_hook(value: Any, *, request_id: str | None = None) -> ExternalGatePreflight:
    if not isinstance(value, dict):
        raise ValueError("A gate hook must be a JSON object")
    tool = value.get("tool")
    name = value.get("tool_name") or (tool.get("name") if isinstance(tool, dict) else tool)
    if "tool_input" in value:
        arguments = value["tool_input"]
    elif "input" in value:
        arguments = value["input"]
    elif isinstance(tool, dict) and "input" in tool:
        arguments = tool["input"]
    else:
        raise ValueError("A gate hook must include its actual tool input")
    native_id = value.get("tool_call_id", value.get("tool_use_id"))
    session_id = value.get("session_id")
    identity = request_id or value.get("request_id")
    if identity is None:
        identity = (
            uuid.uuid5(uuid.NAMESPACE_URL, f"external-gate-hook:{session_id}:{native_id}").hex
            if isinstance(native_id, str) and native_id
            else uuid.uuid4().hex
        )
    return ExternalGatePreflight(
        request_id=identity,
        tool_name=name,
        tool_input=arguments,
        target=value.get("target"),
        session_id=session_id,
        tool_call_id=native_id,
    )


def verdict(allowed: bool, dialect: str, reason: str) -> tuple[dict[str, Any] | None, int, str | None]:
    if dialect not in DIALECTS:
        raise ValueError("Unsupported gate output dialect")
    if allowed:
        return ({"decision": "allow"} if dialect == "json" else {}), 0, None
    reason = reason or "Denied by CodePlane"
    if dialect == "json":
        return {"decision": "deny", "reason": reason}, 0, None
    if dialect in {"claude-code", "codex"}:
        return (
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            (2 if dialect == "codex" else 0),
            (reason if dialect == "codex" else None),
        )
    if dialect == "copilot-cli":
        return {"permissionDecision": "deny", "permissionDecisionReason": reason}, 1, None
    if dialect == "cline":
        return {"cancel": True, "errorMessage": reason}, 0, None
    if dialect == "cursor":
        return {"permission": "deny", "user_message": reason, "agent_message": reason}, 2, None
    if dialect == "amazon-q":
        return None, 2, reason
    return {"decision": "deny", "reason": reason}, 2, reason
