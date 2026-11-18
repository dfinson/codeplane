"""Real pinned SDK producers and inert local bodies. No fake hook contexts."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Producer:
    target: Any = None
    invoke: Any = None
    calls: list = field(default_factory=list)

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
        from langchain_core.tools import StructuredTool

        tool = StructuredTool.from_function(p.body, coroutine=p.abody if path != "fallback" else None, name="local")
        p.target = tool
        args = {"type": "tool_call", "name": "local", "args": {"value": "value"}, "id": "native-call"}

        async def invoke():
            if path == "sync":
                return await asyncio.to_thread(tool.invoke, args)
            return await tool.ainvoke(args)

    elif family == "langgraph":
        from langchain_core.messages import AIMessage
        from langchain_core.tools import StructuredTool
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode

        tool = StructuredTool.from_function(p.body, coroutine=p.abody, name="local")
        p.target = ToolNode([tool])
        graph = StateGraph(MessagesState)
        graph.add_node("tools", p.target)
        graph.add_edge(START, "tools")
        graph.add_edge("tools", END)
        runnable = graph.compile()

        async def invoke():
            args = {
                "messages": [AIMessage(content="", tool_calls=[{"name": "local", "args": {}, "id": "native-call"}])]
            }
            result = await asyncio.to_thread(runnable.invoke, args) if path == "sync" else await runnable.ainvoke(args)
            return result["messages"][-1]

    elif family == "semantic_kernel":
        from semantic_kernel import Kernel
        from semantic_kernel.contents import ChatHistory, FunctionCallContent
        from semantic_kernel.functions import kernel_function

        p.target = Kernel()

        @kernel_function(name="local", description="Inert local tool")
        def local(value: str = "value") -> str:
            return p.body(value)

        p.target.add_function(plugin_name="test", function=local)

        async def invoke():
            history = ChatHistory()
            await p.target.invoke_function_call(
                FunctionCallContent(id="native-call", name="test-local", arguments='{"value":"value"}'), history
            )
            return history.messages[-1].items[0].result

    elif family == "maf":
        from agent_framework import FunctionInvocationContext, FunctionTool
        from agent_framework._middleware import FunctionMiddlewarePipeline

        from backend.services.adapters.native_gates.maf import MiddlewareSlot

        p.target = MiddlewareSlot([])
        tool = FunctionTool(name="local", description="Inert local tool", func=p.body)

        async def invoke():
            context = FunctionInvocationContext(tool, {"value": "value"}, kwargs={"tool_call_id": "native-call"})

            async def final(ctx):
                return await tool.invoke(arguments=ctx.arguments, context=ctx, skip_parsing=True)

            return await FunctionMiddlewarePipeline(*p.target.middleware).execute(context, final)

    elif family == "pydantic_ai":
        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel

        p.target = Agent(TestModel(), tools=[p.body])

        async def invoke():
            if path == "sync":
                return await asyncio.to_thread(p.target.run_sync, "Run the local tool")
            return await p.target.run("Run the local tool")

    elif family == "openai_agents":
        from agents import Agent, RunContextWrapper, function_tool
        from agents.tool_context import ToolContext
        from openai.types.responses import ResponseFunctionToolCall

        tool = function_tool(p.abody, name_override="local", failure_error_function=None)
        p.target = Agent(name="original", tools=[tool])

        async def invoke():
            args = '{"value":"value"}'
            context = ToolContext.from_agent_context(
                RunContextWrapper(context=None),
                "native-call",
                ResponseFunctionToolCall(name="local", arguments=args, call_id="native-call", type="function_call"),
            )
            return await tool.on_invoke_tool(context, args)

    elif family == "smolagents":
        from smolagents import Model, Tool, ToolCallingAgent

        class LocalTool(Tool):
            name = "local"
            description = "Inert local tool"
            inputs = {"value": {"type": "string", "description": "Value"}}
            output_type = "string"

            def forward(self, value: str) -> str:
                return p.body(value)

        p.target = ToolCallingAgent(tools=[LocalTool()], model=Model())

        async def invoke():
            return await asyncio.to_thread(p.target.execute_tool_call, "local", {"value": "value"})

    elif family == "crewai":
        from crewai import Agent, BaseLLM, Crew, Task
        from crewai.agents.parser import AgentAction
        from crewai.tools.structured_tool import CrewStructuredTool
        from crewai.utilities.i18n import I18N
        from crewai.utilities.tool_utils import execute_tool_and_check_finality

        class NoInference(BaseLLM):
            def call(self, *args, **kwargs):
                raise AssertionError("No model inference in tool boundary qualification")

        llm = NoInference(model="inert")
        agent = Agent(role="local", goal="test", backstory="test", llm=llm, allow_delegation=False)
        task = Task(description="test", expected_output="local output", agent=agent)
        p.target = Crew(agents=[agent], tasks=[task], memory=False, cache=False, tracing=False)
        tool = CrewStructuredTool.from_function(p.body, name="local", description="Inert local tool")

        async def invoke():
            action = AgentAction(
                thought="local",
                tool="local",
                tool_input=json.dumps({"value": "value"}),
                text='Action: local\nAction Input: {"value": "value"}',
            )
            result = await asyncio.to_thread(
                execute_tool_and_check_finality,
                action,
                [tool],
                I18N(),
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
