"""Pinned OpenAI thread/run requests and explicitly supplied local functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import encode_arguments
from backend.services.adapters.native_gates.controls import StopEvidence
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
    from backend.services.adapters.native_gates.controls import ControlPermit, NativeInput


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
    original_cancel = resources[2].cancel
    observed_runs: dict[tuple[str, str], str] = {}
    stop_attempts: dict[tuple[str, str], StopEvidence] = {}
    terminal = {"cancelled", "completed", "failed", "expired", "incomplete"}

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

    def identity(result: Any) -> dict[str, Any]:
        return {
            "request_id": getattr(result, "_request_id", None),
            "response_id": getattr(result, "id", None),
            "run_id": result.id if isinstance(result, runs.Run) else getattr(result, "run_id", None),
            "thread_id": getattr(result, "thread_id", None),
        }

    def response(result: Any) -> dict[str, Any]:
        return {"response": result.model_dump(mode="json", exclude_unset=True), **identity(result)}

    def usage_key(result: Any) -> str | None:
        return (
            f"run:{result.id}"
            if (isinstance(result, runs.Run) and result.usage is not None and isinstance(result.id, str) and result.id)
            else None
        )

    installed_client = fingerprint()

    async def stop(permit: ControlPermit) -> StopEvidence:
        results = []
        for key, status in tuple(observed_runs.items()):
            thread_id, run_id = key
            native = {"thread_id": thread_id, "run_id": run_id, "status": status}
            if key in stop_attempts:
                results.append(stop_attempts[key])
                continue
            if status in terminal or status == "cancelling":
                results.append(
                    StopEvidence(
                        "acknowledged" if status in terminal else "cancel-requested",
                        (native,),
                        "openai.observed-run-status",
                    )
                )
                continue
            await gate.binding.require_control(permit)
            validate({})
            if fingerprint() != installed_client:
                raise UnsupportedNativeHookError("OpenAI stop client changed after attachment")
            stop_attempts[key] = StopEvidence("delivery-unknown", (native,), "openai.threads.runs.cancel")
            try:
                value = await call_native(original_cancel, run_id, thread_id=thread_id)
            except Exception as exc:
                evidence = StopEvidence("delivery-unknown", (native,), "openai.threads.runs.cancel", type(exc).__name__)
            else:
                local_errors = []
                supplied = identity(value)
                native = {name: item for name, item in supplied.items() if isinstance(item, str) and item}
                native["status"] = value.status
                matches = value.id == run_id and value.thread_id == thread_id
                if matches:
                    observed_runs[key] = value.status
                disposition: Literal["acknowledged", "cancel-requested", "delivery-unknown"] = (
                    "acknowledged"
                    if matches and value.status in terminal
                    else "cancel-requested"
                    if matches and value.status == "cancelling"
                    else "delivery-unknown"
                )
                try:
                    await gate.binding.supplied_usage(usage_key(value) or permit.operation_id, value)
                except Exception as exc:
                    local_errors.append(type(exc).__name__)
                try:
                    gate.binding.observe(
                        {
                            "type": "response",
                            "operation_id": permit.operation_id,
                            "operation": "openai.threads.runs.cancel",
                            **response(value),
                            "local_cancel_requested": True,
                        }
                    )
                except Exception as exc:
                    local_errors.append(type(exc).__name__)
                evidence = StopEvidence(
                    disposition,
                    (native,),
                    "openai.threads.runs.cancel",
                    ",".join(local_errors) if local_errors else None,
                )
            stop_attempts[key] = evidence
            results.append(evidence)
        combined: Literal["acknowledged", "cancel-requested", "delivery-unknown"] = (
            "delivery-unknown"
            if any(item.status == "delivery-unknown" for item in results)
            else "acknowledged"
            if results and all(item.status == "acknowledged" for item in results)
            else "cancel-requested"
        )
        return StopEvidence(
            combined,
            tuple(native for item in results for native in item.native_ids),
            "openai.threads.runs.cancel"
            if stop_attempts
            else "openai.observed-run-status"
            if results
            else "openai.local-stop-no-observed-run",
            ",".join(item.local_error for item in results if item.local_error) or None,
        )

    async def returned(value: Any) -> None:
        if not isinstance(value, runs.Run):
            return
        if not isinstance(value.thread_id, str) or not value.thread_id or not isinstance(value.id, str) or not value.id:
            raise ValueError("An observed OpenAI run requires actual thread and run identities")
        key = (value.thread_id, value.id)
        observed_runs[key] = value.status
        while (
            gate.binding.control_stop_requested()
            and key not in stop_attempts
            and observed_runs.get(key) not in terminal | {"cancelling", None}
        ):
            await gate.binding.controls.stop(refresh=True)

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
            identity=identity,
            usage_key=usage_key,
            returned=returned,
        )
    for method in ("stream", "submit_tool_outputs_stream"):
        gate.patch(resources[2], method, refuse_stream)
    install_functions(gate, target)

    async def next_request(message: NativeInput, parameters: dict[str, Any]) -> Any:
        thread_id = parameters.pop("thread_id")
        await call_native(resources[1].create, thread_id, role="user", content=message.content)
        return await call_native(resources[2].create, thread_id, **parameters)

    install_turn_boundary(gate, target, request=next_request, stop=stop)
