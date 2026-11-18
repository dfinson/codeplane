"""Actual OpenAI completion objects used by the independently owned Swarm bridge."""

from decimal import Decimal
from importlib.metadata import version

import pytest

from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.usage import result_usage


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("amount", [3, 0, None])
def test_swarm_supplied_completion_usage_is_typed_and_source_present(streaming, amount):
    assert version("openai") == "2.38.0"
    chat = load_sdk("openai.types.chat")
    usage = load_sdk("openai.types.completion_usage")
    response_type = chat.ChatCompletionChunk if streaming else chat.ChatCompletion
    response = response_type.model_construct(
        id="native-completion",
        model="supplied-model",
        usage=None
        if amount is None
        else usage.CompletionUsage(
            prompt_tokens=amount,
            completion_tokens=0,
            total_tokens=amount,
            prompt_tokens_details=usage.PromptTokensDetails(cached_tokens=0),
        ),
    )
    values = result_usage("swarm", response, ExecutionIdentity("job", "binding", "epoch", "token"))
    assert {item.unit: item.amount for item in values} == (
        {}
        if amount is None
        else {
            "input_tokens": Decimal(amount),
            "output_tokens": Decimal(0),
            "cache_read_tokens": Decimal(0),
        }
    )
    assert all(item.model == "supplied-model" and item.producer == "swarm" for item in values)
    assert all(item.source == f"{response_type.__name__}.usage" for item in values)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("amount", [True, -1, 0.5])
def test_swarm_invalid_native_counts_do_not_become_usage(streaming, amount):
    chat = load_sdk("openai.types.chat")
    usage = load_sdk("openai.types.completion_usage")
    response_type = chat.ChatCompletionChunk if streaming else chat.ChatCompletion
    response = response_type.model_construct(
        id="native-completion",
        model="supplied-model",
        usage=usage.CompletionUsage.model_construct(prompt_tokens=amount),
    )
    with pytest.raises(ValueError, match="Invalid native client usage"):
        result_usage("swarm", response, ExecutionIdentity("job", "binding", "epoch", "token"))


def test_swarm_sparse_native_fields_remain_unknown():
    chat = load_sdk("openai.types.chat")
    usage = load_sdk("openai.types.completion_usage")
    execution = ExecutionIdentity("job", "binding", "epoch", "token")
    missing = chat.ChatCompletion.model_construct(id="native-completion")
    assert result_usage("swarm", missing, execution) == ()
    missing.usage = usage.CompletionUsage.model_construct(
        prompt_tokens=0, prompt_tokens_details=usage.PromptTokensDetails.model_construct()
    )
    assert [(item.unit, item.amount) for item in result_usage("swarm", missing, execution)] == [
        ("input_tokens", Decimal(0))
    ]
