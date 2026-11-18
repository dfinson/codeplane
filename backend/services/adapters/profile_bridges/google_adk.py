"""ADK native plugin gates plus final request/function entry fences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import (
    ExecutionEvidence,
    NativeCall,
    NativeGateDeniedError,
    encode_arguments,
)
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from backend.services.adapters.native_gates._optional import ADKPlugin
    from backend.services.adapters.native_gates.installation import GateAttachment
else:
    ADKPlugin = load_sdk("google.adk.plugins.base_plugin").BasePlugin

runners = load_sdk("google.adk.runners")
types = load_sdk("google.genai.types")
FunctionTool = load_sdk("google.adk.tools.function_tool").FunctionTool
BaseLlm = load_sdk("google.adk.models.base_llm").BaseLlm
LlmResponse = load_sdk("google.adk.models.llm_response").LlmResponse


@dataclass
class GoogleADKTarget:
    runner: Any
    tools: tuple[Any, ...]
    models: tuple[Any, ...] = ()


def install(gate: GateAttachment, target: object) -> None:
    if not isinstance(target, GoogleADKTarget) or not isinstance(target.runner, runners.Runner):
        raise TypeError("ADK requires the original caller-owned Runner")
    if any(not isinstance(tool, FunctionTool) for tool in target.tools):
        raise TypeError("ADK local blocking requires actual FunctionTools")
    if any(not isinstance(model, BaseLlm) for model in target.models):
        raise TypeError("ADK provider gating requires caller-owned BaseLlm instances")
    runner = target.runner
    bound_tools, bound_models = target.tools, target.models
    gate.claim(runner)

    def unsupported_live(*args: Any, **kwargs: Any) -> Any:
        raise UnsupportedNativeHookError(
            "ADK live bidirectional input requires a separately acknowledged native binding"
        )

    gate.patch(runner, "run_live", unsupported_live)
    pending_tools: dict[int, tuple[NativeCall, str]] = {}
    pending_models: dict[int, tuple[NativeCall, str]] = {}
    pending_runs: dict[str, tuple[NativeCall, str]] = {}
    denied = types.Content(role="model", parts=[types.Part(text="Denied by CodePlane")])

    def run_fingerprint(ctx: Any) -> str:
        return encode_arguments(
            {
                "agent": id(runner.agent),
                "session_id": ctx.session.id,
                "user_id": ctx.user_id,
                "invocation_id": ctx.invocation_id,
                "content": ctx.user_content.model_dump(mode="json") if ctx.user_content is not None else None,
            }
        )

    def tool_fingerprint(tool: Any, args: Any, ctx: Any) -> str:
        return encode_arguments(
            {
                "tool": id(tool),
                "function": id(tool.func),
                "name": tool.name,
                "args": args,
                "call_id": ctx.function_call_id,
                "run_id": ctx.invocation_id,
            }
        )

    def model_fingerprint(model: Any, request: Any) -> str:
        return encode_arguments(
            {
                "model": id(model),
                "name": model.model,
                "request": request.model_dump(mode="json", exclude={"tools_dict"}),
            }
        )

    class Plugin(ADKPlugin):
        async def before_run_callback(self, *, invocation_context: Any) -> Any:
            ctx = invocation_context
            call = NativeCall(
                "google-adk.run",
                {"request": run_fingerprint(ctx)},
                run_id=ctx.invocation_id,
                fingerprint=lambda: run_fingerprint(ctx),
            )
            if not await gate.allow(call):
                return denied
            pending_runs[ctx.invocation_id] = (call, run_fingerprint(ctx))
            return None

        async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> Any:
            model = callback_context._invocation_context.agent.model
            if not any(model is candidate for candidate in bound_models):
                return LlmResponse(content=denied)
            # ADK adds this billing label after callbacks. Normalize that exact
            # native default before approval rather than ignore later changes.
            label = load_sdk("google.adk.flows.llm_flows.core._model_call").ADK_AGENT_NAME_LABEL_KEY
            llm_request.config = llm_request.config or types.GenerateContentConfig()
            llm_request.config.labels = llm_request.config.labels or {}
            llm_request.config.labels.setdefault(label, callback_context.agent_name)
            call = NativeCall(
                "google-adk.model",
                {"request": model_fingerprint(model, llm_request)},
                run_id=callback_context.invocation_id,
                fingerprint=lambda: model_fingerprint(model, llm_request),
            )
            if not await gate.allow(call):
                return LlmResponse(content=denied)
            pending_models[id(llm_request)] = (call, model_fingerprint(model, llm_request))
            return None

        async def before_tool_callback(self, *, tool: Any, tool_args: Any, tool_context: Any) -> Any:
            if not any(tool is candidate for candidate in bound_tools):
                return {"error": "Unsupported ADK tool boundary"}
            call = NativeCall(
                tool.name,
                tool_args,
                tool_call_id=tool_context.function_call_id,
                run_id=tool_context.invocation_id,
                fingerprint=lambda: tool_fingerprint(tool, tool_args, tool_context),
                evidence=ExecutionEvidence(False),
            )
            if not await gate.allow(call):
                return {"error": "Denied by CodePlane"}
            pending_tools[id(tool_context)] = (call, tool_fingerprint(tool, tool_args, tool_context))
            return None

        async def after_tool_callback(self, *, tool: Any, tool_args: Any, tool_context: Any, result: Any) -> None:
            gate.binding.observe(
                {
                    "type": "tool_observed",
                    "tool_name": tool.name,
                    "tool_call_id": tool_context.function_call_id,
                    "run_id": tool_context.invocation_id,
                    "arguments": tool_args,
                    "result": result,
                }
            )

        async def on_event_callback(self, *, invocation_context: Any, event: Any) -> None:
            gate.binding.observe({"type": "event", "run_id": event.invocation_id, **event.model_dump(mode="json")})
            if not event.partial:
                await gate.binding.supplied_usage(event.id, event)

    plugin = Plugin(name=f"codeplane-{gate.binding.execution.execution_token}")
    manager = runner.plugin_manager
    manager.register_plugin(plugin)
    manager.plugins.remove(plugin)
    manager.plugins.insert(0, plugin)

    def remove() -> None:
        manager.plugins[:] = [item for item in manager.plugins if item is not plugin]

    gate.cleanup(remove)
    original_run = runner.agent.run_async

    async def run(ctx: Any) -> AsyncIterator[Any]:
        admitted = pending_runs.pop(ctx.invocation_id, None)
        if admitted is None or admitted[1] != run_fingerprint(ctx) or not await gate.binding.current():
            raise NativeGateDeniedError("ADK invocation changed before original agent entry")
        call = admitted[0]
        gate.validate()
        lifecycle = gate.binding.begin_work(None)
        completed = False
        try:
            async for event in original_run(ctx):
                call.evidence.started = True
                yield event
            completed = True
        finally:
            try:
                await gate.binding.report(call, call.evidence.started, completed)
            finally:
                gate.binding.end_work(lifecycle)

    gate.claim(runner.agent)
    gate.patch(runner.agent, "run_async", run)
    for tool in target.tools:
        _tool(gate, tool, pending_tools, tool_fingerprint)
    for model in target.models:
        _model(gate, model, pending_models, model_fingerprint)


def _tool(gate: GateAttachment, tool: Any, pending: dict[int, tuple[NativeCall, str]], fingerprint: Any) -> None:
    gate.claim(tool)
    gate.track_callable(tool, "func")
    original = tool.run_async

    async def guarded(*, args: Any, tool_context: Any) -> Any:
        admitted = pending.pop(id(tool_context), None)
        if admitted is None or fingerprint(tool, args, tool_context) != admitted[1] or not await gate.binding.current():
            raise NativeGateDeniedError("ADK tool changed or was revoked before the original function")
        call = admitted[0]
        gate.validate()
        gate.binding.observe(
            {
                "type": "tool_started",
                "tool_name": tool.name,
                "tool_call_id": tool_context.function_call_id,
                "run_id": tool_context.invocation_id,
                "arguments": args,
            }
        )
        return await gate.complete(call, lambda: original(args=args, tool_context=tool_context))

    gate.patch(tool, "run_async", guarded)


def _model(gate: GateAttachment, model: Any, pending: dict[int, tuple[NativeCall, str]], fingerprint: Any) -> None:
    gate.claim(model)
    original = model.generate_content_async

    async def guarded(llm_request: Any, stream: bool = False) -> AsyncIterator[Any]:
        admitted = pending.pop(id(llm_request), None)
        if admitted is None or fingerprint(model, llm_request) != admitted[1] or not await gate.binding.current():
            raise NativeGateDeniedError("ADK model request changed before provider entry")
        call = admitted[0]
        gate.validate()
        completed = False
        try:
            async for response in original(llm_request, stream=stream):
                call.evidence.started = True
                yield response
            completed = True
        finally:
            await gate.binding.report(call, call.evidence.started, completed)

    gate.patch(model, "generate_content_async", guarded)
