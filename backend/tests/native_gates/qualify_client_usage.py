"""Typed usage normalization only; actual request transports are qualified separately."""

import pytest

from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.usage import result_usage

FAMILIES = ("openai-client", "anthropic-client", "gemini-client", "mistral-client", "bedrock-client")
OWNER = ExecutionIdentity("job", "binding", "epoch", "token")


def supplied(family, values):
    if family == "openai-client":
        types = load_sdk("openai.types.beta.threads.run")
        return types.Run.model_construct(model="reported-model", usage=types.Usage.model_construct(**values))
    if family == "anthropic-client":
        types = load_sdk("anthropic.types")
        return types.Message.model_construct(model="reported-model", usage=types.Usage.model_construct(**values))
    if family == "gemini-client":
        types = load_sdk("google.genai.types")
        return types.GenerateContentResponse(
            model_version="reported-model",
            usage_metadata=types.GenerateContentResponseUsageMetadata.model_construct(**values),
        )
    if family == "mistral-client":
        types = load_sdk("mistralai.client.models")
        return types.ChatCompletionResponse.model_construct(
            model="reported-model", usage=types.UsageInfo.model_construct(**values)
        )
    return {"trace": {"trace": {"orchestrationTrace": {"modelInvocationOutput": {"metadata": {"usage": values}}}}}}


def counters(family, amount):
    if family in {"openai-client", "mistral-client"}:
        return {"prompt_tokens": amount, "completion_tokens": amount}
    if family == "anthropic-client":
        return {
            "input_tokens": amount,
            "output_tokens": amount,
            "cache_read_input_tokens": amount,
            "cache_creation_input_tokens": amount,
        }
    if family == "gemini-client":
        return {"prompt_token_count": amount, "candidates_token_count": amount, "cached_content_token_count": amount}
    return {"inputTokens": amount, "outputTokens": amount}


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("amount", [0, 7])
def test_actual_client_usage_preserves_reported_zero_positive_and_provenance(family, amount):
    usage = result_usage(family, supplied(family, counters(family, amount)), OWNER)
    units = {"input_tokens", "output_tokens"}
    if family in {"anthropic-client", "gemini-client"}:
        units.add("cache_read_tokens")
    if family == "anthropic-client":
        units.add("cache_write_tokens")
    assert {item.unit for item in usage} == units
    assert all(item.amount == amount and item.producer == family and item.role == "primary" for item in usage)
    assert all(item.job_id == "job" and item.execution_epoch == "epoch" and item.source for item in usage)
    assert all(not item.verified and item.currency is None and item.unit != "USD" for item in usage)
    assert all(item.model == (None if family == "bedrock-client" else "reported-model") for item in usage)


@pytest.mark.parametrize("family", FAMILIES)
def test_missing_native_counters_do_not_mint_zero_usage(family):
    assert result_usage(family, supplied(family, {}), OWNER) == ()


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("amount", [True, -1, 1.5])
def test_invalid_native_counters_fail_explicitly(family, amount):
    with pytest.raises(ValueError, match="native client usage"):
        result_usage(family, supplied(family, counters(family, amount)), OWNER)
