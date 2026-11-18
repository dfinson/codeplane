"""A2A 0.3 JSON-RPC requests on the existing native execution boundary.

Wire shapes were checked against a2aproject/A2A at
8d57eba286de756176892518a8fc39b0ac2ccefb (v0.3.0). Upstream create_task
constructs only a local object and is deliberately not used for admission.
"""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import io
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from agentmesh.integrations.a2a import A2AAgentCard, A2ATrustProvider

from backend.services.adapters.ai_card import verify_card
from backend.services.adapters.native_gates.binding import NativeCall, NativeGateDeniedError
from backend.services.events.export_binding import credential_headers
from backend.services.ingest.mapping import canonical_record
from backend.services.ingest.otel_decoder import decode_json_body

if TYPE_CHECKING:
    from backend.services.adapters.native_gates import GateAttachment

_STATES = frozenset(
    {
        "submitted",
        "working",
        "input-required",
        "completed",
        "canceled",
        "failed",
        "rejected",
        "auth-required",
        "unknown",
    }
)


class A2ADeliveryUnknownError(OSError):
    outcome = "delivery_unknown"


class A2ARequestRefusedError(OSError):
    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"A2A peer refused the request ({code})")


@dataclass
class A2ATarget:
    peer: A2AAgentCard
    trust: A2ATrustProvider
    project_id: str
    expected_did: str
    public_key: str
    endpoint: str
    headers: dict[str, str] = field(default_factory=dict)

    async def send_message(self, message: dict[str, Any], *, stream: bool = False) -> tuple[dict[str, Any], ...]:
        raise PermissionError("A2A target must be attached to a live Runtime execution")

    async def get_task(self, task_id: str) -> dict[str, Any]:
        raise PermissionError("A2A target must be attached to a live Runtime execution")

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        raise PermissionError("A2A target must be attached to a live Runtime execution")


def _identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"A2A requires an explicit bounded {field_name}")
    return value


def _message(value: dict[str, Any]) -> dict[str, Any]:
    copied = decode_json_body(canonical_record(value))
    if not isinstance(copied, dict) or copied.get("kind") != "message" or copied.get("role") != "user":
        raise ValueError("A2A client input must be a native user message")
    _identity(copied.get("messageId"), "messageId")
    if "contextId" in copied:
        _identity(copied["contextId"], "contextId")
    parts = copied.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > 128:
        raise ValueError("A2A message requires bounded content parts")
    for part in parts:
        if (
            not isinstance(part, dict)
            or not isinstance(part.get("kind"), str)
            or part["kind"] not in {"text", "data", "file"}
        ):
            raise ValueError("Unsupported A2A message part")
        kind = part["kind"]
        expected = str if kind == "text" else dict
        if not isinstance(part.get(kind), expected):
            raise ValueError("Malformed A2A message part")
        if kind == "file":
            file = part["file"]
            supplied = [name for name in ("bytes", "uri") if name in file]
            if len(supplied) != 1 or not isinstance(file[supplied[0]], str):
                raise ValueError("A2A file part must select bytes or uri")
    return copied


def install(gate: GateAttachment, target: object) -> None:
    if (
        not isinstance(target, A2ATarget)
        or not isinstance(target.peer, A2AAgentCard)
        or not isinstance(target.trust, A2ATrustProvider)
    ):
        raise TypeError("A2A requires actual upstream card/trust objects in an A2ATarget")
    if target.peer.ai_card is None or target.trust.trust_bridge is not None:
        raise ValueError("A2A requires a signed card and a local, non-network trust verifier")
    peer = deepcopy(target.peer)
    endpoint, headers = target.endpoint, credential_headers(json.dumps(target.headers))
    if peer.url != endpoint:
        raise ValueError("A2A card endpoint differs from the configured peer")
    tasks: dict[str, tuple[str, str]] = {}
    uncertain: set[str | None] = set()

    def verify() -> None:
        assert peer.ai_card is not None
        verify_card(
            peer.ai_card.model_dump_json().encode(),
            binding_id=gate.binding.execution.binding_id.replace(":", "-"),
            project_id=target.project_id,
            expected_did=target.expected_did,
            public_key=target.public_key,
            services=(("a2a", endpoint),),
        )

    def fingerprint() -> str:
        return hashlib.sha256(
            canonical_record(
                {
                    "endpoint": target.endpoint,
                    "peer": target.peer.to_dict(),
                    "identity": target.expected_did,
                    "key": target.public_key,
                    "headers": target.headers,
                    "minimum_trust": target.trust.min_trust_score,
                    "trust_object": id(target.trust),
                    "trust_bridge": id(target.trust.trust_bridge),
                }
            )
        ).hexdigest()

    verify()

    def accept(result: object, expected_task: str | None, *, allow_message: bool) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ValueError("A2A result must be an object")
        kind = result.get("kind")
        if kind == "message":
            if not allow_message:
                raise ValueError("A2A task request returned a message instead of the task")
            _identity(result.get("messageId"), "messageId")
            if result.get("role") != "agent" or not isinstance(result.get("parts"), list):
                raise ValueError("Invalid A2A agent message")
            if expected_task is not None and result.get("taskId") != expected_task:
                raise ValueError("A2A message belongs to another task")
            return result
        if not isinstance(kind, str) or kind not in {"task", "status-update", "artifact-update"}:
            raise ValueError("Unsupported A2A result kind")
        task_id = _identity(result.get("id" if kind == "task" else "taskId"), "taskId")
        context_id = _identity(result.get("contextId"), "contextId")
        if expected_task is not None and task_id != expected_task:
            raise ValueError("A2A response belongs to another task")
        previous = tasks.get(task_id)
        if previous is not None and previous[0] != context_id:
            raise ValueError("A2A task context changed")
        if kind == "artifact-update":
            if previous is None or not isinstance(result.get("artifact"), dict):
                raise ValueError("A2A artifact has no admitted task")
            _identity(result["artifact"].get("artifactId"), "artifactId")
            return result
        status = result.get("status")
        if not isinstance(status, dict) or not isinstance(status.get("state"), str) or status["state"] not in _STATES:
            raise ValueError("A2A response has no recognized task state")
        if kind == "status-update" and not isinstance(result.get("final"), bool):
            raise ValueError("A2A status update requires its final marker")
        tasks[task_id] = (context_id, status["state"])
        return result

    async def request(method: str, params: dict[str, Any], *, stream: bool = False) -> tuple[dict[str, Any], ...]:
        verify()
        task_id = params.get("id") or params.get("message", {}).get("taskId")
        call = NativeCall(
            f"a2a.{method}",
            {"endpoint": endpoint, "method": method, "params": params},
            definition="A2A 0.3 remote request. Internal tools remain externally governed.",
            fingerprint=fingerprint,
        )
        if not await gate.allow(call):
            raise NativeGateDeniedError("A2A request denied before transport")
        verify()
        if not await target.trust.verify_peer(target.expected_did, peer):
            await gate.binding.report(call, False, False)
            raise NativeGateDeniedError("A2A peer trust verification failed")
        rpc_id = uuid.uuid4().hex
        envelope = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
        body = canonical_record(envelope)
        decode_json_body(body)
        results: list[dict[str, Any]] = []
        correlated_task = task_id
        started: bool | None = None
        success: bool | None = None
        outcome = "delivery_unknown"
        gate.binding.observe(
            {
                "type": "request_started",
                "timestamp": datetime.now(UTC).isoformat(),
                "operation_id": call.operation_id,
                "rpc_id": rpc_id,
                "method": method,
                "params": params,
            }
        )

        def result_from(raw: bytes) -> dict[str, Any]:
            nonlocal correlated_task
            document = decode_json_body(raw)
            if not isinstance(document, dict) or document.get("jsonrpc") != "2.0" or document.get("id") != rpc_id:
                raise ValueError("A2A response has an uncorrelated JSON-RPC identity")
            if "error" in document:
                error = document["error"]
                if (
                    "result" in document
                    or not isinstance(error, dict)
                    or not isinstance(error.get("code"), int)
                    or isinstance(error["code"], bool)
                ):
                    raise ValueError("Malformed A2A protocol error")
                gate.binding.observe(
                    {
                        "type": "request_error",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "operation_id": call.operation_id,
                        "rpc_id": rpc_id,
                        "method": method,
                        "error": error,
                    }
                )
                raise A2ARequestRefusedError(error["code"])
            if len(results) >= 1000:
                raise ValueError("A2A stream exceeds 1000 updates")
            result = accept(document.get("result"), correlated_task, allow_message=method.startswith("message/"))
            if result.get("kind") in {"task", "status-update", "artifact-update"}:
                correlated_task = result.get("id" if result["kind"] == "task" else "taskId")
            gate.binding.observe(
                {
                    "type": "result",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "operation_id": call.operation_id,
                    "rpc_id": rpc_id,
                    "method": method,
                    "result": result,
                }
            )
            results.append(result)
            return result

        async def exchange() -> None:
            async with (
                httpx.AsyncClient(follow_redirects=False, trust_env=False) as client,
                asyncio.timeout(30),
                client.stream(
                    "POST",
                    endpoint,
                    content=body,
                    headers={
                        **headers,
                        "content-type": "application/json",
                        "accept": "text/event-stream" if stream else "application/json",
                        "accept-encoding": "identity",
                    },
                    timeout=30,
                ) as response,
            ):
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("A2A bridge requires bounded uncompressed responses")
                size = 0
                if not stream:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 1024 * 1024:
                            raise ValueError("A2A response exceeds 1 MiB")
                        content.extend(chunk)
                    result_from(bytes(content))
                    return
                if response.headers.get("content-type", "").split(";", 1)[0] != "text/event-stream":
                    raise ValueError("A2A streaming response is not SSE")
                decoder = io.IncrementalNewlineDecoder(
                    codecs.getincrementaldecoder("utf-8-sig")("strict"), translate=True
                )
                pending = ""
                data: list[str] = []
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise ValueError("A2A stream exceeds 1 MiB")
                    pending += decoder.decode(chunk)
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        line = line.rstrip("\r")
                        if not line and data:
                            result = result_from("\n".join(data).encode())
                            data = []
                            if result.get("final") is True or (
                                result.get("kind") == "task"
                                and result["status"]["state"] in {"completed", "canceled", "failed", "rejected"}
                            ):
                                return
                        elif line.startswith("data:"):
                            data.append(line[5:].lstrip(" "))
                decoder.decode(b"", final=True)
                raise A2ADeliveryUnknownError("A2A stream ended without a final task outcome")

        work = asyncio.create_task(exchange())
        revoked = asyncio.create_task(gate.binding.wait_revoked())
        try:
            done, _ = await asyncio.wait((work, revoked), return_when=asyncio.FIRST_COMPLETED)
            if work not in done:
                outcome = "local-stop-requested"
                raise asyncio.CancelledError
            await work
            started, success, outcome = True, True, "responded"
            if method == "tasks/get":
                uncertain.discard(task_id)
            return tuple(results)
        except A2ARequestRefusedError:
            started, success, outcome = False, False, "refused"
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError, A2ADeliveryUnknownError) as exc:
            uncertain.add(task_id)
            raise A2ADeliveryUnknownError("A2A delivery requires reconciliation, not automatic resubmission") from exc
        finally:
            if not work.done():
                work.cancel()
                uncertain.add(task_id)
            revoked.cancel()
            await asyncio.gather(work, revoked, return_exceptions=True)
            await gate.binding.report(call, started, success)
            gate.binding.observe(
                {
                    "type": "request_outcome",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "operation_id": call.operation_id,
                    "rpc_id": rpc_id,
                    "method": method,
                    "outcome": outcome,
                    "task_id": task_id,
                }
            )

    async def send(message: dict[str, Any], *, stream: bool = False) -> tuple[dict[str, Any], ...]:
        snapshot = _message(message)
        task = snapshot.get("taskId")
        if task is not None:
            _identity(task, "taskId")
            known = tasks.get(task)
            if known is None or known[1] != "input-required" or snapshot.get("contextId") != known[0]:
                raise ValueError("A2A input requires the admitted task/context in input-required state")
        if task in uncertain or None in uncertain:
            raise A2ADeliveryUnknownError("Reconcile uncertain delivery with tasks/get before sending again")
        return await request("message/stream" if stream else "message/send", {"message": snapshot}, stream=stream)

    async def get(task_id: str) -> dict[str, Any]:
        values = await request("tasks/get", {"id": _identity(task_id, "taskId")})
        return values[-1]

    async def cancel(task_id: str) -> dict[str, Any]:
        _identity(task_id, "taskId")
        if task_id not in tasks:
            raise ValueError("A2A cancellation requires a source-confirmed task identity")
        values = await request("tasks/cancel", {"id": task_id})
        return values[-1]

    gate.patch(target, "send_message", send)
    gate.patch(target, "get_task", get)
    gate.patch(target, "cancel_task", cancel)
