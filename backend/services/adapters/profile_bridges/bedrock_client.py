"""Pinned Bedrock Agent Runtime requests, actual event streams and delegated local functions."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import encode_arguments
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.ingest.mapping import canonical_record

from ._client_calls import (
    FunctionDispatcher,
    LocalFunction,
    call_native,
    install_functions,
    install_request,
    install_turn_boundary,
)

if TYPE_CHECKING:
    from backend.services.adapters.native_gates import GateAttachment
    from backend.services.adapters.native_gates.controls import NativeInput


@dataclass
class BedrockClientTarget(FunctionDispatcher):
    client: Any
    functions: tuple[LocalFunction, ...] = ()


def _wire_json(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {key: _wire_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def install(gate: GateAttachment, target: object) -> None:
    sdk = load_sdk("botocore.client")
    streams = load_sdk("botocore.eventstream")
    if not isinstance(target, BedrockClientTarget) or not isinstance(target.client, sdk.BaseClient):
        raise TypeError("Bedrock bridge requires the original pinned botocore client")
    client = target.client
    if client.meta.service_model.service_name != "bedrock-agent-runtime":
        raise TypeError("Bedrock bridge qualifies only the Agent Runtime invoke_agent operation")
    if client.meta.config.retries.get("total_max_attempts") != 1:
        raise UnsupportedNativeHookError("Bedrock request qualification requires one total attempt")
    gate.claim(client)

    def fingerprint() -> str:
        return encode_arguments(
            {
                "client": id(target.client),
                "endpoint": client.meta.endpoint_url,
                "service": client.meta.service_model.service_name,
                "retry": client.meta.config.retries,
                "transport": id(client._endpoint.http_session),
                "endpoint_object": id(client._endpoint),
            }
        )

    def validate(arguments: dict[str, Any]) -> None:
        if target.client is not client:
            raise UnsupportedNativeHookError("Bedrock client was replaced; detach before replacement")
        if client.meta.config.retries.get("total_max_attempts") != 1:
            raise UnsupportedNativeHookError("Bedrock retries changed after attachment")

    def identity(result: Any) -> dict[str, Any]:
        return {
            "request_id": result.get("ResponseMetadata", {}).get("RequestId"),
            "session_id": result.get("sessionId"),
        }

    def response(result: Any) -> dict[str, Any]:
        if not isinstance(result, dict) or not isinstance(result.get("completion"), streams.EventStream):
            raise ValueError("Bedrock did not return its actual SDK EventStream")
        request_id = result.get("ResponseMetadata", {}).get("RequestId")
        session_id = result.get("sessionId")
        stream = result["completion"]
        consumed = []
        total_bytes = 0
        try:
            for index, event in enumerate(stream):
                if index >= 1000:
                    raise ValueError("Bedrock stream exceeds 1000 native events")
                trace = event.get("trace", {}).get("trace", {})
                for phase in (
                    "orchestrationTrace",
                    "preProcessingTrace",
                    "postProcessingTrace",
                    "routingClassifierTrace",
                ):
                    output = trace.get(phase, {}).get("modelInvocationOutput")
                    if output is not None and output.get("metadata", {}).get("usage") is not None:
                        trace_id = output.get("traceId")
                        if not request_id or not isinstance(trace_id, str) or not trace_id:
                            raise ValueError("Bedrock usage needs actual request and model trace identities")
                        asyncio.run_coroutine_threadsafe(
                            gate.binding.supplied_usage(f"{request_id}:{phase}:{trace_id}", event), gate.binding.loop
                        ).result()
                retained = _wire_json(event)
                total_bytes += len(canonical_record(retained))
                if total_bytes > 1024 * 1024:
                    raise ValueError("Bedrock stream exceeds 1 MiB")
                consumed.append(event)
                gate.binding.observe(
                    {
                        "type": "stream_event",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "request_id": request_id,
                        "session_id": session_id,
                        "event": retained,
                    }
                )
                if not gate.active:
                    raise asyncio.CancelledError
        finally:
            stream.close()
        # Return the original response with a retained finite replay of the consumed stream.
        result["completion"] = tuple(consumed)
        return {"response": {"events": _wire_json(consumed)}, "request_id": request_id, "session_id": session_id}

    install_request(
        gate,
        client,
        "invoke_agent",
        "bedrock.invoke_agent",
        fingerprint=fingerprint,
        validate=validate,
        response=response,
        identity=identity,
        usage_key=lambda _: None,
        streaming_response=True,
    )
    install_functions(gate, target)

    async def next_request(message: NativeInput, parameters: dict[str, Any]) -> Any:
        return await call_native(client.invoke_agent, **{**parameters, "inputText": message.content})

    install_turn_boundary(gate, target, request=next_request)
