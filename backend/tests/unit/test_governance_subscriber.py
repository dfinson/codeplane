"""Unit tests for the governance accrual subscriber (A2 bus-subscriber pattern).

Assert the core invariant (binding condition §2): **executed tool calls accrue,
everything else does not.** The subscriber must call ``decider.observe`` exactly
once for an executed (``tool.call.completed``) call on a registered job with a
stable id, and must stay silent for non-completed kinds, unregistered jobs, and
malformed payloads — so a rejected/denied call never advances the budget. Also
covers ``action_from_completed_tool`` reconstruction across tool-name buckets.
"""

from __future__ import annotations

from typing import Any

from backend.models.events import EventKind, SessionEvent, new_event
from backend.services.action_policy.classifier import Action, ActionKind
from backend.services.events.governance_subscriber import (
    GovernanceSubscriber,
    action_from_completed_tool,
)


class _SpyDecider:
    """Records observe() calls; controls registration for the negative paths."""

    def __init__(self, *, registered: bool = True) -> None:
        self._registered = registered
        self.observed: list[tuple[Action, str]] = []

    def is_registered(self, job_id: str) -> bool:
        return self._registered

    def observe(self, action: Action, *, tool_call_id: str) -> None:
        self.observed.append((action, tool_call_id))


def _event(kind: Any, *, session_id: str = "j1", payload: dict[str, Any] | None = None) -> SessionEvent:
    return new_event(session_id=session_id, kind=kind, payload=payload or {})


# ---------------------------------------------------------------------------
# The accrual invariant
# ---------------------------------------------------------------------------


async def test_executed_call_accrues_once() -> None:
    spy = _SpyDecider(registered=True)
    sub = GovernanceSubscriber(spy)  # type: ignore[arg-type]
    ev = _event(
        EventKind.tool_call_completed,
        payload={"tool_call_id": "tc1", "tool_name": "bash", "arguments": {"command": "ls -la"}},
    )
    await sub.handle_event(ev)
    assert len(spy.observed) == 1
    action, tool_call_id = spy.observed[0]
    assert tool_call_id == "tc1"
    assert action.kind == ActionKind.shell
    assert action.command == "ls -la"
    assert action.job_id == "j1"


async def test_non_completed_kind_does_not_accrue() -> None:
    spy = _SpyDecider(registered=True)
    sub = GovernanceSubscriber(spy)  # type: ignore[arg-type]
    ev = _event(
        EventKind.tool_call_started,
        payload={"tool_call_id": "tc1", "tool_name": "bash"},
    )
    await sub.handle_event(ev)
    assert spy.observed == []


async def test_unregistered_job_does_not_accrue() -> None:
    spy = _SpyDecider(registered=False)
    sub = GovernanceSubscriber(spy)  # type: ignore[arg-type]
    ev = _event(
        EventKind.tool_call_completed,
        payload={"tool_call_id": "tc1", "tool_name": "bash"},
    )
    await sub.handle_event(ev)
    assert spy.observed == []


async def test_missing_tool_call_id_does_not_accrue() -> None:
    # Without a stable id the event can't be deduped → skip rather than double-count.
    spy = _SpyDecider(registered=True)
    sub = GovernanceSubscriber(spy)  # type: ignore[arg-type]
    ev = _event(EventKind.tool_call_completed, payload={"tool_name": "bash"})
    await sub.handle_event(ev)
    assert spy.observed == []


async def test_missing_tool_name_does_not_accrue() -> None:
    spy = _SpyDecider(registered=True)
    sub = GovernanceSubscriber(spy)  # type: ignore[arg-type]
    ev = _event(EventKind.tool_call_completed, payload={"tool_call_id": "tc1"})
    await sub.handle_event(ev)
    assert spy.observed == []


async def test_missing_session_id_does_not_accrue() -> None:
    spy = _SpyDecider(registered=True)
    sub = GovernanceSubscriber(spy)  # type: ignore[arg-type]
    ev = _event(
        EventKind.tool_call_completed,
        session_id="",
        payload={"tool_call_id": "tc1", "tool_name": "bash"},
    )
    await sub.handle_event(ev)
    assert spy.observed == []


# ---------------------------------------------------------------------------
# action_from_completed_tool reconstruction across tool-name buckets
# ---------------------------------------------------------------------------


def test_reconstruct_shell_tool() -> None:
    a = action_from_completed_tool("j1", "bash", {"command": "git status"})
    assert a.kind == ActionKind.shell
    assert a.command == "git status"
    assert a.job_id == "j1"


def test_reconstruct_write_tool() -> None:
    a = action_from_completed_tool("j1", "create_file", {"path": "src/app.py"})
    assert a.kind == ActionKind.file
    assert a.path == "src/app.py"


def test_reconstruct_mcp_tool() -> None:
    a = action_from_completed_tool("j1", "mcp__github__create_issue", {})
    assert a.kind == ActionKind.mcp_tool
    assert a.mcp_server == "github"
    assert a.mcp_tool == "create_issue"


def test_reconstruct_sdk_read_tool() -> None:
    a = action_from_completed_tool("j1", "read", {"path": "src/app.py"})
    assert a.kind == ActionKind.sdk_tool
    assert a.path == "src/app.py"


def test_reconstruct_from_json_string_arguments() -> None:
    # Some SDKs deliver arguments as a JSON string, not a dict.
    a = action_from_completed_tool("j1", "bash", '{"command": "echo hi"}')
    assert a.kind == ActionKind.shell
    assert a.command == "echo hi"
