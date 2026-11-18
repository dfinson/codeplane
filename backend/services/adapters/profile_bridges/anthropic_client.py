"""Pinned Messages requests; hosted tools remain provider-owned."""

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
class AnthropicClientTarget(FunctionDispatcher):
    client: Any
    functions: tuple[LocalFunction, ...] = ()


def install(gate: GateAttachment, target: object) -> None:
    sdk = load_sdk("anthropic")
    if not isinstance(target, AnthropicClientTarget) or not isinstance(
        target.client, (sdk.Anthropic, sdk.AsyncAnthropic)
    ):
        raise TypeError("Anthropic client bridge requires the original pinned Messages client")
    client = target.client
    if client.max_retries != 0:
        raise UnsupportedNativeHookError("Anthropic request qualification requires max_retries=0")
    gate.claim(client)
    messages = client.messages

    def fingerprint() -> str:
        return encode_arguments(
            {
                "client": id(target.client),
                "base_url": str(client.base_url),
                "max_retries": client.max_retries,
                "messages": id(client.messages),
                "http_client": id(client._client),
                "transport": id(client._client._transport),
                "resource_client": id(messages._client),
            }
        )

    def validate(arguments: dict[str, Any]) -> None:
        if target.client is not client:
            raise UnsupportedNativeHookError("Anthropic client was replaced; detach before replacement")
        if client.messages is not messages or messages._client is not client:
            raise UnsupportedNativeHookError("Anthropic message resource was replaced; detach before replacement")
        if arguments.get("stream") or any(key in arguments for key in ("extra_headers", "extra_body", "extra_query")):
            raise UnsupportedNativeHookError(
                "Anthropic streaming and per-request transport overrides are not qualified"
            )
        if client.max_retries != 0:
            raise UnsupportedNativeHookError("Anthropic retries changed after attachment")

    def response(result: Any) -> dict[str, Any]:
        return {
            "response": result.model_dump(mode="json", exclude_unset=True),
            "request_id": getattr(result, "_request_id", None),
            "response_id": result.id,
        }

    install_request(
        gate,
        client.messages,
        "create",
        "anthropic.messages.create",
        fingerprint=fingerprint,
        validate=validate,
        response=response,
        usage_key=lambda result: f"message:{result.id}",
        async_method=isinstance(client, sdk.AsyncAnthropic),
    )
    gate.patch(client.messages, "stream", refuse_stream)
    install_functions(gate, target)
