"""Bound MCP operations using standard transports and authenticated native handlers."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import inspect
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import jsonschema
from agentmesh.integrations.mcp import TrustGatedMCPServer
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from pydantic_core import to_jsonable_python
from starlette.requests import Request

from backend.services.adapters.ai_card import verify_card
from backend.services.adapters.native_gates.binding import NativeCall, NativeGateDeniedError, encode_arguments
from backend.services.ingest.mapping import canonical_record
from backend.services.ingest.otel_decoder import decode_json_body

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from agentmesh.identity import AgentIdentity
    from mcp.server.fastmcp.tools import Tool

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
    stdio: StdioServerParameters | None = None
    authority: McpCallerAuthority | None = None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        raise PermissionError("MCP target must be attached to a live Runtime execution")


@dataclass(frozen=True)
class McpCallerAuthority:
    identity: AgentIdentity
    binding_id: str
    epoch: str

    def proof(self, endpoint: str, tool: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        payload = {
            "caller_did": str(self.identity.did),
            "binding_id": self.binding_id,
            "epoch": self.epoch,
            "endpoint": endpoint,
            "tool": tool,
            "request_id": request_id,
            "arguments_sha256": hashlib.sha256(canonical_record(arguments)).hexdigest(),
            "nonce": uuid.uuid4().hex,
            "expires_at": int(time.time()) + 60,
        }
        return {**payload, "signature": self.identity.sign(canonical_record(payload))}


@dataclass
class McpServerTarget:
    server: FastMCP
    trust: TrustGatedMCPServer
    endpoint: str
    project_id: str
    peer_card: bytes
    expected_did: str
    public_key: str
    capabilities: tuple[str, ...]
    trust_score: int

    async def authority_scope(self) -> tuple[str, str]:
        raise PermissionError("MCP server is not attached to a live Runtime execution")


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
    if isinstance(target, McpServerTarget):
        _install_server(gate, target)
        return
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
    stdio = target.stdio.model_copy(deep=True) if target.stdio is not None else None
    authority = target.authority
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
            arguments={
                "endpoint": endpoint,
                "tool": name,
                "arguments": payload,
                "transport": "stdio" if stdio is not None else "streamable-http",
                "stdio_sha256": hashlib.sha256(canonical_record(stdio.model_dump(mode="json"))).hexdigest()
                if stdio is not None
                else None,
            },
            definition=f"MCP tools/call against pinned peer {did}; input schema SHA256 {fingerprint}",
        )
        if not await gate.allow(call):
            raise NativeGateDeniedError("MCP request denied before transport")
        verified_peer()
        request_id: str | None = None
        protocol_session: str | None = None
        outcome = "delivery_unknown"
        started: bool | None = None
        succeeded: bool | None = None
        native_result: dict[str, Any] | None = None
        try:
            async with (
                _connection(endpoint, stdio) as (read, write, session_id),
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session,
            ):
                await session.initialize()
                protocol_session = session_id()
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
                gate.binding.observe(
                    {
                        "type": "tool_started",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "operation_id": call.operation_id,
                        "request_id": request_id,
                        "tool": name,
                        "arguments": payload,
                        "schema_sha256": fingerprint,
                        "peer_did": did,
                        "protocol_session_id": protocol_session,
                        "tool_call_id_source": "codeplane-operation; native request_id retained separately",
                    }
                )
                metadata: dict[str, Any] = {"codeplane-operation-id": call.operation_id}
                if authority is not None:
                    metadata["codeplane-authority"] = authority.proof(endpoint, name, payload, request_id)
                pending = asyncio.create_task(session.call_tool(name, payload, meta=metadata))
                revoked = asyncio.create_task(gate.binding.wait_revoked())
                try:
                    done, _ = await asyncio.wait((pending, revoked), return_when=asyncio.FIRST_COMPLETED)
                    if pending not in done:
                        await revoked
                        raise asyncio.CancelledError
                    result = await pending
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
                        native_result = late.model_dump(mode="json", by_alias=True)
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
                finally:
                    revoked.cancel()
                    await asyncio.gather(revoked, return_exceptions=True)
                decode_json_body(result.model_dump_json(by_alias=True).encode())
                native_result = result.model_dump(mode="json", by_alias=True)
                started, succeeded = True, not result.isError
                outcome = (
                    "responded-after-cancel"
                    if not gate.binding.active
                    else "tool-error"
                    if result.isError
                    else "responded"
                )
                return result
        except (httpx.HTTPError, McpError, ValueError) as exc:
            # A returned protocol error or a lost transport is not proof that
            # a remote handler did not run. Never resubmit the operation.
            raise McpDeliveryUnknownError("MCP did not confirm a bounded tool response") from exc
        finally:
            await gate.binding.report(call, started, succeeded)
            gate.binding.observe(
                {
                    "type": "tool_completed"
                    if succeeded is True
                    else "tool_failed"
                    if succeeded is False
                    else "tool_unknown",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "operation_id": call.operation_id,
                    "request_id": request_id,
                    "tool": name,
                    "schema_sha256": fingerprint,
                    "peer_did": did,
                    "outcome": outcome,
                    "success": succeeded,
                    "result": native_result,
                    "protocol_session_id": protocol_session,
                    "tool_call_id_source": "codeplane-operation; native request_id retained separately",
                }
            )
            if target.observe is not None:
                target.observe(McpObservation(call.operation_id, request_id, name, fingerprint, did, outcome))

    gate.patch(target, "call_tool", invoke)


@asynccontextmanager
async def _connection(endpoint: str, stdio: StdioServerParameters | None) -> AsyncIterator[Any]:
    if stdio is not None:
        async with stdio_client(stdio) as (read, write):
            yield read, write, lambda: None
    else:
        async with streamablehttp_client(
            endpoint,
            timeout=timedelta(seconds=30),
            sse_read_timeout=timedelta(seconds=30),
            httpx_client_factory=_http_client,
        ) as connection:
            yield connection


def _install_server(gate: GateAttachment, target: McpServerTarget) -> None:
    if not isinstance(target.server, FastMCP) or not isinstance(target.trust, TrustGatedMCPServer):
        raise TypeError("MCP server binding requires actual standard and upstream trust servers")
    if type(target.trust_score) is not int or not 0 <= target.trust_score <= 1000:
        raise ValueError("MCP trust score must be an explicit operator-configured value from 0 to 1000")
    server, trust = target.server, target.trust
    endpoint, did, key, card, project = (
        target.endpoint,
        target.expected_did,
        target.public_key,
        bytes(target.peer_card),
        target.project_id,
    )
    capabilities, score = tuple(target.capabilities), target.trust_score
    tools = tuple(trust._tools.items())
    if not tools or len(tools) > 100:
        raise ValueError("MCP server requires an explicit bounded tool inventory")
    manager = server._tool_manager
    schemas: dict[str, dict[str, Any]] = {}
    signatures: dict[str, inspect.Signature] = {}
    asynchronous_handlers: dict[str, bool] = {}
    for name, tool in tools:
        native = manager.get_tool(name)
        if native is None or canonical_record(native.parameters) != canonical_record(tool.input_schema):
            raise ValueError("MCP trust tool must match the actual native handler and schema")
        if not callable(native.fn) or tool.handler is not native.fn:
            raise ValueError("MCP trust registration must reference the actual native handler")
        if tool.require_human_sponsor:
            raise ValueError("MCP sponsor-required tools need a separate verified sponsorship authority")
        _local_schema(tool.input_schema)
        jsonschema.Draft202012Validator.check_schema(tool.input_schema)
        schemas[name] = deepcopy(tool.input_schema)
        signatures[name] = inspect.signature(native.fn)
        asynchronous_handlers[name] = native.is_async

    def verify_peer() -> None:
        verify_card(
            card,
            binding_id=gate.binding.execution.binding_id.replace(":", "-"),
            project_id=project,
            expected_did=did,
            public_key=key,
            services=(("mcp", endpoint),),
        )

    verify_peer()
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True))
    gate.claim(server)
    gate.claim(manager)
    gate.claim(trust)
    gate.patch(trust, "audit_all_calls", False)
    original_dispatch = manager.call_tool
    invocation: ContextVar[tuple[Any, bool] | None] = ContextVar("mcp_server_invocation", default=None)
    seen: dict[str, int] = {}

    def native_tool(name: str) -> Tool:
        native = manager.get_tool(name)
        if native is None:
            raise NativeGateDeniedError("MCP native handler was removed after attachment")
        return native

    def fingerprint() -> str:
        return encode_arguments(
            {
                name: {
                    "native": id(native_tool(name)),
                    "trust": id(trust._tools.get(name)),
                    "schema": native_tool(name).parameters,
                    "trust_schema": trust._tools[name].input_schema,
                    "capability": trust._tools[name].required_capability,
                    "minimum": trust._tools[name].min_trust_score,
                    "sponsor": trust._tools[name].require_human_sponsor,
                }
                for name in schemas
            }
        )

    attached = fingerprint()

    def install_handler(name: str, tool: Any) -> None:
        native = native_tool(name)
        holder = SimpleNamespace(call=native.fn)
        gate.track_callable(holder, "call")
        asynchronous = native.is_async

        @wraps(native.fn)
        async def handler(*args: Any, **kwargs: Any) -> Any:
            if asynchronous:
                return await holder.call(*args, **kwargs)
            return await asyncio.to_thread(holder.call, *args, **kwargs)

        gate.patch(native, "fn", handler)
        gate.patch(native, "is_async", True)

        async def dispatch(**arguments: Any) -> Any:
            current = invocation.get()
            if current is None:
                raise PermissionError("MCP trust handler has no authenticated protocol request")
            context, convert_result = current
            return await original_dispatch(name, arguments, context=context, convert_result=convert_result)

        gate.patch(tool, "handler", dispatch)

    for name, tool in tools:
        install_handler(name, tool)

    async def authority_scope() -> tuple[str, str]:
        if not gate.active or not await gate.binding.current():
            raise PermissionError("MCP server binding is no longer live")
        return gate.binding.execution.binding_id, gate.binding.execution.epoch

    async def dispatch(name: str, arguments: dict[str, Any], context: Any = None, convert_result: bool = False) -> Any:
        gate.validate()
        if name not in schemas or context is None:
            raise PermissionError("MCP tool requires its bound native request context")
        if fingerprint() != attached or not gate.active or not await gate.binding.current():
            raise PermissionError("MCP server execution or tool configuration is no longer current")
        payload = decode_json_body(canonical_record(arguments))
        jsonschema.Draft202012Validator({**schemas[name], "additionalProperties": False}).validate(payload)
        verify_peer()
        meta = context.request_context.meta
        proof = meta.model_dump().get("codeplane-authority") if meta is not None else None
        if not isinstance(proof, dict):
            raise PermissionError("MCP requires a signed, binding-scoped request proof")
        proof = dict(proof)
        signature = proof.pop("signature", None)
        nonce, expires = proof.get("nonce"), proof.get("expires_at")
        now = int(time.time())
        expected = {
            "caller_did": did,
            "binding_id": gate.binding.execution.binding_id,
            "epoch": gate.binding.execution.epoch,
            "endpoint": endpoint,
            "tool": name,
            "request_id": str(context.request_id),
            "arguments_sha256": hashlib.sha256(canonical_record(payload)).hexdigest(),
            "nonce": nonce,
            "expires_at": expires,
        }
        if (
            proof != expected
            or not isinstance(signature, str)
            or not isinstance(nonce, str)
            or len(nonce) != 32
            or any(char not in "0123456789abcdef" for char in nonce)
            or type(expires) is not int
            or not now < expires <= now + 65
        ):
            raise PermissionError("MCP request proof has an invalid identity, scope or lifetime")
        try:
            public_key.verify(base64.b64decode(signature, validate=True), canonical_record(proof))
        except (ValueError, binascii.Error, InvalidSignature) as exc:
            raise PermissionError("MCP request signature does not match the pinned caller key") from exc
        for used, deadline in tuple(seen.items()):
            if deadline <= now:
                del seen[used]
        if nonce in seen or len(seen) >= 1024:
            raise PermissionError("MCP request proof was already used or its replay window is full")
        seen[nonce] = expires
        native = native_tool(name)
        request = context.request_context.request
        protocol_session = request.headers.get("mcp-session-id") if isinstance(request, Request) else None
        validated = native.fn_metadata.arg_model.model_validate(payload).model_dump(mode="json")

        def exact_entry(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
            bound = signatures[name].bind(*args, **kwargs)
            bound.apply_defaults()
            actual = {key: value for key, value in bound.arguments.items() if key != native.context_kwarg}
            return canonical_record(to_jsonable_python(actual)) == canonical_record(validated)

        call = NativeCall(
            f"mcp.{name}",
            {"endpoint": endpoint, "tool": name, "arguments": validated},
            tool_call_id=str(context.request_id),
            fingerprint=fingerprint,
            definition=f"Authenticated MCP handler for pinned caller {did}",
            entry_check=exact_entry,
        )
        if not await gate.allow(call):
            raise NativeGateDeniedError("MCP handler denied before invocation")
        verify_peer()
        token = invocation.set((context, convert_result))

        async def invoke() -> Any:
            gate.binding.observe(
                {
                    "type": "tool_started",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "operation_id": call.operation_id,
                    "request_id": str(context.request_id),
                    "tool": name,
                    "arguments": validated,
                    "peer_did": did,
                    "protocol_session_id": protocol_session,
                    "schema_sha256": hashlib.sha256(canonical_record(schemas[name])).hexdigest(),
                    "tool_call_id_source": "codeplane-operation; native request_id retained separately",
                }
            )
            result = await trust.invoke_tool(name, validated, did, list(capabilities), score)
            if not result.success:
                raise NativeGateDeniedError("MCP upstream trust or native handler refused the operation")
            return result.result

        operation = asyncio.create_task(gate.complete(call, invoke))
        try:
            try:
                result = await asyncio.shield(operation)
            except asyncio.CancelledError:
                if asynchronous_handlers[name]:
                    operation.cancel()
                # MCP uses level-triggered AnyIO cancellation. Drain the owned
                # operation outside that scope so its receipt transaction closes.
                with anyio.CancelScope(shield=True):
                    await asyncio.gather(operation, return_exceptions=True)
                raise
        except BaseException as exc:
            completed = operation.done() and not operation.cancelled() and operation.exception() is None
            outcome, success = ("responded-after-cancel", True) if completed else (type(exc).__name__, False)
            raise
        else:
            outcome, success = "responded", True
            return result
        finally:
            invocation.reset(token)
            gate.binding.observe(
                {
                    "type": "tool_completed" if success else "tool_failed",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "operation_id": call.operation_id,
                    "request_id": str(context.request_id),
                    "tool": name,
                    "peer_did": did,
                    "protocol_session_id": protocol_session,
                    "schema_sha256": hashlib.sha256(canonical_record(schemas[name])).hexdigest(),
                    "outcome": outcome,
                    "success": success,
                    "result": to_jsonable_python(operation.result()) if success else None,
                    "tool_call_id_source": "codeplane-operation; native request_id retained separately",
                }
            )

    gate.patch(manager, "call_tool", dispatch)
    gate.patch(target, "authority_scope", authority_scope)


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
