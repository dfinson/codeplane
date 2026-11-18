"""Pinned OpenAI thread/run requests and explicitly supplied local functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import encode_arguments
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError

from ._client_calls import FunctionDispatcher, LocalFunction, install_functions, install_request, refuse_stream

if TYPE_CHECKING:
    from backend.services.adapters.native_gates import GateAttachment


@dataclass
class OpenAIClientTarget(FunctionDispatcher):
    client: Any
    functions: tuple[LocalFunction, ...] = ()


def install(gate: GateAttachment, target: object) -> None:
    sdk = load_sdk("openai")
    runs = load_sdk("openai.types.beta.threads.run")
    if not isinstance(target, OpenAIClientTarget) or not isinstance(target.client, (sdk.OpenAI, sdk.AsyncOpenAI)):
        raise TypeError("OpenAI client bridge requires the original pinned OpenAI client")
    client = target.client
    if client.max_retries != 0:
        raise UnsupportedNativeHookError("OpenAI request qualification requires max_retries=0")
    gate.claim(client)
    threads = client.beta.threads
    resources = (threads, threads.messages, threads.runs)

    def fingerprint() -> str:
        return encode_arguments(
            {
                "client": id(target.client),
                "base_url": str(client.base_url),
                "max_retries": client.max_retries,
                "http_client": id(client._client),
                "transport": id(client._client._transport),
                "resources": [id(client.beta.threads), id(client.beta.threads.messages), id(client.beta.threads.runs)],
                "resource_clients": [id(resource._client) for resource in resources],
            }
        )

    def validate(arguments: dict[str, Any]) -> None:
        if target.client is not client:
            raise UnsupportedNativeHookError("OpenAI client was replaced; detach before replacement")
        if any(resource._client is not client for resource in resources) or (
            client.beta.threads is not resources[0]
            or threads.messages is not resources[1]
            or threads.runs is not resources[2]
        ):
            raise UnsupportedNativeHookError("OpenAI resource client was replaced; detach before replacement")
        if arguments.get("stream") or any(key in arguments for key in ("extra_headers", "extra_body", "extra_query")):
            raise UnsupportedNativeHookError("OpenAI streaming and per-request transport overrides are not qualified")
        if client.max_retries != 0:
            raise UnsupportedNativeHookError("OpenAI retries changed after attachment")

    def response(result: Any) -> dict[str, Any]:
        return {
            "response": result.model_dump(mode="json", exclude_unset=True),
            "request_id": getattr(result, "_request_id", None),
            "response_id": getattr(result, "id", None),
            "run_id": result.id if isinstance(result, runs.Run) else getattr(result, "run_id", None),
        }

    def usage_key(result: Any) -> str | None:
        return f"run:{result.id}" if isinstance(result, runs.Run) and result.usage is not None else None

    for resource, method, operation in (
        (resources[0], "create", "threads.create"),
        (resources[1], "create", "threads.messages.create"),
        (resources[2], "create", "threads.runs.create"),
        (resources[2], "retrieve", "threads.runs.retrieve"),
        (resources[2], "submit_tool_outputs", "threads.runs.submit_tool_outputs"),
        (resources[2], "cancel", "threads.runs.cancel"),
    ):
        install_request(
            gate,
            resource,
            method,
            f"openai.{operation}",
            fingerprint=fingerprint,
            validate=validate,
            response=response,
            usage_key=usage_key,
        )
    for method in ("stream", "submit_tool_outputs_stream"):
        gate.patch(resources[2], method, refuse_stream)
    install_functions(gate, target)
