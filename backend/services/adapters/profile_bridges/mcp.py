"""A bound MCP tool operation using the standard Streamable HTTP client."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx
import jsonschema
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

from backend.services.adapters.ai_card import verify_card
from backend.services.adapters.native_gates.binding import NativeCall, NativeGateDeniedError
from backend.services.ingest.mapping import canonical_record
from backend.services.ingest.otel_decoder import decode_json_body

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from backend.services.adapters.native_gates import GateAttachment


@dataclass(frozen=True, slots=True)
class McpObservation:
    operation_id: str
    request_id: str | None
    tool: str
    schema_sha256: str
    peer_did: str
    outcome: str


@dataclass
class McpTarget:
    endpoint: str
    project_id: str
    peer_card: bytes
    expected_did: str
    public_key: str
    tools: dict[str, dict[str, Any]]
    observe: Callable[[McpObservation], None] | None = None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        raise PermissionError("MCP target must be attached to a live Runtime execution")


class McpDeliveryUnknownError(OSError):
    outcome = "delivery_unknown"


def _local_schema(schema: dict[str, Any]) -> None:
    decode_json_body(canonical_record(schema))
    pending: list[Any] = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for name in ("$ref", "$dynamicRef"):
                reference = value.get(name)
                if reference is not None and (not isinstance(reference, str) or not reference.startswith("#")):
                    raise ValueError("MCP schemas must not resolve external references")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def install(gate: GateAttachment, target: object) -> None:
    if not isinstance(target, McpTarget):
        raise TypeError("MCP gating requires an explicitly configured McpTarget")
    endpoint, project, card, did, key = (
        target.endpoint,
        target.project_id,
        bytes(target.peer_card),
        target.expected_did,
        target.public_key,
    )
    schemas = deepcopy(target.tools)
    if not schemas or len(schemas) > 100:
        raise ValueError("MCP target requires between 1 and 100 explicitly pinned tool schemas")
    decode_json_body(canonical_record(schemas))
    for schema in schemas.values():
        _local_schema(schema)
        jsonschema.Draft202012Validator.check_schema(schema)
        if schema.get("type") != "object":
            raise ValueError("MCP input schema must declare an object")

    def verified_peer() -> None:
        verify_card(
            card,
            binding_id=gate.binding.execution.binding_id.replace(":", "-"),
            project_id=project,
            expected_did=did,
            public_key=key,
            services=(("mcp", endpoint),),
        )

    verified_peer()

    async def invoke(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        if name not in schemas:
            raise ValueError("MCP tool is not in this binding's pinned schema inventory")
        encoded = canonical_record(arguments)
        payload = decode_json_body(encoded)
        schema = schemas[name]
        # Upstream trust wrappers silently drop unexpected keyword arguments.
        # Reject them before either approval or transport, instead.
        validation_schema = {**schema, "additionalProperties": False}
        jsonschema.Draft202012Validator(validation_schema).validate(payload)
        fingerprint = hashlib.sha256(canonical_record(schema)).hexdigest()
        verified_peer()
        call = NativeCall(
            name=f"mcp.{name}",
            arguments={"endpoint": endpoint, "tool": name, "arguments": payload},
            definition=f"MCP tools/call against pinned peer {did}; input schema SHA256 {fingerprint}",
        )
        if not await gate.allow(call):
            raise NativeGateDeniedError("MCP request denied before transport")
        verified_peer()
        request_id: str | None = None
        outcome = "delivery_unknown"
        started: bool | None = None
        succeeded: bool | None = None
        try:
            async with (
                streamablehttp_client(
                    endpoint,
                    timeout=timedelta(seconds=30),
                    sse_read_timeout=timedelta(seconds=30),
                    httpx_client_factory=_http_client,
                ) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                available = await session.list_tools()
                actual = next((tool for tool in available.tools if tool.name == name), None)
                if actual is None or canonical_record(actual.inputSchema) != canonical_record(schema):
                    raise ValueError("MCP peer's actual tool schema does not match the pinned schema")
                if actual.outputSchema is not None:
                    _local_schema(actual.outputSchema)
                if not await gate.binding.current() or not gate.active:
                    raise NativeGateDeniedError("MCP execution was revoked before tools/call")
                # This private counter is pinned to MCP 1.27.1 and there is only
                # one operation on this owned session, so no request can race it.
                request_id = str(session._request_id)
                pending = asyncio.create_task(
                    session.call_tool(name, payload, meta={"codeplane-operation-id": call.operation_id})
                )
                try:
                    result = await asyncio.shield(pending)
                except asyncio.CancelledError:
                    outcome = "cancel-requested"
                    await session.send_notification(
                        types.ClientNotification(
                            types.CancelledNotification(
                                params=types.CancelledNotificationParams(
                                    requestId=int(request_id), reason="Owning CodePlane invocation cancelled"
                                )
                            )
                        )
                    )
                    try:
                        late = await asyncio.wait_for(asyncio.shield(pending), timeout=2)
                        decode_json_body(late.model_dump_json(by_alias=True).encode())
                        started, succeeded = True, not late.isError
                        outcome = "responded-after-cancel"
                    except McpError as exc:
                        if exc.error.code == 0 and exc.error.message == "Request cancelled":
                            outcome = "cancel-acknowledged"
                    except TimeoutError:
                        outcome = "cancel-requested"
                    finally:
                        if not pending.done():
                            pending.cancel()
                        with contextlib.suppress(asyncio.CancelledError, McpError):
                            await pending
                    raise
                decode_json_body(result.model_dump_json(by_alias=True).encode())
                started, succeeded = True, not result.isError
                outcome = "tool-error" if result.isError else "responded"
                return result
        except (httpx.HTTPError, McpError, ValueError) as exc:
            # A returned protocol error or a lost transport is not proof that
            # a remote handler did not run. Never resubmit the operation.
            raise McpDeliveryUnknownError("MCP did not confirm a bounded tool response") from exc
        finally:
            await gate.binding.report(call, started, succeeded)
            if target.observe is not None:
                target.observe(McpObservation(call.operation_id, request_id, name, fingerprint, did, outcome))

    gate.patch(target, "call_tool", invoke)


def _http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={**(headers or {}), "accept-encoding": "identity"},
        timeout=timeout,
        auth=auth,
        follow_redirects=False,
        trust_env=False,
        transport=_BoundedTransport(),
    )


class _BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        received = 0
        async for chunk in self._stream:
            received += len(chunk)
            if received > 1024 * 1024:
                raise ValueError("MCP protocol response exceeds 1 MiB")
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class _BoundedTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise ValueError("MCP bridge requires bounded uncompressed protocol responses")
        if not isinstance(response.stream, httpx.AsyncByteStream):
            await response.aclose()
            raise ValueError("MCP transport did not provide an asynchronous response stream")
        response.stream = _BoundedStream(response.stream)
        return response
