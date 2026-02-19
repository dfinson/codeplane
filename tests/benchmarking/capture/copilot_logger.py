#!/usr/bin/env python3
"""
copilot_logger.py — mitmproxy addon for capturing Copilot Chat API traffic.

Runs as a transparent proxy during VS Code agent sessions.  Writes one JSON
file per VS Code session to ``~/.copilot-logs/``, containing every API
round-trip (messages, tool calls, token counts, latency, model, finish reason).

Usage:
    # Start capturing (Terminal 1):
    mitmdump -s copilot_logger.py

    # … run your VS Code agent session …

    # View a captured session (standalone):
    python3 copilot_logger.py --dump ~/.copilot-logs/<session>.json

    # List all captured sessions:
    python3 copilot_logger.py --dump-all

    # Aggregate stats:
    python3 copilot_logger.py --stats

Requires VS Code settings:
    {
        "http.proxy": "http://127.0.0.1:8080",
        "http.proxyStrictSSL": false
    }
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_DIR = Path.home() / ".copilot-logs"

# Hosts whose traffic we intercept
COPILOT_HOSTS: frozenset[str] = frozenset(
    {
        "api.githubcopilot.com",
        "api.individual.githubcopilot.com",
        "copilot-proxy.githubusercontent.com",
    }
)

# Paths that indicate a chat completions request
CHAT_PATHS: frozenset[str] = frozenset(
    {
        "/chat/completions",
        "/v1/chat/completions",
    }
)

# Headers to redact before writing to disk
REDACT_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "x-github-token",
        "openai-api-key",
        "openai-organization",
    }
)

# SSE parsing
_SSE_DATA_RE = re.compile(r"^data:\s*(.*)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Session store (in-memory, flushed to disk after each turn)
# ---------------------------------------------------------------------------


class SessionStore:
    """Manages per-session capture data."""

    def __init__(self, log_dir: Path = LOG_DIR) -> None:
        self._log_dir = log_dir
        self._sessions: dict[str, dict[str, Any]] = {}
        self._log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    def get_or_create(self, session_id: str) -> dict[str, Any]:
        if session_id in self._sessions:
            return self._sessions[session_id]
        session: dict[str, Any] = {
            "session_id": session_id,
            "started_at": None,
            "ended_at": None,
            "models_used": [],
            "totals": {
                "turns": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tokens": 0,
                "tool_calls": 0,
                "duration_ms": 0,
            },
            "tool_call_frequency": {},
            "chat_segments": [],
            "turns": [],
        }
        self._sessions[session_id] = session
        return session

    def flush(self, session_id: str) -> Path:
        """Write session to disk and return the file path."""
        session = self._sessions[session_id]
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)[:80]
        path = self._log_dir / f"{safe_id}.json"
        with open(path, "w") as f:
            json.dump(session, f, indent=2, default=str)
        return path


# ---------------------------------------------------------------------------
# Request/response parsing
# ---------------------------------------------------------------------------


def _extract_session_id(headers: dict[str, str]) -> str:
    """Extract session ID from request headers using fallback chain."""
    for header in (
        "vscode-sessionid",
        "x-vscode-sessionid",
        "copilot-integration-id",
        "x-request-id",
    ):
        val = headers.get(header)
        if val:
            return val
    return f"unknown_{int(time.time())}"


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return headers with sensitive values replaced."""
    return {k: ("<REDACTED>" if k.lower() in REDACT_HEADERS else v) for k, v in headers.items()}


def _parse_request_body(raw: bytes) -> dict[str, Any] | None:
    """Parse request body as JSON, returning None on failure."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_sse_response(raw: bytes) -> dict[str, Any]:
    """Parse an SSE (Server-Sent Events) streaming response.

    Reassembles assistant text, tool calls, usage, model, and finish reason
    from the stream of ``data: {...}`` lines.
    """
    assistant_text_parts: list[str] = []
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    usage: dict[str, int] | None = None
    model: str | None = None
    finish_reason: str | None = None
    chunk_count = 0

    text = raw.decode("utf-8", errors="replace")

    for match in _SSE_DATA_RE.finditer(text):
        line = match.group(1).strip()
        if not line or line == "[DONE]":
            continue
        chunk_count += 1
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Model
        if "model" in chunk and chunk["model"]:
            model = chunk["model"]

        # Usage (take the last one — final chunk has the aggregate)
        if "usage" in chunk and chunk["usage"]:
            usage = chunk["usage"]

        # Choices
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})

            # Assistant text
            content = delta.get("content")
            if content:
                assistant_text_parts.append(content)

            # Finish reason
            fr = choice.get("finish_reason")
            if fr:
                finish_reason = fr

            # Tool calls (streamed incrementally)
            for tc in delta.get("tool_calls", []):
                idx = tc.get("index", 0)
                if idx not in tool_calls_by_index:
                    tool_calls_by_index[idx] = {
                        "id": tc.get("id", ""),
                        "name": "",
                        "arguments_raw": "",
                    }
                entry = tool_calls_by_index[idx]
                if tc.get("id"):
                    entry["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    entry["name"] = fn["name"]
                if fn.get("arguments"):
                    entry["arguments_raw"] += fn["arguments"]

    # Assemble tool calls
    tool_calls: list[dict[str, Any]] = []
    for idx in sorted(tool_calls_by_index):
        entry = tool_calls_by_index[idx]
        args_str = entry["arguments_raw"]
        try:
            arguments = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            arguments = {"_raw": args_str}
        tool_calls.append(
            {
                "id": entry["id"],
                "name": entry["name"],
                "arguments": arguments,
            }
        )

    return {
        "assistant_text": "".join(assistant_text_parts),
        "tool_calls": tool_calls,
        "usage": usage,
        "model": model,
        "finish_reason": finish_reason,
        "sse_chunk_count": chunk_count,
    }


def _parse_json_response(raw: bytes) -> dict[str, Any]:
    """Parse a non-streaming JSON response."""
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {
            "assistant_text": "",
            "tool_calls": [],
            "usage": None,
            "model": None,
            "finish_reason": None,
            "sse_chunk_count": 0,
        }

    assistant_text = ""
    tool_calls: list[dict[str, Any]] = []
    finish_reason = None

    for choice in body.get("choices", []):
        msg = choice.get("message", {})
        assistant_text += msg.get("content", "") or ""
        finish_reason = choice.get("finish_reason", finish_reason)
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            args_str = fn.get("arguments", "")
            try:
                arguments = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                arguments = {"_raw": args_str}
            tool_calls.append(
                {
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": arguments,
                }
            )

    return {
        "assistant_text": assistant_text,
        "tool_calls": tool_calls,
        "usage": body.get("usage"),
        "model": body.get("model"),
        "finish_reason": finish_reason,
        "sse_chunk_count": 0,
    }


# ---------------------------------------------------------------------------
# Turn building
# ---------------------------------------------------------------------------


def _build_message_stats(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics for the messages array."""
    role_counts: dict[str, int] = {}
    total_bytes = 0
    tool_result_count = 0

    for msg in messages:
        role = msg.get("role", "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        content = msg.get("content")
        if isinstance(content, str):
            total_bytes += len(content.encode("utf-8"))
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text", "")
                    if isinstance(text, str):
                        total_bytes += len(text.encode("utf-8"))
        if role == "tool":
            tool_result_count += 1

    return {
        "role_counts": role_counts,
        "total_content_bytes": total_bytes,
        "tool_result_count": tool_result_count,
    }


def _extract_tool_results_in_context(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Index tool results present in the messages array."""
    results: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        content_bytes = len(content.encode("utf-8")) if isinstance(content, str) else 0
        results.append(
            {
                "tool_call_id": msg.get("tool_call_id", ""),
                "content_bytes": content_bytes,
                "content_preview": (content[:200] if isinstance(content, str) else ""),
            }
        )
    return results


def _compute_incremental(
    messages: list[dict[str, Any]],
    previous_message_count: int,
) -> dict[str, Any]:
    """Track what’s new in this turn vs repeated context."""
    new_messages = messages[previous_message_count:]
    repeated = messages[:previous_message_count]

    def _content_bytes(msg_list: list[dict[str, Any]]) -> int:
        total = 0
        for msg in msg_list:
            c = msg.get("content", "")
            if isinstance(c, str):
                total += len(c.encode("utf-8"))
        return total

    new_user = sum(1 for m in new_messages if m.get("role") == "user")
    new_tool = sum(1 for m in new_messages if m.get("role") == "tool")

    return {
        "new_user_messages": new_user,
        "new_tool_results": new_tool,
        "new_content_bytes": _content_bytes(new_messages),
        "repeated_context_bytes": _content_bytes(repeated),
    }


def _detect_segment_boundary(
    messages: list[dict[str, Any]],
    prev_system_hash: str | None,
    prev_message_count: int,
) -> tuple[bool, str]:
    """Detect if a new chat segment has started.

    Returns (is_boundary, current_system_hash).
    """
    system_content = ""
    for msg in messages:
        if msg.get("role") == "system":
            c = msg.get("content", "")
            if isinstance(c, str):
                system_content = c
            break
    current_hash = hashlib.sha256(system_content.encode()).hexdigest()[:16]

    if prev_system_hash is None:
        return False, current_hash

    # Boundary: system prompt changed or message count shrunk (new chat)
    if current_hash != prev_system_hash:
        return True, current_hash
    if len(messages) < prev_message_count:
        return True, current_hash

    return False, current_hash


# ---------------------------------------------------------------------------
# mitmproxy addon
# ---------------------------------------------------------------------------


class CopilotLogger:
    """mitmproxy addon that captures Copilot Chat API traffic."""

    def __init__(self) -> None:
        self._store = SessionStore()
        # Track per-session state for incremental computation
        self._session_state: dict[str, dict[str, Any]] = {}
        # Pending requests: flow.id -> (session_id, request_body, start_time, headers)
        self._pending: dict[str, tuple[str, dict[str, Any] | None, float, dict[str, str]]] = {}

    def request(self, flow: Any) -> None:  # noqa: ANN401
        """Called when a request is received."""
        host = flow.request.pretty_host
        if host not in COPILOT_HOSTS:
            return
        if flow.request.path.split("?")[0] not in CHAT_PATHS:
            return
        if flow.request.method != "POST":
            return

        headers = dict(flow.request.headers)
        session_id = _extract_session_id(headers)
        body = _parse_request_body(flow.request.get_content())
        self._pending[flow.id] = (session_id, body, time.time(), headers)

    def response(self, flow: Any) -> None:  # noqa: ANN401
        """Called when a response is received."""
        pending = self._pending.pop(flow.id, None)
        if pending is None:
            return

        session_id, request_body, start_time, req_headers = pending
        duration_ms = int((time.time() - start_time) * 1000)

        # Parse response
        content_type = flow.response.headers.get("content-type", "")
        raw_response = flow.response.get_content()

        if "text/event-stream" in content_type:
            parsed = _parse_sse_response(raw_response)
        else:
            parsed = _parse_json_response(raw_response)

        # Get or create session
        session = self._store.get_or_create(session_id)
        state = self._session_state.setdefault(
            session_id,
            {
                "prev_message_count": 0,
                "prev_system_hash": None,
                "segment_index": 0,
            },
        )

        # Messages from request
        messages: list[dict[str, Any]] = request_body.get("messages", []) if request_body else []

        # Build turn record
        now_iso = datetime.now(tz=UTC).isoformat()
        turn_number = session["totals"]["turns"] + 1

        # Request params
        request_model = request_body.get("model", "") if request_body else ""
        temperature = request_body.get("temperature") if request_body else None
        top_p = request_body.get("top_p") if request_body else None
        max_tokens = request_body.get("max_tokens") if request_body else None

        # Tool definitions from request
        tool_defs = request_body.get("tools", []) if request_body else []

        # Incremental tracking
        incremental = _compute_incremental(
            messages,
            state["prev_message_count"],
        )

        # Chat segment detection
        is_boundary, sys_hash = _detect_segment_boundary(
            messages,
            state["prev_system_hash"],
            state["prev_message_count"],
        )
        if is_boundary:
            state["segment_index"] += 1
        state["prev_system_hash"] = sys_hash
        state["prev_message_count"] = len(messages)

        # Tool results in context
        tool_results_in_context = _extract_tool_results_in_context(messages)

        # Message stats
        message_stats = _build_message_stats(messages)

        # Usage
        usage = parsed.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0) or (prompt_tokens + completion_tokens)

        # HTTP status
        http_status = flow.response.status_code

        turn: dict[str, Any] = {
            "turn_number": turn_number,
            "timestamp_iso": now_iso,
            "duration_ms": duration_ms,
            "http_status": http_status,
            "request_model": request_model,
            "model": parsed.get("model") or request_model,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "messages": messages,
            "tool_definitions": tool_defs,
            "assistant_text": parsed.get("assistant_text", ""),
            "tool_calls": parsed.get("tool_calls", []),
            "tool_results_in_context": tool_results_in_context,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            "finish_reason": parsed.get("finish_reason"),
            "sse_chunk_count": parsed.get("sse_chunk_count", 0),
            "message_stats": message_stats,
            "incremental": incremental,
            "chat_segment_index": state["segment_index"],
            "request_headers": _redact_headers(req_headers),
        }

        # Update session
        session["turns"].append(turn)
        t = session["totals"]
        t["turns"] = turn_number
        t["prompt_tokens"] += prompt_tokens
        t["completion_tokens"] += completion_tokens
        t["tokens"] += total_tokens
        t["tool_calls"] += len(parsed.get("tool_calls", []))
        t["duration_ms"] += duration_ms

        # Models used
        response_model = parsed.get("model") or request_model
        if response_model and response_model not in session["models_used"]:
            session["models_used"].append(response_model)

        # Tool call frequency
        for tc in parsed.get("tool_calls", []):
            name = tc.get("name", "unknown")
            session["tool_call_frequency"][name] = session["tool_call_frequency"].get(name, 0) + 1

        # Timestamps
        if session["started_at"] is None:
            session["started_at"] = now_iso
        session["ended_at"] = now_iso

        # Update chat segments
        segments = session["chat_segments"]
        seg_idx = state["segment_index"]
        if not segments or segments[-1]["segment_index"] != seg_idx:
            segments.append(
                {
                    "segment_index": seg_idx,
                    "first_turn": turn_number,
                    "last_turn": turn_number,
                    "system_prompt_hash": f"sha256:{sys_hash}",
                }
            )
        else:
            segments[-1]["last_turn"] = turn_number

        # Flush to disk
        path = self._store.flush(session_id)

        # Log to stderr
        tc_count = len(parsed.get("tool_calls", []))
        sys.stderr.write(
            f"[copilot_logger] Turn {turn_number} | "
            f"{response_model} | "
            f"{prompt_tokens}+{completion_tokens} tokens | "
            f"{tc_count} tool calls | "
            f"{duration_ms}ms | "
            f"{parsed.get('finish_reason', '?')} | "
            f"HTTP {http_status} | "
            f"{path}\n"
        )


# ---------------------------------------------------------------------------
# mitmproxy entry point
# ---------------------------------------------------------------------------

addons = [CopilotLogger()]


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------


def _cli_dump(path: str) -> None:
    """Pretty-print a single capture file."""
    with open(path) as f:
        data = json.load(f)
    print(json.dumps(data, indent=2, default=str))


def _cli_dump_all() -> None:
    """List all captured sessions."""
    if not LOG_DIR.exists():
        print(f"No captures found in {LOG_DIR}")
        return
    for p in sorted(LOG_DIR.glob("*.json")):
        try:
            with open(p) as f:
                data = json.load(f)
            turns = data.get("totals", {}).get("turns", 0)
            tokens = data.get("totals", {}).get("tokens", 0)
            models = ", ".join(data.get("models_used", []))
            started = data.get("started_at", "?")
            print(f"{p.name:40s} | {turns:3d} turns | {tokens:8d} tokens | {models} | {started}")
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"{p.name:40s} | ERROR: {exc}")


def _cli_stats() -> None:
    """Aggregate stats across all captures."""
    if not LOG_DIR.exists():
        print(f"No captures found in {LOG_DIR}")
        return
    total_sessions = 0
    total_turns = 0
    total_tokens = 0
    total_tool_calls = 0
    model_set: set[str] = set()

    for p in sorted(LOG_DIR.glob("*.json")):
        try:
            with open(p) as f:
                data = json.load(f)
            total_sessions += 1
            total_turns += data.get("totals", {}).get("turns", 0)
            total_tokens += data.get("totals", {}).get("tokens", 0)
            total_tool_calls += data.get("totals", {}).get("tool_calls", 0)
            model_set.update(data.get("models_used", []))
        except (json.JSONDecodeError, KeyError):
            pass

    print(f"Sessions:   {total_sessions}")
    print(f"Turns:      {total_turns}")
    print(f"Tokens:     {total_tokens:,}")
    print(f"Tool calls: {total_tool_calls}")
    print(f"Models:     {', '.join(sorted(model_set)) or 'none'}")


def _cli_main() -> None:
    """Standalone CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Copilot traffic capture viewer",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dump", metavar="PATH", help="View a capture file")
    group.add_argument(
        "--dump-all",
        action="store_true",
        help="List all captures",
    )
    group.add_argument(
        "--stats",
        action="store_true",
        help="Aggregate statistics",
    )
    args = parser.parse_args()

    if args.dump:
        _cli_dump(args.dump)
    elif args.dump_all:
        _cli_dump_all()
    elif args.stats:
        _cli_stats()


if __name__ == "__main__":
    _cli_main()
