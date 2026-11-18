"""Actual AutoGen/LlamaIndex/ADK inputs at registered caller-owned run boundaries."""

import asyncio
from typing import Any

import pytest

from backend.models.domain import StateConflictError
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.profile_bridges.autogen import AutoGenTarget
from backend.services.adapters.profile_bridges.llamaindex import LlamaIndexTarget
from backend.tests.native_gates.qualify_autogen import Request, RoutedAgent, core
from backend.tests.native_gates.qualify_google_adk import make_target as make_adk_target
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for


@pytest.mark.parametrize("family", ["autogen", "llamaindex", "google-adk"])
@pytest.mark.parametrize("cancel_before_boundary", [False, True])
async def test_registered_operator_input_is_consumed_by_original_sdk(family, cancel_before_boundary, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    ready, boundary = asyncio.Event(), asyncio.Event()
    observed = []
    native_ids = {}
    autogen_runtime = None
    adk_runner = None

    def body(value: str) -> str:
        return value

    if family == "autogen":
        tools = load_sdk("autogen_core.tools")
        tool = tools.FunctionTool(body, "Inert local function")

        class Receiver(RoutedAgent):
            @core.message_handler
            async def receive(self, message: Request, ctx: Any) -> Any:
                value = await tool.run_json({"value": message.value}, ctx.cancellation_token)
                observed.append(value)
                native_ids["message_id"] = ctx.message_id
                return value

        autogen_runtime = core.SingleThreadedAgentRuntime()
        await Receiver.register(autogen_runtime, "local", lambda: Receiver("caller-owned"))
        target = AutoGenTarget(autogen_runtime, (tool,))

        async def receive_autogen(message):
            result = await autogen_runtime.send_message(Request(message.content), core.AgentId("local", "default"))
            assert result == message.content
            await host.acknowledge_native_input(message, dict(native_ids))
            return result

        receiver = receive_autogen
    elif family == "llamaindex":
        chat = load_sdk("llama_index.core.chat_engine.simple")
        callbacks = load_sdk("llama_index.core.callbacks")
        memory = load_sdk("llama_index.core.memory")
        mock = load_sdk("llama_index.core.llms.mock")
        manager = callbacks.CallbackManager([])
        engine = chat.SimpleChatEngine(
            mock.MockLLM(max_tokens=2, callback_manager=manager),
            memory.ChatMemoryBuffer.from_defaults(token_limit=100, tokenizer_fn=lambda value: value.split()),
            [],
            callback_manager=manager,
        )
        target = LlamaIndexTarget(engine, ())

        async def receive_llama(message):
            result = await engine.achat(message.content)
            assert any(item.role == "user" and item.content == message.content for item in engine.chat_history)
            observed.append(message.content)
            await host.acknowledge_native_input(message, {})
            return result

        receiver = receive_llama
    else:
        target, _, requests = await make_adk_target(body)
        adk_runner = target.runner
        types = load_sdk("google.genai.types")

        async def receive_adk(message):
            output = [
                event
                async for event in target.runner.run_async(
                    user_id="local",
                    session_id="native-session",
                    new_message=types.Content(role="user", parts=[types.Part(text=message.content)]),
                )
            ]
            assert any(
                part.text == message.content
                for content in requests[-1].contents
                if content.role == "user"
                for part in content.parts
            )
            observed.append(message.content)
            await host.acknowledge_native_input(message, {"invocation_id": output[-1].invocation_id})
            return output

        receiver = receive_adk

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)

    async def invoke():
        if autogen_runtime is not None:
            autogen_runtime.start()
        try:
            ready.set()
            await boundary.wait()
            await host.consume_native_input()
            await host.consume_native_input()
            return "two SDK runs"
        finally:
            if autogen_runtime is not None:
                await autogen_runtime.stop()

    task = await host.start_native(job.id, family=family, target=target, invoke=invoke, input_handler=receiver)
    try:
        await asyncio.wait_for(ready.wait(), 5)
        context = await host.get_current_execution_context(job.id)
        assert await host.send_operator_message(job.id, "first original SDK input")
        assert await host.send_operator_message(job.id, "second original SDK input")
        before = await events_for(host, job.id)
        queued = [event for event in before if event.kind == "execution.native.input_queued"]
        assert len(queued) == 2 and not observed
        assert not any(event.kind == "execution.native.input_consumed" for event in before)
        if cancel_before_boundary:
            await host.cancel_job(job.id)
            assert not task.done()
            boundary.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 15)
        else:
            boundary.set()
            assert await asyncio.wait_for(task, 15) == "two SDK runs"
    finally:
        boundary.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if adk_runner is not None:
            await adk_runner.close()
    events = await events_for(host, job.id)
    consumed = [event for event in events if event.kind == "execution.native.input_consumed"]
    undelivered = [event for event in events if event.kind == "execution.native.input_undelivered"]
    if cancel_before_boundary:
        assert not observed and not consumed
        terminal, status = undelivered, "undelivered"
    else:
        assert observed == ["first original SDK input", "second original SDK input"]
        assert not undelivered
        terminal, status = consumed, "consumed"
    assert [event.payload["message_id"] for event in terminal] == [event.payload["message_id"] for event in queued]
    assert all(event.payload["delivery_status"] == status for event in terminal)
    for event in queued + terminal:
        assert event.payload["execution"]["job_id"] == job.id
        assert event.payload["execution"]["execution_token"] == context.execution_token
        assert event.payload["execution"] == queued[0].payload["execution"]
        assert event.payload["admission_id"] == queued[0].payload["admission_id"]
    with pytest.raises(StateConflictError):
        await host.send_operator_message(job.id, "must not become a managed resume")
