"""Scoped native CrewAI hooks. Exceptions cannot be used to deny in this SDK."""

from __future__ import annotations

from contextvars import ContextVar
from inspect import signature
from typing import TYPE_CHECKING, Any

import structlog

from ._optional import load_sdk
from .binding import ExecutionEvidence, NativeCall, NativeGateDeniedError, encode_arguments

if TYPE_CHECKING:
    from .installation import GateAttachment

log = structlog.get_logger()
Crew = load_sdk("crewai").Crew
_hooks = load_sdk("crewai.hooks")
tool_hooks = _hooks.tool_hooks
register_before_tool_call_hook = _hooks.register_before_tool_call_hook
register_after_tool_call_hook = _hooks.register_after_tool_call_hook
unregister_before_tool_call_hook = _hooks.unregister_before_tool_call_hook
unregister_after_tool_call_hook = _hooks.unregister_after_tool_call_hook


def install(gate: GateAttachment, crew: object) -> None:
    if not isinstance(crew, Crew):
        raise TypeError("CrewAI gating requires the original Crew")
    active: ContextVar[tuple[Any, NativeCall, list[bool]] | None] = ContextVar("native_crewai_invocation", default=None)
    tools: dict[int, Any] = {}

    def install_leaf(tool: Any) -> Any:
        if id(tool) in tools:
            return tools[id(tool)]
        gate.claim(tool)
        original = tool.func
        parameters = signature(original)

        def leaf(*args: Any, **kwargs: Any) -> Any:
            state = active.get()
            if state is None or state[0] is not tool or not state[2] or not gate.active:
                raise NativeGateDeniedError("Denied by CodePlane: no current CrewAI invocation")
            _, call, available = state
            actual = parameters.bind(*args, **kwargs)
            actual.apply_defaults()
            if encode_arguments(actual.arguments) != encode_arguments(call.arguments):
                raise NativeGateDeniedError("Denied by CodePlane: CrewAI arguments changed after approval")
            available.pop()
            token = gate.binding.begin_work(call)

            def invoke() -> Any:
                try:
                    return original(*args, **kwargs)
                finally:
                    gate.binding.end_work(token)

            return gate.complete_sync(call, invoke)

        gate.patch(tool, "func", leaf)
        tools[id(tool)] = parameters
        return parameters

    def before(context: Any) -> bool | None:
        if context.crew is not crew:
            return None
        active.set(None)
        try:
            parameters = install_leaf(context.tool)
            arguments = parameters.bind(**context.tool_input)
            arguments.apply_defaults()
            call = NativeCall(
                context.tool_name,
                arguments.arguments,
                definition=context.tool.description,
                evidence=ExecutionEvidence(False),
            )
            allowed = gate.allow_sync(call)
            if allowed:
                active.set((context.tool, call, [True]))
        except Exception:
            # CrewAI logs and ignores hook exceptions. Return its actual stop value.
            log.exception("native_crewai_permission_failed", job_id=gate.binding.execution.job_id)
            return False
        return None if allowed else False

    def after(context: Any) -> None:
        if context.crew is crew:
            active.set(None)
        return None

    def remove_before() -> None:
        unregister_before_tool_call_hook(before)

    def remove_after() -> None:
        unregister_after_tool_call_hook(after)

    register_before_tool_call_hook(before)
    # This version catches an entire hook loop and then executes the tool. A
    # throwing earlier observer must not skip the enforcing hook.
    tool_hooks._before_tool_call_hooks.remove(before)
    tool_hooks._before_tool_call_hooks.insert(0, before)
    gate.cleanup(remove_before)
    register_after_tool_call_hook(after)
    gate.cleanup(remove_after)
