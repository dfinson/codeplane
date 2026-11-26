"""Pinned Mistral chat completion requests and explicit caller-owned tool dispatch."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import encode_arguments
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError

from ._client_calls import (
    FunctionDispatcher,
    LocalFunction,
    call_native,
    install_functions,
    install_request,
    install_turn_boundary,
    refuse_stream,
)

if TYPE_CHECKING:
    from backend.services.adapters.native_gates import GateAttachment
    from backend.services.adapters.native_gates.controls import NativeInput


@dataclass
class MistralClientTarget(FunctionDispatcher):
    client: Any
    functions: tuple[LocalFunction, ...] = ()


def install(gate: GateAttachment, target: object) -> None:
    sdk = load_sdk("mistralai.client")
    if not isinstance(target, MistralClientTarget) or not isinstance(target.client, sdk.Mistral):
        raise TypeError("Mistral client bridge requires the original pinned Mistral client")
    client = target.client
    config = client.sdk_configuration
    chat = client.chat
    if config.retry_config is not None:
        raise UnsupportedNativeHookError("Mistral request qualification requires retry_config=None")
    gate.claim(client)
    request_id: ContextVar[str | None] = ContextVar("mistral_request_id", default=None)

    def observe_transport(transport: Any, asynchronous: bool) -> None:
        if transport is None:
            return
        gate.claim(transport)
        original = transport.send

        @wraps(original)
        async def send_async(*args: Any, **kwargs: Any) -> Any:
            returned = await original(*args, **kwargs)
            request_id.set(returned.headers.get("x-request-id"))
            return returned

        @wraps(original)
        def send_sync(*args: Any, **kwargs: Any) -> Any:
            returned = original(*args, **kwargs)
            request_id.set(returned.headers.get("x-request-id"))
            return returned

        gate.patch(transport, "send", send_async if asynchronous else send_sync)

    observe_transport(config.client, False)
    observe_transport(config.async_client, True)

    def fingerprint() -> str:
        return encode_arguments(
            {
                "client": id(target.client),
                "configuration": id(client.sdk_configuration),
                "server_url": config.server_url,
                "retry_config": repr(config.retry_config),
                "chat": id(client.chat),
                "chat_config": id(chat.sdk_configuration),
                "transport": id(config.client),
                "async_transport": id(config.async_client),
            }
        )

    def validate(arguments: dict[str, Any]) -> None:
        if target.client is not client or client.sdk_configuration is not config:
            raise UnsupportedNativeHookError("Mistral client configuration was replaced; detach before replacement")
        if client.chat is not chat or chat.sdk_configuration is not config:
            raise UnsupportedNativeHookError("Mistral chat resource was replaced; detach before replacement")
        if any(key in arguments for key in ("retries", "http_headers", "server_url", "stream")):
            raise UnsupportedNativeHookError("Mistral per-request transport/retry/stream overrides are not qualified")
        if config.retry_config is not None:
            raise UnsupportedNativeHookError("Mistral retries changed after attachment")

    def identity(result: Any) -> dict[str, Any]:
        return {
            "response_id": result.id,
            "request_id": request_id.get(),
        }

    def response(result: Any) -> dict[str, Any]:
        return {"response": result.model_dump(mode="json", exclude_unset=True), **identity(result)}

    for method in ("complete", "complete_async"):
        install_request(
            gate,
            client.chat,
            method,
            f"mistral.chat.{method}",
            fingerprint=fingerprint,
            validate=validate,
            response=response,
            identity=identity,
            usage_key=lambda result: f"completion:{result.id}" if isinstance(result.id, str) and result.id else None,
        )
    for method in ("stream", "stream_async"):
        gate.patch(client.chat, method, refuse_stream)
    install_functions(gate, target)

    async def next_request(message: NativeInput, parameters: dict[str, Any]) -> Any:
        messages = list(parameters.get("messages", ()))
        return await call_native(
            client.chat.complete_async,
            **{
                **parameters,
                "messages": [*messages, {"role": "user", "content": message.content}],
            },
        )

    install_turn_boundary(gate, target, request=next_request)
