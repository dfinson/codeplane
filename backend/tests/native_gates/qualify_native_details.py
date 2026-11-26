import asyncio
from typing import TYPE_CHECKING

import pytest

from backend.services.adapters.native_gates._optional import load_sdk
from backend.tests.native_gates.producers import make_producer


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_real_agt_trust_tool_is_preserved_and_not_double_gated(asynchronous, binding_factory):
    from agentmesh.integrations.langchain import InMemoryTrustStore, TrustVerifiedTool
    from agentmesh.integrations.langchain.tools import TrustVerificationError

    calls = []
    trust = InMemoryTrustStore(default_score=700)

    def inner(query, **kwargs):
        calls.append(query)
        return query

    tool = TrustVerifiedTool(
        name="trusted",
        description="local",
        agent_did="did:mesh:local",
        trust_store=trust,
        inner_fn=inner,
    )
    gates, decision, _ = binding_factory("langchain")
    await gates.attach(tool)

    async def invoke():
        return await tool.ainvoke("local") if asynchronous else await asyncio.to_thread(tool.invoke, "local")

    assert await invoke() == "local"
    assert calls == ["local"] and len(decision.invocations) == 1
    trust.set_trust_score("did:mesh:local", 0)
    with pytest.raises(TrustVerificationError):
        await invoke()
    assert calls == ["local"] and len(decision.invocations) == 2
    decision.allow = False
    tools = load_sdk("langchain_core.tools")

    with pytest.raises(tools.ToolException, match="Denied by CodePlane"):
        await invoke()
    assert len(decision.invocations) == 3


async def test_native_langchain_callback_ids_and_error_policy_are_preserved(binding_factory):
    native_callbacks = load_sdk("langchain_core.callbacks")
    tools = load_sdk("langchain_core.tools")

    callbacks = []
    if TYPE_CHECKING:
        from backend.tests.native_gates.sdk_types import CallbackBase
    else:
        CallbackBase = native_callbacks.BaseCallbackHandler  # noqa: N806 - the real SDK base.

    class Observer(CallbackBase):
        def on_tool_start(self, serialized, input_str, **kwargs):
            callbacks.append(kwargs)

    producer = make_producer("langchain-async")
    producer.target.callbacks = [Observer()]
    gates, decision, _ = binding_factory("langchain")
    await gates.attach(producer.target)
    result = await producer.invoke()
    assert result.tool_call_id == "native-call"
    assert decision.invocations[0].native_tool_call_id == "native-call"
    assert decision.invocations[0].metadata.run_id == str(callbacks[0]["run_id"])
    decision.allow = False
    with pytest.raises(tools.ToolException):
        await producer.invoke()
    producer.target.handle_tool_error = True
    denied = await producer.invoke()
    assert denied.status == "error" and denied.tool_call_id == "native-call"


async def test_pydantic_runtime_toolsets_parts_usage_and_original_references(binding_factory):
    pydantic_ai = load_sdk("pydantic_ai")
    messages = load_sdk("pydantic_ai.messages")
    test_models = load_sdk("pydantic_ai.models.test")
    toolsets = load_sdk("pydantic_ai.toolsets")

    calls = []
    extra = toolsets.FunctionToolset()

    @extra.tool
    def local() -> str:
        calls.append(True)
        return "value"

    agent = pydantic_ai.Agent(test_models.TestModel())
    originals = agent._function_toolset, agent._user_toolsets, agent._dynamic_toolsets
    gates, decision, _ = binding_factory("pydantic_ai")
    await gates.attach(agent)
    result = await agent.run("local", toolsets=[extra])
    assert len(calls) == len(decision.invocations) == 1
    returned = [
        part for message in result.all_messages() for part in message.parts if isinstance(part, messages.ToolReturnPart)
    ]
    assert returned[0].tool_call_id == decision.invocations[0].native_tool_call_id
    assert result.usage().input_tokens > 0
    gates.detach(agent)
    assert agent._function_toolset is originals[0]
    assert agent._user_toolsets is originals[1]
    assert agent._dynamic_toolsets is originals[2]


async def test_openai_shared_tool_is_not_unwrapped_by_another_binding(binding_factory):
    agents = load_sdk("agents")

    producer = make_producer("openai_agents-async")
    sibling = agents.Agent(name="sibling", tools=producer.target.tools)
    first, decision, _ = binding_factory("openai_agents")
    second, _, _ = binding_factory("openai_agents", token="other")
    await first.attach(producer.target)
    with pytest.raises(ValueError, match="another gate"):
        await second.attach(sibling)
    second.detach(sibling)
    await producer.invoke()
    assert len(producer.calls) == len(decision.invocations) == 1


@pytest.mark.parametrize("allowed", [True, False])
async def test_real_crew_kickoff_with_scripted_local_llm(allowed, binding_factory):
    crewai = load_sdk("crewai")
    tool = load_sdk("crewai.tools").tool
    if TYPE_CHECKING:
        from backend.tests.native_gates.sdk_types import LLMBase
    else:
        LLMBase = crewai.BaseLLM  # noqa: N806 - the real SDK base.

    calls = []

    @tool("local")
    def local(value: str) -> str:
        """Return a local value."""
        calls.append(value)
        return "real-body-result"

    class ScriptedLLM(LLMBase):
        def __init__(self):
            super().__init__(model="local-script")
            self.inputs = []

        def call(self, messages, *args, **kwargs):
            self.inputs.append(str(messages))
            if len(self.inputs) == 1:
                return 'Thought: use local\nAction: local\nAction Input: {"value":"local"}'
            return "Thought: finished\nFinal Answer: done"

    model = ScriptedLLM()
    agent = crewai.Agent(role="local", goal="test", backstory="test", llm=model, tools=[local], allow_delegation=False)
    task = crewai.Task(description="Use local once", expected_output="done", agent=agent)
    crew = crewai.Crew(agents=[agent], tasks=[task], memory=False, cache=False, tracing=False)
    gates, decision, _ = binding_factory("crewai")
    decision.allow = allowed
    await gates.attach(crew)
    result = await asyncio.to_thread(crew.kickoff)
    assert result.raw == "done"
    assert calls == (["local"] if allowed else [])
    assert len(decision.invocations) == 1
    assert ("real-body-result" if allowed else "blocked by hook") in model.inputs[-1]


async def test_crewai_throwing_sibling_hook_cannot_bypass_deny(binding_factory):
    hooks = load_sdk("crewai.hooks")
    register_before_tool_call_hook = hooks.register_before_tool_call_hook
    unregister_before_tool_call_hook = hooks.unregister_before_tool_call_hook

    producer = make_producer("crewai-sync")

    def throwing(context):
        raise ValueError("observer failed")

    register_before_tool_call_hook(throwing)
    try:
        gates, decision, _ = binding_factory("crewai")
        decision.allow = False
        await gates.attach(producer.target)
        result = await producer.invoke()
        assert "blocked by hook" in result and not producer.calls
    finally:
        unregister_before_tool_call_hook(throwing)


async def test_original_openai_runner_executes_original_tools_without_network(binding_factory):
    agents = load_sdk("agents")
    usage = load_sdk("agents.usage")
    if TYPE_CHECKING:
        from backend.tests.native_gates.sdk_types import ModelBase
    else:
        ModelBase = agents.Model  # noqa: N806 - the real SDK base.
    responses = load_sdk("openai.types.responses")

    calls = []

    @agents.function_tool
    async def local(value: str) -> str:
        """Return a local value."""
        calls.append(value)
        return "tool-result"

    class ScriptedModel(ModelBase):
        def __init__(self):
            self.responses = 0

        async def get_response(self, *args, **kwargs):
            self.responses += 1
            output = (
                [
                    responses.ResponseFunctionToolCall(
                        id="call-local",
                        call_id="native-runner-call",
                        name="local",
                        arguments='{"value":"local"}',
                        type="function_call",
                    )
                ]
                if self.responses == 1
                else [
                    responses.ResponseOutputMessage(
                        id="message-local",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[responses.ResponseOutputText(text="done", type="output_text", annotations=[])],
                    )
                ]
            )
            return agents.ModelResponse(
                output=output, usage=usage.Usage(requests=1, input_tokens=2, output_tokens=1), response_id=None
            )

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("This local model qualifies Runner.run, not model streaming")
            yield

    agent = agents.Agent(name="original", model=ScriptedModel(), tools=[local])
    tools = agent.tools
    original = local.on_invoke_tool
    gates, decision, _ = binding_factory("openai_agents")
    await gates.attach(agent)
    result = await agents.Runner.run(agent, "local", run_config=agents.RunConfig(tracing_disabled=True))
    assert result.final_output == "done" and calls == ["local"]
    assert result.context_wrapper.usage.input_tokens == 4
    assert decision.invocations[0].native_tool_call_id == "native-runner-call"
    assert agent.tools is tools
    gates.detach(agent)
    assert local.on_invoke_tool is original


async def test_langgraph_keeps_actual_durable_checkpoint_and_blocking_node_trust(tmp_path, binding_factory):
    from agentmesh.integrations.langgraph import TrustedGraphNode, TrustVerificationError

    messages = load_sdk("langchain_core.messages")
    tools = load_sdk("langchain_core.tools")
    sqlite = load_sdk("langgraph.checkpoint.sqlite")
    graph_sdk = load_sdk("langgraph.graph")
    prebuilt = load_sdk("langgraph.prebuilt")

    calls = []

    def local() -> str:
        """A local graph tool."""
        calls.append(True)
        return "persisted-result"

    node = prebuilt.ToolNode([tools.StructuredTool.from_function(local)])
    gates, decision, _ = binding_factory("langgraph")
    await gates.attach(node)
    trusted = TrustedGraphNode(name="trusted", handler=node.ainvoke, fail_action="block")
    with pytest.raises(TrustVerificationError, match="No trust context"):
        await trusted({"messages": []})
    assert not calls and not decision.invocations
    graph = graph_sdk.StateGraph(graph_sdk.MessagesState)
    graph.add_node("tools", node)
    graph.add_edge(graph_sdk.START, "tools")
    graph.add_edge("tools", graph_sdk.END)
    config = {"configurable": {"thread_id": "durable-native"}}
    path = str(tmp_path / "graph.db")
    with sqlite.SqliteSaver.from_conn_string(path) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        result = await asyncio.to_thread(
            compiled.invoke,
            {
                "messages": [
                    messages.AIMessage(content="", tool_calls=[{"id": "graph-call", "name": "local", "args": {}}])
                ]
            },
            config,
        )
        assert result["messages"][-1].content == "persisted-result"
        assert compiled.checkpointer is checkpointer
    with sqlite.SqliteSaver.from_conn_string(path) as reopened:
        recovered = graph.compile(checkpointer=reopened).get_state(config)
        assert recovered.values["messages"][-1].tool_call_id == "graph-call"
    assert len(calls) == len(decision.invocations) == 1


async def test_pydantic_real_stream_events_keep_tool_parts(binding_factory):
    pydantic_ai = load_sdk("pydantic_ai")
    messages = load_sdk("pydantic_ai.messages")
    test_models = load_sdk("pydantic_ai.models.test")

    calls = []

    def local() -> str:
        calls.append(True)
        return "stream-result"

    agent = pydantic_ai.Agent(test_models.TestModel(), tools=[local])
    gates, decision, _ = binding_factory("pydantic_ai")
    await gates.attach(agent)
    events = [event async for event in agent.run_stream_events("local")]
    call = next(event for event in events if isinstance(event, messages.FunctionToolCallEvent))
    result = next(event for event in events if isinstance(event, messages.FunctionToolResultEvent))
    assert call.part.tool_call_id == result.result.tool_call_id == decision.invocations[0].native_tool_call_id
    assert result.result.content == "stream-result"
    assert calls == [True]
