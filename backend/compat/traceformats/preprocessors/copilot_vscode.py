# Derived from traceforge-toolkit 0.1.5. MIT license: backend/compat/traceforge.LICENSE.
"""Replay VS Code ChatModel v3 snapshots, sets and appends.

Request metadata and citation positions belong to the owning decoder. They are
serialized with its checkpoint and restored on failed records or batch resume.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.compat.traceformats.preprocessors.registry import StatefulPreprocessor, register_preprocessor


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Any = None
    model: Any = None
    timestamp: Any = None
    citation_count: int = Field(default=0, ge=0)


class _JournalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: dict[int, _Request] = Field(default_factory=dict)
    next_index: int = Field(default=0, ge=0)


def _agent_id(req: dict[str, Any]) -> Any:
    agent = req.get("agent")
    if isinstance(agent, dict):
        return agent.get("id") or agent.get("name")
    return agent


@register_preprocessor("copilot_vscode")
class CopilotVSCodePreprocessor(StatefulPreprocessor):
    def __init__(self) -> None:
        self._state = _JournalState()

    def checkpoint(self) -> dict[str, Any]:
        return self._state.model_dump(mode="json")

    def restore(self, state: dict[str, Any]) -> None:
        self._state = _JournalState.model_validate(deepcopy(state))

    def _emit_part(self, part: Any, idx: int) -> dict[str, Any] | None:
        if not isinstance(part, dict):
            return None
        request = self._state.requests.get(idx, _Request())
        return {
            **part,
            "event_type": part.get("kind") or "assistant_text",
            "request_id": request.request_id,
            "model": request.model,
            "timestamp": request.timestamp,
        }

    def _emit_citations(self, citations: Any, idx: int) -> list[dict[str, Any]]:
        if not isinstance(citations, list):
            return []
        request = self._state.requests.setdefault(idx, _Request())
        citations = [citation for citation in citations if isinstance(citation, dict)]
        start = min(request.citation_count, len(citations))
        request.citation_count = len(citations)
        return [
            {
                "event_type": "code_citation",
                "request_id": request.request_id,
                "model": request.model,
                "value": citation.get("value"),
                "license": citation.get("license"),
                "snippet": citation.get("snippet"),
                "timestamp": request.timestamp,
            }
            for citation in citations[start:]
        ]

    def _emit_completion(self, elapsed_ms: int | float, idx: int) -> dict[str, Any]:
        request = self._state.requests.get(idx, _Request())
        return {
            "event_type": "request_result",
            "request_id": request.request_id,
            "model": request.model,
            "elapsed_ms": elapsed_ms,
            "timestamp": request.timestamp,
        }

    def _emit_request(self, req: dict[str, Any], idx: int) -> list[dict[str, Any]]:
        self._state.requests[idx] = _Request(
            request_id=req.get("requestId"), model=req.get("modelId"), timestamp=req.get("timestamp")
        )
        message = req.get("message")
        out: list[dict[str, Any]] = [
            {
                "event_type": "user_message",
                "request_id": req.get("requestId"),
                "model": req.get("modelId"),
                "agent": _agent_id(req),
                "text": message.get("text") if isinstance(message, dict) else message,
                "timestamp": req.get("timestamp"),
            }
        ]
        for part in req.get("response") or []:
            emitted = self._emit_part(part, idx)
            if emitted is not None:
                out.append(emitted)
        out.extend(self._emit_citations(req.get("codeCitations"), idx))
        elapsed_ms = req.get("elapsedMs")
        if isinstance(elapsed_ms, (int, float)) and not isinstance(elapsed_ms, bool):
            out.append(self._emit_completion(elapsed_ms, idx))
        return out

    def __call__(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        if "kind" not in obj and obj.get("event_type"):
            return [obj]
        kind = obj.get("kind")
        if type(kind) is not int or kind not in (0, 1, 2):
            raise ValueError("Unsupported VS Code journal record kind")
        if kind == 0:
            snap = obj.get("v")
            if not isinstance(snap, dict):
                raise ValueError("Malformed VS Code journal snapshot")
            version = snap.get("version")
            if type(version) is not int or version != 3:
                raise ValueError("Unsupported VS Code journal version; expected version 3")
            self._state = _JournalState()
            out: list[dict[str, Any]] = [
                {
                    "event_type": "session_started",
                    "session_id": snap.get("sessionId"),
                    "version": version,
                    "responder": snap.get("responderUsername"),
                    "initial_location": snap.get("initialLocation"),
                    "timestamp": snap.get("creationDate"),
                }
            ]
            for req in snap.get("requests") or []:
                if isinstance(req, dict):
                    out.extend(self._emit_request(req, self._state.next_index))
                    self._state.next_index += 1
            return out

        path, value = obj.get("k"), obj.get("v")
        if not isinstance(path, list) or not path:
            return []
        if kind == 2:
            if path == ["requests"]:
                out = []
                for req in value or []:
                    if isinstance(req, dict):
                        out.extend(self._emit_request(req, self._state.next_index))
                        self._state.next_index += 1
                return out
            if len(path) == 3 and path[0] == "requests" and path[2] == "response":
                out = []
                for part in value or []:
                    emitted = self._emit_part(part, path[1])
                    if emitted is not None:
                        out.append(emitted)
                return out
            return []
        if kind == 1 and len(path) == 3 and path[0] == "requests":
            idx = path[1]
            if path[2] == "codeCitations":
                return self._emit_citations(value, idx)
            if path[2] == "elapsedMs" and isinstance(value, (int, float)) and not isinstance(value, bool):
                return [self._emit_completion(value, idx)]
        return []
