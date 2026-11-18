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
    model: str | None = None
    if family == "openai-client":
        runs = load_sdk("openai.types.beta.threads.run")
        if isinstance(result, runs.Run) and result.usage is not None:
            values = _client_tokens(
                result.usage, (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"))
            )
            source = "Run.usage"
            model = result.model
    elif family == "anthropic-client":
        messages = load_sdk("anthropic.types")
        if isinstance(result, messages.Message):
            values = _client_tokens(
                result.usage,
                (
                    ("input_tokens", "input_tokens"),
                    ("output_tokens", "output_tokens"),
                    ("cache_read_input_tokens", "cache_read_tokens"),
                    ("cache_creation_input_tokens", "cache_write_tokens"),
                ),
            )
            source = "Message.usage"
            model = result.model
    elif family == "gemini-client":
        types = load_sdk("google.genai.types")
        if isinstance(result, types.GenerateContentResponse) and result.usage_metadata is not None:
            values = _client_tokens(
                result.usage_metadata,
                (
                    ("prompt_token_count", "input_tokens"),
                    ("candidates_token_count", "output_tokens"),
                    ("cached_content_token_count", "cache_read_tokens"),
                ),
            )
            source = "GenerateContentResponse.usage_metadata"
            model = result.model_version
    elif family == "mistral-client":
        models = load_sdk("mistralai.client.models")
        if isinstance(result, models.ChatCompletionResponse):
            values = _client_tokens(
                result.usage, (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"))
            )
            source = "ChatCompletionResponse.usage"
            model = result.model
    elif family == "bedrock-client":
        if isinstance(result, dict):
            trace = result.get("trace", {}).get("trace", {})
            for phase in ("orchestrationTrace", "preProcessingTrace", "postProcessingTrace", "routingClassifierTrace"):
                output = trace.get(phase, {}).get("modelInvocationOutput", {})
                supplied = output.get("metadata", {}).get("usage")
                if supplied is not None:
                    if not isinstance(supplied, dict):
                        raise ValueError("Invalid native Bedrock usage object")
                    values = _client_tokens(
                        supplied, (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"))
                    )
                    source = f"InvokeAgent.trace.{phase}.modelInvocationOutput.metadata.usage"
                    break
    elif family == "autogen":
        models = load_sdk("autogen_core.models")
        if isinstance(result, models.CreateResult):
            values = {"input_tokens": result.usage.prompt_tokens, "output_tokens": result.usage.completion_tokens}
            source = "CreateResult.usage"
    elif family == "llamaindex":
        llms = load_sdk("llama_index.core.base.llms.types")
        if isinstance(result, (llms.CompletionResponse, llms.ChatResponse)):
            for native_name, unit in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
                amount = result.additional_kwargs.get(native_name)
                if amount is not None:
                    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                        raise ValueError("Invalid native LlamaIndex token usage")
                    values[unit] = amount
            source = f"{type(result).__name__}.additional_kwargs"
    elif family == "google-adk":
        events = load_sdk("google.adk.events")
        if isinstance(result, events.Event) and result.usage_metadata is not None:
            reported = result.usage_metadata
            for native_name, unit in (
                ("prompt_token_count", "input_tokens"),
                ("candidates_token_count", "output_tokens"),
                ("cached_content_token_count", "cache_read_tokens"),
            ):
                amount = getattr(reported, native_name)
                if amount is not None:
                    values[unit] = amount
            source = "Event.usage_metadata"
    elif family == "crewai":
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
                "model": model,
                "job_id": execution.job_id,
                "execution_epoch": execution.epoch,
                "role": "primary",
                "source": source,
            }
        )
        for unit, amount in values.items()
    )


def _client_tokens(supplied: Any, fields: tuple[tuple[str, str], ...]) -> dict[str, int]:
    values = {}
    present = supplied.keys() if isinstance(supplied, dict) else supplied.model_fields_set
    for native, unit in fields:
        if native not in present:
            continue
        amount = supplied.get(native) if isinstance(supplied, dict) else getattr(supplied, native)
        if amount is None:
            continue
        if type(amount) is not int or amount < 0:
            raise ValueError(f"Invalid native client usage: {native}")
        values[unit] = amount
    return values
