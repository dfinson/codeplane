"""Governance accrual subscriber — the write side of the governance substrate.

Mirrors :class:`~backend.services.events.telemetry_subscriber.TelemetrySubscriber`
(the A2 bus-subscriber pattern) for the governance domain. It subscribes to the
single canonical ``traceforge.SessionEvent`` stream and, on every **executed**
tool call (``tool.call.completed``), advances the durable governance state
(budget / taint / session state) via :meth:`GovernanceDecider.observe`.

Only executed calls reach ``tool.call.completed`` — a permission decision that
denies an action blocks it *before* the SDK runs the tool, so no completion event
is emitted. This is exactly binding condition §2: **executed → budget advances;
rejected → no accrual.** The decision (read) path never accrues; only this
subscriber writes.

Accrual is gated on job registration so only main-agent jobs wired through the
action-policy setup accrue — sidecar/unknown sessions are ignored.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.events import EventKind
from backend.services.action_policy.classifier import Action, ActionKind
from backend.services.events.observation import is_observation_history

if TYPE_CHECKING:
    from backend.models.session_event import SessionEvent
    from backend.services.action_policy.governance import GovernanceDecider
    from backend.services.events.event_bus import EventBus

log = structlog.get_logger()

# Canonical tool-name → kind buckets (mirrors sidecar/policy_router and the
# telemetry tool classifier so accrual reconstructs the same Action shape the
# decision path saw).
_SHELL_TOOLS: frozenset[str] = frozenset(
    {
        "Bash",
        "bash",
        "sh",
        "shell",
        "run_in_terminal",
        "execute_command",
        "run_command",
        "terminal",
        "powershell",
        "pwsh",
        "cmd",
    }
)
_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "create_file",
        "create",
        "edit_file",
        "edit",
        "Edit",
        "MultiEdit",
        "write",
        "Write",
        "write_file",
        "replace_string_in_file",
        "multi_replace_string_in_file",
        "str_replace_based_edit_tool",
        "str_replace_editor",
        "insert_edit_into_file",
        "apply_patch",
        "delete_file",
        "NotebookEdit",
    }
)
_SHELL_COMMAND_KEYS = ("command", "cmd", "script", "shellCommand")
_PATH_KEYS = ("path", "file_path", "filePath", "filename", "file", "target_file", "notebook_path")


def _coerce_args(raw: Any) -> dict[str, Any]:
    """Normalize a tool-call ``arguments`` payload into a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _first(args: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        val = args.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def action_from_completed_tool(job_id: str, tool_name: str, raw_args: Any) -> Action:
    """Reconstruct an :class:`Action` from an executed tool call for accrual.

    Uses the same tool-name buckets the decision path relies on so TraceForge
    re-classifies the executed call with the same effect/mechanism/capability the
    decision was scored against — keeping the budget accrual consistent with the
    gate.
    """
    args = _coerce_args(raw_args)

    if tool_name.startswith(("mcp__", "mcp_")):
        sep = "__" if "__" in tool_name else "_"
        parts = tool_name.split(sep, 2)
        server = parts[1] if len(parts) >= 2 else None
        tool = parts[2] if len(parts) >= 3 else tool_name
        return Action(
            kind=ActionKind.mcp_tool,
            mcp_server=server,
            mcp_tool=tool,
            path=_first(args, _PATH_KEYS),
            tool_name=tool_name,
            job_id=job_id,
        )

    if tool_name in _SHELL_TOOLS:
        return Action(
            kind=ActionKind.shell,
            command=_first(args, _SHELL_COMMAND_KEYS),
            tool_name=tool_name,
            job_id=job_id,
        )

    if tool_name in _WRITE_TOOLS:
        return Action(
            kind=ActionKind.file,
            path=_first(args, _PATH_KEYS),
            tool_name=tool_name,
            job_id=job_id,
        )

    # Reads / search / url / memory / unknown → sdk_tool. A shell command may still
    # ride in the args (some SDKs wrap commands in generic tools).
    return Action(
        kind=ActionKind.sdk_tool,
        command=_first(args, _SHELL_COMMAND_KEYS),
        path=_first(args, _PATH_KEYS),
        tool_name=tool_name,
        job_id=job_id,
    )


class GovernanceSubscriber:
    """Accrue durable governance state from executed tool calls off the event bus."""

    def __init__(self, decider: GovernanceDecider) -> None:
        self._decider = decider

    def subscribe(self, event_bus: EventBus) -> None:
        """Attach this subscriber to an ``EventBus``."""
        event_bus.subscribe(self.handle_event)

    async def handle_event(self, event: SessionEvent) -> None:
        """Advance governance budget/taint for each executed tool call."""
        if is_observation_history(event):
            return
        if str(event.kind) != EventKind.tool_call_completed:
            return
        job_id = event.session_id
        if not job_id or not self._decider.is_registered(job_id):
            return  # sidecar / unknown session — not governed here

        payload = event.payload
        tool_call_id = payload.get("tool_call_id")
        if not tool_call_id:
            # Without a stable id we cannot dedupe a re-delivered event, so skip
            # rather than risk double-counting the budget.
            return
        tool_name = str(payload.get("tool_name") or "")
        if not tool_name:
            return

        action = action_from_completed_tool(job_id, tool_name, payload.get("arguments"))
        self._decider.observe(action, tool_call_id=str(tool_call_id))

    def cleanup(self, job_id: str) -> None:
        """No per-job state is held here; present for lifecycle symmetry."""
        return
