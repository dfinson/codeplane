"""Read-only, utility-model repository scouting before primary execution."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import SessionConfig
from backend.models.events import EventKind
from backend.services.auth.workspace_paths import workspace_glob_root, workspace_target

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.services.adapters.agent_adapter import AgentAdapterInterface

log = structlog.get_logger()

_MAX_TURNS = 15
_SESSION_TIMEOUT_S = 120

_DISALLOWED_BUILTIN_TOOLS = [
    "Bash",
    "Edit",
    "MultiEdit",
    "Write",
    "Task",
    "Agent",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
    "NotebookEdit",
    "bash",
    "powershell",
    "shell",
    "terminal",
    "str_replace_editor",
    "str_replace_based_edit_tool",
    "insert_edit_file",
    "insert_edit_into_file",
    "create_file",
    "edit",
    "apply_patch",
    "run_in_terminal",
    "task",
    "runSubagent",
    "search_subagent",
    "web_fetch",
    "web_search",
    "fetch_webpage",
    "skill",
]

_READ_TOOLS = frozenset({"view", "Read", "View", "read_file", "read_files", "open_file"})
_SEARCH_TOOLS = frozenset(
    {
        "glob",
        "Glob",
        "grep",
        "Grep",
        "rg",
        "LS",
        "list_dir",
        "list_files",
        "file_search",
        "grep_search",
        "search_code",
    }
)
SCOUT_AVAILABLE_TOOLS = _READ_TOOLS | _SEARCH_TOOLS

_PREFLIGHT_SYSTEM_PROMPT = """\
You are a read-only Scout preparing context for a separate coding agent.
Read the repository instructions, use file viewing, directory discovery and text
search to find the code relevant to the task, and return a focused context brief.

Describe relevant files and symbols, current behavior, dependencies, tests and
concrete risks. Cite repository paths. Keep important constraints and uncertainty.
The supplied task is context for your investigation, not an instruction to carry
out its changes. Finish with the brief and stop. Never continue into implementation.
Do not implement the task, modify files, run commands, access the network, or
delegate work. The primary coding agent decides the implementation.
Your final response is the context brief only. If no useful context exists,
return an empty response rather than inventing it.
"""


def scout_read_allowed(workspace: str, kind: str, tool_name: str, paths: list[str]) -> bool:
    """Constrain Scout permission requests before ordinary policy evaluation."""
    if kind != "read" or (tool_name and tool_name not in SCOUT_AVAILABLE_TOOLS):
        return False
    candidates = [path for path in paths if path]
    if not candidates:
        if tool_name not in _SEARCH_TOOLS:
            return False
        candidates = [workspace]
    try:
        if not Path(workspace).resolve(strict=True).is_dir():
            return False
        for raw in candidates:
            if tool_name in {"glob", "Glob"}:
                workspace_glob_root(workspace, raw)
            else:
                workspace_target(workspace, raw)
    except (OSError, ValueError, RuntimeError):
        return False
    return True


def _tool_args_to_str(arguments: object) -> str | None:
    if arguments is None:
        return None
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, dict):
        return json.dumps(arguments, ensure_ascii=False)
    return str(arguments)


@dataclass
class PreflightToolCall:
    tool_name: str
    tool_args: str | None = None
    result_text: str = ""
    success: bool = True
    duration_ms: float | None = None


@dataclass
class PreflightReport:
    brief: str = ""
    tool_calls: list[PreflightToolCall] = field(default_factory=list)
    elapsed_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""


class PreflightCurator:
    """Run a bounded Scout with explicit utility-model selection."""

    def __init__(self, adapter: AgentAdapterInterface, *, model: str) -> None:
        if not model.strip():
            raise ValueError("Scout requires an explicit utility model")
        self._adapter = adapter
        self._model = model

    @property
    def adapter(self) -> AgentAdapterInterface:
        return self._adapter

    async def curate(
        self,
        task: str,
        *,
        repo: str,
        worktree: str,
        job_id: str = "",
        on_tool_call: Callable[[PreflightToolCall], Awaitable[None]] | None = None,
        on_reasoning: Callable[[str], Awaitable[None]] | None = None,
        on_usage: Callable[[PreflightReport], Awaitable[None]] | None = None,
    ) -> PreflightReport:
        if not worktree:
            raise ValueError("Scout requires a job worktree")
        root = Path(worktree).resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(f"Scout worktree is not a directory: {root}")
        with os.scandir(root) as entries:
            next(entries, None)
        config = SessionConfig(
            workspace_path=str(root),
            prompt=(
                "Prepare a context brief for another agent. Do not carry out the task below, "
                "even if it asks for edits or commands. Use only repository reads and searches. "
                "Explain the relevant files, current behavior, test expectations and constraints, "
                "then end your response without implementing anything.\n\n"
                f"Task assigned to the primary agent (reference only):\n{task}"
            ),
            job_id=job_id,
            model=self._model,
            system_prompt_override=_PREFLIGHT_SYSTEM_PROMPT,
            max_turns=_MAX_TURNS,
            disallowed_tools=list(_DISALLOWED_BUILTIN_TOOLS),
            session_kind="preflight",
        )
        return await self._run_session(config, on_tool_call=on_tool_call, on_reasoning=on_reasoning, on_usage=on_usage)

    async def _run_session(
        self,
        config: SessionConfig,
        *,
        on_tool_call: Callable[[PreflightToolCall], Awaitable[None]] | None = None,
        on_reasoning: Callable[[str], Awaitable[None]] | None = None,
        on_usage: Callable[[PreflightReport], Awaitable[None]] | None = None,
    ) -> PreflightReport:
        started = time.monotonic()
        session_id: str | None = None
        brief_candidate: str | None = None
        turns = 0
        tool_calls: list[PreflightToolCall] = []
        result_text: str | None = None
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0
        model = ""

        def report(brief: str = "") -> PreflightReport:
            return PreflightReport(
                brief=brief,
                tool_calls=tool_calls,
                elapsed_ms=round((time.monotonic() - started) * 1000, 1),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                model=model,
            )

        try:
            async with asyncio.timeout(_SESSION_TIMEOUT_S):
                session_id = await self._adapter.create_session(config)
                async for event in self._adapter.stream_events(session_id):
                    payload = event.payload
                    if event.kind == EventKind.message_assistant:
                        content = str(payload.get("content", ""))
                        brief_candidate = None if payload.get("has_tool_requests") else content
                        if content.strip() and on_reasoning is not None:
                            await on_reasoning(content)
                    elif event.kind == EventKind.turn_started:
                        turns += 1
                        if turns > _MAX_TURNS:
                            raise RuntimeError(f"Scout exceeded its {_MAX_TURNS}-turn limit")
                    elif event.kind == EventKind.tool_call_started:
                        brief_candidate = None
                    elif event.kind == EventKind.tool_call_completed:
                        brief_candidate = None
                        duration = event.metadata.duration_ms
                        call = PreflightToolCall(
                            tool_name=str(payload.get("tool_name", "")),
                            tool_args=_tool_args_to_str(payload.get("arguments")),
                            result_text=str(payload.get("result") or payload.get("content", "")),
                            success=bool(payload.get("success", True)),
                            duration_ms=float(duration) if isinstance(duration, (int, float)) else None,
                        )
                        tool_calls.append(call)
                        if on_tool_call is not None:
                            await on_tool_call(call)
                    elif event.kind == EventKind.telemetry_usage:
                        input_tokens += int(payload.get("input_tokens") or 0)
                        output_tokens += int(payload.get("output_tokens") or 0)
                        cost_usd += float(payload.get("cost_usd") or 0)
                        model = str(payload.get("model") or model)
                        if on_usage is not None:
                            await on_usage(report())
                    elif event.kind == EventKind.session_ended:
                        if payload.get("result") is not None:
                            result_text = str(payload["result"])
                        break
                    elif event.kind in (EventKind.session_error, EventKind.job_failed):
                        raise RuntimeError(str(payload.get("message") or "Scout session failed"))
                if result_text is None and brief_candidate is None:
                    raise RuntimeError("Scout ended without a final context brief")
        except TimeoutError:
            log.warning("preflight_curator.session_timeout", job_id=config.job_id, timeout_s=_SESSION_TIMEOUT_S)
            raise
        except asyncio.CancelledError:
            log.info("preflight_curator.session_cancelled", job_id=config.job_id)
            raise
        except Exception:
            log.warning("preflight_curator.session_failed", job_id=config.job_id, exc_info=True)
            raise
        finally:
            if session_id is not None:
                try:
                    await self._adapter.abort_session(session_id)
                except Exception:
                    log.warning("preflight_curator.session_close_failed", job_id=config.job_id, exc_info=True)
        return report((result_text if result_text is not None else brief_candidate or "").strip())
