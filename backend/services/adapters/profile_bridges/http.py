"""Authenticated, exact-route middleware around caller-owned ASGI/WSGI applications.

No route or process is launched by this bridge. CodePlane authentication and a
distinct process-only binding credential precede preflight. HTTP completion is
not proof that an external agent stopped. Generic HTTP supplies no token/currency
usage contract, so status/body size never becomes inferred usage.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import secrets
from contextlib import suppress
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from starlette.requests import Request

from backend.services.adapters.ai_card import verify_card
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import (
    ExecutionEvidence,
    NativeCall,
    NativeGateDeniedError,
    encode_arguments,
)
from backend.services.adapters.native_gates.lifecycle import retain_task
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.auth.middleware import AuthPolicy, is_request_authenticated
from backend.services.ingest.mapping import canonical_record

if TYPE_CHECKING:
    from starlette.types import Message, Receive, Scope, Send

    from backend.services.adapters.native_gates import GateAttachment

log = structlog.get_logger()
_LIMIT = 1024 * 1024
_BINDING = "x-codeplane-request-binding"
_EPOCH = "x-codeplane-request-epoch"
_TOKEN = "x-codeplane-request-token"


@dataclass(frozen=True)
class HttpIdentity:
    document: bytes
    project_id: str
    did: str
    public_key: str
    endpoint: str


@dataclass
class HttpTarget:
    framework: str
    app: Any
    operations: dict[tuple[str, str], str]
    auth_policy: AuthPolicy = field(default_factory=lambda: AuthPolicy(remote=True, password_required=True))
    identity: HttpIdentity | None = None

    def request_headers(self) -> dict[str, str]:
        raise PermissionError("HTTP handler has no live Runtime binding")

    async def asgi(self, scope: Any, receive: Any, send: Any) -> None:
        raise PermissionError("HTTP handler has no live Runtime binding")

    def wsgi(self, environ: Any, start_response: Any) -> Any:
        raise PermissionError("HTTP handler has no live Runtime binding")


def signed_request(headers: dict[str, str], method: str, path: str, body: bytes, *, query: str = "") -> bytes:
    """Public signed material, never the operator session or binding secret."""
    return canonical_record(
        {
            "binding_id": headers[_BINDING],
            "execution_epoch": headers[_EPOCH],
            "request_id": headers["x-request-id"],
            "method": method,
            "path": path,
            "query": query,
            "content_type": headers.get("content-type", ""),
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }
    )


def install(gate: GateAttachment, target: object) -> None:
    if not isinstance(target, HttpTarget):
        raise TypeError("HTTP bridge requires an explicitly configured HttpTarget")
    expected = {
        "fastapi": ("fastapi", "FastAPI"),
        "flask": ("flask", "Flask"),
        "django": ("django.core.handlers.wsgi", "WSGIHandler"),
    }
    if target.framework not in expected:
        raise ValueError("Unqualified HTTP application framework")
    module, symbol = expected[target.framework]
    if not isinstance(target.app, getattr(load_sdk(module), symbol)):
        raise TypeError("HTTP bridge requires the original framework application")
    if not target.auth_policy.enabled or not target.auth_policy.remote:
        raise ValueError("HTTP bridge requires explicit authenticated remote posture, never loopback trust")
    operations = dict(target.operations)
    if not operations or any(
        not method.isupper() or not path.startswith("/") or "?" in path or "#" in path or not name
        for (method, path), name in operations.items()
    ):
        raise ValueError("HTTP controls require exact methods, paths and declared operation names")
    app, policy, identity = target.app, target.auth_policy, deepcopy(target.identity)
    gate.claim(app)
    credential = secrets.token_urlsafe(32)
    binding = gate.binding.execution
    if target.framework == "fastapi":
        matched = set()
        for route in app.routes:
            for method in getattr(route, "methods", ()) or ():
                if (method, getattr(route, "path", "")) in operations:
                    dependant = getattr(route, "dependant", None)
                    if dependant is None or not gate.track_callable(dependant, "call"):
                        raise ValueError("Declared FastAPI controls require original APIRoute endpoints")
                    gate.track_callable(route, "app")
                    matched.add((method, route.path))
        if matched != set(operations):
            raise ValueError("Declared FastAPI controls must match installed exact routes")

    def callable_identity(value: Any) -> tuple[int, int]:
        function = getattr(value, "__func__", value)
        return id(function), id(getattr(function, "__code__", None))

    def routes() -> Any:
        if target.framework == "fastapi":
            return [
                (
                    getattr(route, "path", ""),
                    sorted(getattr(route, "methods", ()) or ()),
                    callable_identity(getattr(route, "endpoint", None)),
                    callable_identity(getattr(route, "app", None)),
                    callable_identity(getattr(getattr(route, "dependant", None), "call", None)),
                )
                for route in app.routes
            ]
        if target.framework == "flask":
            return {
                "rules": [(rule.rule, rule.endpoint, sorted(rule.methods)) for rule in app.url_map.iter_rules()],
                "views": {name: callable_identity(view) for name, view in app.view_functions.items()},
                "wsgi": callable_identity(app.wsgi_app),
            }
        resolve = load_sdk("django.urls").resolve
        return [(path, callable_identity(resolve(path).func)) for _, path in operations]

    initial_routes = encode_arguments(routes())

    def identity_verified() -> None:
        if identity is not None:
            verify_card(
                identity.document,
                binding_id=binding.binding_id.replace(":", "-"),
                project_id=identity.project_id,
                expected_did=identity.did,
                public_key=identity.public_key,
                services=(("http", identity.endpoint),),
            )

    identity_verified()

    def headers() -> dict[str, str]:
        gate.validate()
        if not gate.active:
            raise PermissionError("HTTP binding was revoked")
        return {_BINDING: binding.binding_id, _EPOCH: binding.epoch, _TOKEN: credential}

    gate.patch(target, "request_headers", headers)

    def authenticate(scope: Scope) -> tuple[Request, str]:
        if not gate.active:
            raise PermissionError("HTTP binding is no longer live")
        try:
            gate.validate()
        except UnsupportedNativeHookError as exc:
            raise PermissionError("HTTP attachment changed; detach before replacing it") from exc
        if target.app is not app or encode_arguments(routes()) != initial_routes:
            raise PermissionError("HTTP handler configuration changed; detach before replacing it")
        request = Request({**scope, "app": SimpleNamespace(state=SimpleNamespace(auth_policy=policy))})
        if any(
            len(request.headers.getlist(name)) > 1
            for name in (
                _BINDING,
                _EPOCH,
                _TOKEN,
                "cookie",
                "x-request-id",
                "x-agent-did",
                "x-agent-signature",
                "cf-access-jwt-assertion",
                "x-codeplane-ingest-token",
            )
        ):
            raise PermissionError("Ambiguous HTTP authentication or correlation headers")
        if not is_request_authenticated(request):
            raise PermissionError("CodePlane authentication is required")
        if "x-codeplane-ingest-token" in request.headers:
            raise PermissionError("Ingest credentials cannot invoke handlers or declared controls")
        if request.headers.get(_BINDING) != binding.binding_id or request.headers.get(_EPOCH) != binding.epoch:
            raise PermissionError("HTTP credential belongs to another binding or execution epoch")
        supplied = request.headers.get(_TOKEN, "")
        if len(supplied) > 256 or not hmac.compare_digest(supplied.encode(), credential.encode()):
            raise PermissionError("HTTP binding credential is invalid")
        if (scope["method"], scope["path"]) not in operations:
            raise PermissionError("HTTP operation is not declared for this binding")
        request_id = request.headers.get("x-request-id", "")
        if not request_id or len(request_id) > 128 or any(ord(char) < 32 for char in request_id):
            raise PermissionError("HTTP request needs a bounded correlation identity")
        if identity is None and any(
            key in request.headers for key in ("x-agent-did", "x-agent-signature", "x-agent-capabilities")
        ):
            raise PermissionError("Self-asserted agent identity is not authentication")
        return request, request_id

    def actor(request: Request, body: bytes) -> str | None:
        if identity is None:
            return None
        identity_verified()
        if str(request.url).split("?", 1)[0] != identity.endpoint:
            raise PermissionError("HTTP request does not target the independently pinned endpoint")
        if request.headers.get("x-agent-did") != identity.did:
            raise PermissionError("HTTP actor does not match the independently pinned identity")
        try:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(identity.public_key, validate=True))
            key.verify(
                base64.b64decode(request.headers.get("x-agent-signature", ""), validate=True),
                signed_request(dict(request.headers), request.method, request.url.path, body, query=request.url.query),
            )
        except (ValueError, InvalidSignature) as exc:
            raise PermissionError("HTTP request signature is invalid") from exc
        return identity.did

    def call_for(request: Request, request_id: str, body: bytes) -> NativeCall:
        if len(body) > _LIMIT:
            raise ValueError("HTTP request exceeds 1 MiB")
        actor_id = actor(request, body)
        snapshot = {
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query,
            "body_base64": base64.b64encode(body).decode(),
            "request_id": request_id,
            "actor_did": actor_id,
        }

        def fingerprint() -> str:
            identity_verified()
            return encode_arguments(
                {
                    "app": id(target.app),
                    "operations": sorted((method, path, name) for (method, path), name in target.operations.items()),
                    "policy": asdict(target.auth_policy),
                    "identity": repr(target.identity),
                    "routes": routes(),
                    "authenticated": is_request_authenticated(request),
                }
            )

        return NativeCall(
            f"http.{operations[request.method, request.url.path]}",
            snapshot,
            definition="Authenticated configured handler; remote completion is not inferred from local cancellation.",
            fingerprint=fingerprint,
            evidence=ExecutionEvidence(False),
        )

    def observe(kind: str, call: NativeCall, **fields: Any) -> None:
        gate.binding.observe(
            {
                "type": kind,
                "timestamp": datetime.now(UTC).isoformat(),
                "operation_id": call.operation_id,
                "request_id": call.arguments["request_id"],
                "actor_did": call.arguments["actor_did"],
                "method": call.arguments["method"],
                "path": call.arguments["path"],
                **fields,
            }
        )

    async def asgi_entry(scope: Scope, receive: Receive, send: Send, summary: dict[str, Any]) -> None:
        async def observed_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                summary["status"] = message["status"]
            if message["type"] == "http.response.body":
                summary["bytes"] += len(message.get("body", b""))
                if summary["bytes"] > _LIMIT:
                    raise ValueError("HTTP response exceeds 1 MiB")
                summary["complete"] = not message.get("more_body", False)
            await send(message)

        await app(scope, receive, observed_send)

    def wsgi_entry(environ: Any, start_response: Any, summary: dict[str, Any]) -> list[bytes]:
        def observed_start(status: str, response_headers: Any, exc_info: Any = None) -> Any:
            summary["status"] = int(status.split()[0])
            return start_response(status, response_headers, exc_info)

        result = app(environ, observed_start)
        try:
            content = []
            for block in result:
                summary["bytes"] += len(block)
                if summary["bytes"] > _LIMIT:
                    raise ValueError("HTTP response exceeds 1 MiB")
                content.append(block)
            summary["complete"] = True
            return content
        finally:
            if hasattr(result, "close"):
                result.close()

    entry = SimpleNamespace(asgi=asgi_entry, wsgi=wsgi_entry)
    gate.track_callable(entry, "asgi")
    gate.track_callable(entry, "wsgi")

    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        if target.framework != "fastapi":
            raise TypeError("This configured application uses WSGI")
        if scope["type"] != "http":
            raise PermissionError("This binding declares HTTP handlers only, not lifespan or WebSocket control")
        try:
            request, request_id = authenticate(scope)
            chunks = bytearray()
            while True:
                incoming = await receive()
                if incoming["type"] == "http.disconnect":
                    raise PermissionError("HTTP requester disconnected before preflight")
                chunks.extend(incoming.get("body", b""))
                if len(chunks) > _LIMIT:
                    raise ValueError("HTTP request exceeds 1 MiB")
                if not incoming.get("more_body", False):
                    break
            body = bytes(chunks)
            call = call_for(request, request_id, body)
            if not await gate.allow(call):
                raise NativeGateDeniedError("HTTP handler denied before invocation")
        except (PermissionError, ValueError) as exc:
            log.warning("native_http_request_refused", reason=str(exc))
            await send({"type": "http.response.start", "status": 403, "headers": []})
            await send({"type": "http.response.body", "body": b"Request refused"})
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        summary = {"status": 0, "bytes": 0, "complete": False}
        observe("request_started", call, body_sha256=hashlib.sha256(body).hexdigest())

        async def execute() -> None:
            pending = asyncio.create_task(entry.asgi(scope, replay, send, summary))
            revoked = asyncio.create_task(gate.binding.wait_revoked())
            try:
                done, _ = await asyncio.wait((pending, revoked), return_when=asyncio.FIRST_COMPLETED)
                if pending not in done:
                    pending.cancel()
                await retain_task(pending)
            finally:
                if not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await retain_task(pending)
                revoked.cancel()
                await asyncio.gather(revoked, return_exceptions=True)

        try:
            await gate.complete(
                call, execute, outcome=lambda _: bool(summary["complete"] and 200 <= summary["status"] < 400)
            )
        except BaseException:
            observe("request_incomplete", call, remote_outcome="unknown")
            raise
        observe("request_completed", call, status=summary["status"], response_bytes=summary["bytes"])

    def wsgi(environ: Any, start_response: Any) -> Any:
        if target.framework == "fastapi":
            raise TypeError("This configured application uses ASGI")
        raw_headers = [
            (key[5:].replace("_", "-").lower().encode(), str(value).encode("latin-1"))
            for key, value in environ.items()
            if key.startswith("HTTP_")
        ]
        if environ.get("CONTENT_TYPE"):
            raw_headers.append((b"content-type", environ["CONTENT_TYPE"].encode("latin-1")))
        scope = {
            "type": "http",
            "method": environ["REQUEST_METHOD"],
            "path": environ.get("PATH_INFO", ""),
            "query_string": environ.get("QUERY_STRING", "").encode(),
            "scheme": environ.get("wsgi.url_scheme", "http"),
            "headers": raw_headers,
            "client": (environ.get("REMOTE_ADDR", ""), 0),
        }
        try:
            request, request_id = authenticate(scope)
            length = int(environ.get("CONTENT_LENGTH") or "0")
            if not 0 <= length <= _LIMIT:
                raise ValueError("HTTP request exceeds 1 MiB")
            body = environ["wsgi.input"].read(length)
            if len(body) != length:
                raise ValueError("HTTP request body is incomplete")
            call = call_for(request, request_id, body)
            if not gate.allow_sync(call):
                raise NativeGateDeniedError("HTTP handler denied before invocation")
        except (PermissionError, ValueError) as exc:
            log.warning("native_http_request_refused", reason=str(exc))
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"Request refused"]
        replay = {**environ, "wsgi.input": io.BytesIO(body)}
        summary = {"status": 0, "bytes": 0, "complete": False}
        observe("request_started", call, body_sha256=hashlib.sha256(body).hexdigest())
        try:
            result = gate.complete_sync(
                call,
                lambda: entry.wsgi(replay, start_response, summary),
                outcome=lambda _: bool(summary["complete"] and 200 <= summary["status"] < 400),
            )
        except BaseException:
            observe("request_incomplete", call, remote_outcome="unknown")
            raise
        observe("request_completed", call, status=summary["status"], response_bytes=summary["bytes"])
        return result

    gate.patch(target, "asgi", asgi)
    gate.patch(target, "wsgi", wsgi)
