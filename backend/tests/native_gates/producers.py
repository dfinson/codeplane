"""Real pinned SDK producers and inert local bodies. No fake hook contexts."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.services.adapters.native_gates._optional import load_sdk


@dataclass
class Producer:
    target: Any = None
    invoke: Any = None
    calls: list = field(default_factory=list)
    leaf: Any = None
    leaf_attribute: str = ""

    def body(self, value: str = "value") -> str:
        """Return the input without performing I/O."""
        self.calls.append(value)
        return f"result:{value}"

    async def abody(self, value: str = "value") -> str:
        """Return the input without performing I/O."""
        return self.body(value)


MODES = (
    "langchain-sync",
    "langchain-async",
    "langchain-fallback",
    "langgraph-sync",
    "langgraph-async",
    "crewai-sync",
    "semantic_kernel-async",
    "maf-async",
    "smolagents-sync",
    "pydantic_ai-async",
    "pydantic_ai-sync",
    "openai_agents-async",
)


def make_producer(mode):
    p = Producer()
    family, path = mode.split("-")
    if family == "langchain":
        tools = load_sdk("langchain_core.tools")

        tool = tools.StructuredTool.from_function(
            p.body, coroutine=p.abody if path != "fallback" else None, name="local"
        )
        p.target = tool
        p.leaf, p.leaf_attribute = tool, "coroutine" if path == "async" else "func"
        args = {"type": "tool_call", "name": "local", "args": {"value": "value"}, "id": "native-call"}

        async def invoke():
            if path == "sync":
                return await asyncio.to_thread(tool.invoke, args)
            return await tool.ainvoke(args)

    elif family == "langgraph":
        messages = load_sdk("langchain_core.messages")
        tools = load_sdk("langchain_core.tools")
        graph_sdk = load_sdk("langgraph.graph")
        prebuilt = load_sdk("langgraph.prebuilt")

        tool = tools.StructuredTool.from_function(p.body, coroutine=p.abody, name="local")
        p.target = prebuilt.ToolNode([tool])
        p.leaf, p.leaf_attribute = tool, "coroutine" if path == "async" else "func"
        graph = graph_sdk.StateGraph(graph_sdk.MessagesState)
        graph.add_node("tools", p.target)
        graph.add_edge(graph_sdk.START, "tools")
        graph.add_edge("tools", graph_sdk.END)
        runnable = graph.compile()

        async def invoke():
            args = {
                "messages": [
                    messages.AIMessage(content="", tool_calls=[{"name": "local", "args": {}, "id": "native-call"}])
                ]
            }
            result = await asyncio.to_thread(runnable.invoke, args) if path == "sync" else await runnable.ainvoke(args)
            return result["messages"][-1]

    elif family == "semantic_kernel":
        kernel = load_sdk("semantic_kernel")
        contents = load_sdk("semantic_kernel.contents")
        kernel_function = load_sdk("semantic_kernel.functions").kernel_function

        p.target = kernel.Kernel()

        @kernel_function(name="local", description="Inert local tool")
        def local(value: str = "value") -> str:
            return p.body(value)

        p.target.add_function(plugin_name="test", function=local)
        p.leaf, p.leaf_attribute = p.target.get_function("test", "local"), "method"

        async def invoke():
            history = contents.ChatHistory()
            await p.target.invoke_function_call(
                contents.FunctionCallContent(id="native-call", name="test-local", arguments='{"value":"value"}'),
                history,
            )
            return history.messages[-1].items[0].result

    elif family == "maf":
        maf = load_sdk("agent_framework")
        middleware = load_sdk("agent_framework._middleware")

        from backend.services.adapters.native_gates.maf import MiddlewareSlot

        p.target = MiddlewareSlot([])
        tool = maf.FunctionTool(name="local", description="Inert local tool", func=p.body)
        p.leaf, p.leaf_attribute = tool, "func"

        async def invoke():
            context = maf.FunctionInvocationContext(tool, {"value": "value"}, kwargs={"tool_call_id": "native-call"})

            async def final(ctx):
                return await tool.invoke(arguments=ctx.arguments, context=ctx, skip_parsing=True)

            return await middleware.FunctionMiddlewarePipeline(*p.target.middleware).execute(context, final)

    elif family == "pydantic_ai":
        pydantic_ai = load_sdk("pydantic_ai")
        test_models = load_sdk("pydantic_ai.models.test")

        p.target = pydantic_ai.Agent(test_models.TestModel(), tools=[p.body])
        p.leaf, p.leaf_attribute = p.target._function_toolset.tools["body"].function_schema, "function"

        async def invoke():
            if path == "sync":
                return await asyncio.to_thread(p.target.run_sync, "Run the local tool")
            return await p.target.run("Run the local tool")

    elif family == "openai_agents":
        agents = load_sdk("agents")
        tool_context = load_sdk("agents.tool_context")
        from openai.types.responses import ResponseFunctionToolCall

        tool = agents.function_tool(p.abody, name_override="local", failure_error_function=None)
        p.target = agents.Agent(name="original", tools=[tool])

        async def invoke():
            args = '{"value":"value"}'
            context = tool_context.ToolContext.from_agent_context(
                agents.RunContextWrapper(context=None),
                "native-call",
                ResponseFunctionToolCall(name="local", arguments=args, call_id="native-call", type="function_call"),
            )
            return await tool.on_invoke_tool(context, args)

    elif family == "smolagents":
        smol = load_sdk("smolagents")
        if TYPE_CHECKING:
            from backend.tests.native_gates.sdk_types import ToolBase
        else:
            ToolBase = smol.Tool  # noqa: N806 - the real SDK base.

        class LocalTool(ToolBase):
            name = "local"
            description = "Inert local tool"
            inputs = {"value": {"type": "string", "description": "Value"}}
            output_type = "string"

            def forward(self, value: str) -> str:
                return p.body(value)

        p.target = smol.ToolCallingAgent(tools=[LocalTool()], model=smol.Model())
        p.leaf, p.leaf_attribute = p.target.tools["local"], "forward"

        async def invoke():
            return await asyncio.to_thread(p.target.execute_tool_call, "local", {"value": "value"})

    elif family == "crewai":
        crewai = load_sdk("crewai")
        parser = load_sdk("crewai.agents.parser")
        structured_tool = load_sdk("crewai.tools.structured_tool")
        i18n = load_sdk("crewai.utilities.i18n")
        execute_tool_and_check_finality = load_sdk("crewai.utilities.tool_utils").execute_tool_and_check_finality
        if TYPE_CHECKING:
            from backend.tests.native_gates.sdk_types import LLMBase
        else:
            LLMBase = crewai.BaseLLM  # noqa: N806 - the real SDK base.

        class NoInference(LLMBase):
            def call(self, *args, **kwargs):
                raise AssertionError("No model inference in tool boundary qualification")

        llm = NoInference(model="inert")
        agent = crewai.Agent(role="local", goal="test", backstory="test", llm=llm, allow_delegation=False)
        task = crewai.Task(description="test", expected_output="local output", agent=agent)
        p.target = crewai.Crew(agents=[agent], tasks=[task], memory=False, cache=False, tracing=False)
        tool = structured_tool.CrewStructuredTool.from_function(p.body, name="local", description="Inert local tool")
        p.leaf, p.leaf_attribute = tool, "func"

        async def invoke():
            action = parser.AgentAction(
                thought="local",
                tool="local",
                tool_input=json.dumps({"value": "value"}),
                text='Action: local\nAction Input: {"value": "value"}',
            )
            result = await asyncio.to_thread(
                execute_tool_and_check_finality,
                action,
                [tool],
                i18n.I18N(),
                agent=agent,
                task=task,
                crew=p.target,
                function_calling_llm=llm,
            )
            return result.result

    else:
        raise AssertionError(mode)
    p.invoke = invoke
    return p
