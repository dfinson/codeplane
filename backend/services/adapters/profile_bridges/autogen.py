"""Version-qualified AutoGen RPC intervention and original FunctionTool entry."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import asdict, dataclass, is_dataclass
from functools import wraps
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import (
    ExecutionEvidence,
    NativeCall,
    NativeGateDeniedError,
    encode_arguments,
)
from backend.services.adapters.native_gates.lifecycle import retain_task
from backend.services.adapters.native_gates.observation_scope import native_execution_context
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError

if TYPE_CHECKING:
    from backend.services.adapters.native_gates._optional import InterventionHandler
    from backend.services.adapters.native_gates.installation import GateAttachment
else:
    InterventionHandler = load_sdk("autogen_core").DefaultInterventionHandler

core = load_sdk("autogen_core")
tools = load_sdk("autogen_core.tools")


@dataclass
class AutoGenTarget:
    runtime: Any
    tools: tuple[Any, ...]


def _message(message: Any) -> Any:
    if isinstance(message, BaseModel):
        return message.model_dump(mode="json")
    if is_dataclass(message) and not isinstance(message, type):
        return asdict(message)
    if isinstance(message, (str, dict, list, int, float, bool)) or message is None:
        return message
    raise UnsupportedNativeHookError("AutoGen gating requires JSON, dataclass or Pydantic messages")


def install(gate: GateAttachment, target: object) -> None:
    if not isinstance(target, AutoGenTarget) or not isinstance(target.runtime, core.SingleThreadedAgentRuntime):
        raise TypeError("AutoGen requires the caller's SingleThreadedAgentRuntime and FunctionTools")
    runtime = target.runtime
    if runtime._run_context is not None or runtime._background_tasks:
        raise UnsupportedNativeHookError("Attach AutoGen before starting its caller-owned message runtime")
    if any(not isinstance(tool, tools.FunctionTool) for tool in target.tools):
        raise UnsupportedNativeHookError("Only actual AutoGen FunctionTools have a qualified local entry boundary")
    gate.claim(runtime)
    requests: dict[str, tuple[NativeCall, str]] = {}
    agents: set[int] = set()
    current_message: ContextVar[str | None] = ContextVar("autogen_message", default=None)

    def fingerprint(message: Any, context: Any, recipient: Any) -> str:
        return encode_arguments(
            {
                "message": _message(message),
                "message_object": id(message),
                "message_type": f"{type(message).__module__}.{type(message).__qualname__}",
                "message_id": context.message_id,
                "sender": str(context.sender) if context.sender is not None else None,
                "recipient": str(recipient),
                "cancellation": id(context.cancellation_token),
                "is_rpc": context.is_rpc,
                "topic_id": str(context.topic_id) if context.topic_id is not None else None,
            }
        )

    class CodePlaneIntervention(InterventionHandler):
        async def on_send(self, message: Any, *, message_context: Any, recipient: Any) -> Any:
            dropped = True
            try:
                result = await self._decide(message, message_context=message_context, recipient=recipient)
                dropped = result is core.DropMessage
                return result
            finally:
                if dropped:
                    # 0.7.5 omits task_done on intervention drops/errors. Balance
                    # only this consumed request, never stop or drain the runtime.
                    runtime._message_queue.task_done()

        async def _decide(self, message: Any, *, message_context: Any, recipient: Any) -> Any:
            if message_context.message_id in requests or message_context.cancellation_token.is_cancelled():
                return core.DropMessage
            call = NativeCall(
                "autogen.send_message",
                {
                    "message": _message(message),
                    "message_id": message_context.message_id,
                    "sender": str(message_context.sender) if message_context.sender is not None else None,
                    "recipient": str(recipient),
                },
                definition="Caller-owned AutoGen RPC delivery to a local agent handler, not remote completion.",
                fingerprint=lambda: fingerprint(message, message_context, recipient),
                evidence=ExecutionEvidence(False),
            )
            if not await gate.allow(call):
                return core.DropMessage
            requests[message_context.message_id] = (call, fingerprint(message, message_context, recipient))
            return message

    handler = CodePlaneIntervention()
    previous = runtime._intervention_handlers
    if previous is None:
        runtime._intervention_handlers = []
    runtime._intervention_handlers.insert(0, handler)

    def remove() -> None:
        handlers = runtime._intervention_handlers
        if handlers is not None:
            handlers[:] = [item for item in handlers if item is not handler]
            if not handlers and previous is None:
                runtime._intervention_handlers = None

    gate.cleanup(remove)
    get_agent = runtime._get_agent

    async def owned_agent(agent_id: Any) -> Any:
        lifecycle = gate.binding.begin_work(None)
        try:
            agent = await get_agent(agent_id)
            if not gate.active:
                raise NativeGateDeniedError("AutoGen agent construction completed after revocation")
        finally:
            gate.binding.end_work(lifecycle)
        if id(agent) in agents:
            gate.validate()
            return agent
        gate.track_callable(agent, "on_message")
        original = agent.on_message

        async def receive(message: Any, ctx: Any) -> Any:
            approved = requests.get(ctx.message_id)
            if approved is None:
                raise NativeGateDeniedError("AutoGen message has no exact approved intervention")
            call, snapshot = approved
            if (
                not await gate.binding.current()
                or ctx.cancellation_token.is_cancelled()
                or fingerprint(message, ctx, agent.id) != snapshot
            ):
                raise NativeGateDeniedError("AutoGen message changed or was revoked before handler entry")
            gate.validate()
            scope = native_execution_context.set(gate.binding.execution)
            message_scope = current_message.set(ctx.message_id)
            lifecycle = gate.binding.begin_work(None)
            raw = {
                "message_id": ctx.message_id,
                "sender": str(ctx.sender) if ctx.sender is not None else None,
                "recipient": str(agent.id),
                "message": _message(message),
            }
            try:
                gate.binding.observe({"type": "message_started", **raw})
                result = await gate.complete(call, lambda: original(message, ctx))
                await gate.binding.supplied_usage(call.operation_id, result)
                gate.binding.observe({"type": "message_completed", **raw, "result": _message(result)})
                return result
            finally:
                current_message.reset(message_scope)
                native_execution_context.reset(scope)
                requests.pop(ctx.message_id, None)
                gate.binding.end_work(lifecycle)

        gate.patch(agent, "on_message", receive)
        agents.add(id(agent))
        return agent

    gate.patch(runtime, "_get_agent", owned_agent)
    process_send = runtime._process_send

    async def retained_send(envelope: Any) -> None:
        lifecycle = gate.binding.begin_work(None)
        try:
            if not gate.active:
                if not envelope.future.done():
                    envelope.future.set_exception(NativeGateDeniedError("AutoGen delivery was revoked"))
                runtime._message_queue.task_done()
                return
            await process_send(envelope)
        finally:
            gate.binding.end_work(lifecycle)

    gate.patch(runtime, "_process_send", retained_send)

    async def unsupported_publish(*args: Any, **kwargs: Any) -> None:
        raise UnsupportedNativeHookError(
            "AutoGen publish is enqueue-only; this attachment qualifies awaited RPC send_message, not publication"
        )

    gate.patch(runtime, "publish_message", unsupported_publish)
    for tool in target.tools:
        _install_tool(gate, tool, current_message)


def _install_tool(gate: GateAttachment, tool: Any, current_message: ContextVar[str | None]) -> None:
    gate.claim(tool)
    function = tool._func
    entry = SimpleNamespace(invoke=function)
    gate.track_callable(entry, "invoke")
    run, run_json = tool.run, tool.run_json
    native_call_id: ContextVar[str | None] = ContextVar("autogen_tool_call", default=None)

    @wraps(function)
    async def leaf(*args: Any, **kwargs: Any) -> Any:
        if asyncio.iscoroutinefunction(function):
            return await entry.invoke(*args, **kwargs)
        return await retain_task(asyncio.create_task(asyncio.to_thread(entry.invoke, *args, **kwargs)))

    gate.patch(tool, "_func", leaf)

    async def guarded(args: Any, cancellation_token: Any) -> Any:
        def fingerprint() -> str:
            return encode_arguments(
                {
                    "arguments": args.model_dump(mode="json"),
                    "function": id(tool._func),
                    "schema": tool.schema,
                    "signature": str(tool._signature),
                    "cancellation_support": tool._has_cancellation_support,
                }
            )

        call = NativeCall(
            tool.name,
            {"arguments": args.model_dump(mode="json"), "message_id": current_message.get()},
            tool_call_id=native_call_id.get(),
            definition=tool.description,
            fingerprint=fingerprint,
            evidence=ExecutionEvidence(False),
        )
        if cancellation_token.is_cancelled() or not await gate.allow(call) or cancellation_token.is_cancelled():
            raise NativeGateDeniedError("AutoGen function denied before native invoker")

        succeeded: bool | None = None

        async def observed_run() -> Any:
            nonlocal succeeded
            try:
                result = await run(args, cancellation_token)
            except Exception:
                if call.evidence.started:
                    succeeded = False
                raise
            succeeded = True if call.evidence.started else None
            return result

        async def invoke() -> Any:
            pending = asyncio.create_task(observed_run())
            revoked = asyncio.create_task(gate.binding.wait_revoked())
            try:
                await asyncio.wait((pending, revoked), return_when=asyncio.FIRST_COMPLETED)
                if revoked.done():
                    cancellation_token.cancel()
                return await retain_task(pending)
            finally:
                revoked.cancel()
                await asyncio.gather(revoked, return_exceptions=True)

        raw = {
            "tool_name": tool.name,
            "tool_call_id": native_call_id.get(),
            "message_id": current_message.get(),
            "arguments": args.model_dump(mode="json"),
        }
        gate.binding.observe({"type": "tool_started", **raw})
        try:
            result = await gate.complete(call, invoke)
        except BaseException as exc:
            gate.binding.observe({"type": "tool_failed", **raw, "error_type": type(exc).__name__, "success": succeeded})
            raise
        gate.binding.observe(
            {"type": "tool_completed", **raw, "result": tool.return_value_as_string(result), "success": succeeded}
        )
        return result

    async def identified(args: Any, cancellation_token: Any, call_id: str | None = None) -> Any:
        token = native_call_id.set(call_id)
        try:
            return await run_json(args, cancellation_token, call_id)
        finally:
            native_call_id.reset(token)

    gate.patch(tool, "run", guarded)
    gate.patch(tool, "run_json", identified)
