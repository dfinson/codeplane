"""Pinned generation requests with native automatic function calling explicitly disabled."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import encode_arguments
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError

from ._client_calls import FunctionDispatcher, LocalFunction, install_functions, install_request, plain, refuse_stream

if TYPE_CHECKING:
    from backend.services.adapters.native_gates import GateAttachment


@dataclass
class GeminiClientTarget(FunctionDispatcher):
    client: Any
    functions: tuple[LocalFunction, ...] = ()


def install(gate: GateAttachment, target: object) -> None:
    sdk = load_sdk("google.genai")
    types = load_sdk("google.genai.types")
    if not isinstance(target, GeminiClientTarget) or not isinstance(target.client, sdk.Client):
        raise TypeError("Gemini client bridge requires the original pinned Client")
    client = target.client
    options = client._api_client._http_options
    api_client = client._api_client
    resources = (client.models, client.aio.models)
    if options.retry_options is None or options.retry_options.attempts not in (0, 1):
        raise UnsupportedNativeHookError("Gemini request qualification requires retry_options.attempts=1")
    gate.claim(client)

    def fingerprint() -> str:
        return encode_arguments(
            {
                "client": id(target.client),
                "api_client": id(client._api_client),
                "base_url": options.base_url,
                "retry": plain(options.retry_options),
                "models": id(client.models),
                "async_models": id(client.aio.models),
                "resource_clients": [id(resource._api_client) for resource in resources],
                "http_client": id(options.httpx_client),
                "async_http_client": id(options.httpx_async_client),
            }
        )

    def validate(arguments: dict[str, Any]) -> None:
        if (
            target.client is not client
            or client._api_client is not api_client
            or api_client._http_options is not options
        ):
            raise UnsupportedNativeHookError("Gemini client options were replaced; detach before replacement")
        if any(resource._api_client is not api_client for resource in resources):
            raise UnsupportedNativeHookError("Gemini model resource was replaced; detach before replacement")
        config = types.GenerateContentConfig.model_validate(arguments.get("config") or {})
        if config.automatic_function_calling is None or config.automatic_function_calling.disable is not True:
            raise UnsupportedNativeHookError("Gemini automatic function calling must be explicitly disabled")
        if config.http_options is not None:
            raise UnsupportedNativeHookError("Per-request Gemini transport overrides are not qualified")
        if options.retry_options is None or options.retry_options.attempts not in (0, 1):
            raise UnsupportedNativeHookError("Gemini retries changed after attachment")

    def response(result: Any) -> dict[str, Any]:
        headers = result.sdk_http_response.headers if result.sdk_http_response is not None else None
        return {
            "response": result.model_dump(mode="json", exclude_unset=True),
            "response_id": result.response_id,
            "request_id": headers.get("x-request-id") if headers else None,
        }

    for models in resources:
        install_request(
            gate,
            models,
            "generate_content",
            "gemini.generate_content",
            fingerprint=fingerprint,
            validate=validate,
            response=response,
            usage_key=lambda result: f"response:{result.response_id}" if result.response_id else None,
        )
        gate.patch(models, "generate_content_stream", refuse_stream)
    install_functions(gate, target)
