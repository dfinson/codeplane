"""Use actual returned native usage, never model names, prices or inferred USD."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.models.usage import UsageAmount

if TYPE_CHECKING:
    from backend.services.adapters.contracts import ExecutionIdentity


def result_usage(family: str, result: Any, execution: ExecutionIdentity) -> tuple[UsageAmount, ...]:
    values: dict[str, int] = {}
    source = ""
    if family == "pydantic_ai":
        from pydantic_ai.run import AgentRunResult

        if isinstance(result, AgentRunResult):
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
        from agents.result import RunResult

        if isinstance(result, RunResult):
            agent_usage = result.context_wrapper.usage
            if agent_usage.input_tokens or agent_usage.output_tokens:
                values = {"input_tokens": agent_usage.input_tokens, "output_tokens": agent_usage.output_tokens}
                if agent_usage.input_tokens_details.cached_tokens:
                    values["cache_read_tokens"] = agent_usage.input_tokens_details.cached_tokens
            source = "RunResult.context_wrapper.usage"
    elif family in {"langchain", "langgraph"}:
        from langchain_core.messages import AIMessage

        if isinstance(result, AIMessage) and result.usage_metadata is not None:
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
