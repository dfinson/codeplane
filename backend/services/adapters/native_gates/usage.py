"""Use actual returned native usage, never model names, prices or inferred USD."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.models.usage import UsageAmount

from ._optional import load_sdk

if TYPE_CHECKING:
    from backend.services.adapters.contracts import ExecutionIdentity


def result_usage(family: str, result: Any, execution: ExecutionIdentity) -> tuple[UsageAmount, ...]:
    values: dict[str, int] = {}
    source = ""
    if family == "crewai":
        crew_output = load_sdk("crewai.crews.crew_output")
        if isinstance(result, crew_output.CrewOutput):
            reported = result.token_usage
            for native_name, unit in (
                ("prompt_tokens", "input_tokens"),
                ("completion_tokens", "output_tokens"),
                ("cached_prompt_tokens", "cache_read_tokens"),
            ):
                amount = getattr(reported, native_name)
                if native_name in reported.model_fields_set or amount:
                    values[unit] = amount
            source = "CrewOutput.token_usage"
    elif family == "pydantic_ai":
        pydantic_run = load_sdk("pydantic_ai.run")

        if isinstance(result, pydantic_run.AgentRunResult):
            usage = result.usage()
            # These aggregate SDK dataclasses default omitted counters to zero.
            # An all-default object is not proof of reported zero usage.
            if usage.input_tokens or usage.output_tokens:
                values = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
                if usage.cache_read_tokens:
                    values["cache_read_tokens"] = usage.cache_read_tokens
                if usage.cache_write_tokens:
                    values["cache_write_tokens"] = usage.cache_write_tokens
            source = "AgentRunResult.usage"
    elif family == "openai_agents":
        agents_result = load_sdk("agents.result")

        if isinstance(result, agents_result.RunResult):
            agent_usage = result.context_wrapper.usage
            if agent_usage.input_tokens or agent_usage.output_tokens:
                values = {"input_tokens": agent_usage.input_tokens, "output_tokens": agent_usage.output_tokens}
                if agent_usage.input_tokens_details.cached_tokens:
                    values["cache_read_tokens"] = agent_usage.input_tokens_details.cached_tokens
            source = "RunResult.context_wrapper.usage"
    elif family in {"langchain", "langgraph"}:
        messages = load_sdk("langchain_core.messages")

        if isinstance(result, messages.AIMessage) and result.usage_metadata is not None:
            values = {
                "input_tokens": result.usage_metadata["input_tokens"],
                "output_tokens": result.usage_metadata["output_tokens"],
            }
            source = "AIMessage.usage_metadata"
    return tuple(
        UsageAmount.model_validate(
            {
                "amount": amount,
                "unit": unit,
                "producer": family,
                "model": None,
                "job_id": execution.job_id,
                "execution_epoch": execution.epoch,
                "role": "primary",
                "source": source,
            }
        )
        for unit, amount in values.items()
    )
