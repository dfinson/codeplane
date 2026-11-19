"""Port preview proxy — reverse-proxies local development servers through CodePlane.

Enables previewing agent-built web apps without exposing extra ports.
Routes: ``/api/preview/{port}/{path}``
"""

from __future__ import annotations

import anyio
import httpx
import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from backend.di import PreviewHttpClient

router = APIRouter(tags=["preview"], route_class=DishkaRoute)

log = structlog.get_logger()

_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB cap on proxied responses

_UPSTREAM_BASE = "http://127.0.0.1"

# Headers that MUST NOT be forwarded to upstream (hop-by-hop + spoofable).
_BLOCKED_REQUEST_HEADERS = frozenset(
    {
        "host",
        "connection",
        "transfer-encoding",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "forwarded",
        "cookie",
        "authorization",
        "cf-access-jwt-assertion",
        "x-codeplane-internal-token",
    }
)

# Response headers stripped before forwarding back to the client.
_BLOCKED_RESPONSE_HEADERS = frozenset(
    {"transfer-encoding", "connection", "content-encoding", "content-length", "set-cookie"}
)


@router.api_route(
    "/preview/{port:int}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
    response_class=Response,
    response_model=None,
)
async def preview_proxy(port: int, path: str, request: Request, client: FromDishka[PreviewHttpClient]) -> Response:
    """Reverse-proxy a request to a local development server."""
    if port < 1024 or port > 65535:
        return JSONResponse({"detail": f"Port {port} not allowed (must be 1024-65535)"}, status_code=400)

    upstream_url = f"{_UPSTREAM_BASE}:{port}/{path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    # Forward headers (except blocked ones)
    forward_headers = {}
    for key, value in request.headers.items():
        if key.lower() not in _BLOCKED_REQUEST_HEADERS:
            forward_headers[key] = value

    try:
        body = await request.body()
        upstream_request = client.build_request(
            method=request.method,
            url=upstream_url,
            headers=forward_headers,
            content=body if body else None,
        )
        # build_request merges the shared client's cookie jar and defaults.
        # Strip again after that merge; never mutate the jar around a send.
        for header in _BLOCKED_REQUEST_HEADERS - {"host"}:
            upstream_request.headers.pop(header, None)
        upstream_response = await client.send(upstream_request, stream=True, auth=None, follow_redirects=False)
        try:
            content = bytearray()
            async for chunk in upstream_response.aiter_bytes():
                if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
                    return JSONResponse(
                        {"detail": f"Upstream response too large (limit {_MAX_RESPONSE_BYTES} bytes)"},
                        status_code=502,
                    )
                content.extend(chunk)
            response_headers = {
                key: value
                for key, value in upstream_response.headers.items()
                if key.lower() not in _BLOCKED_RESPONSE_HEADERS
            }
            return Response(
                content=bytes(content),
                status_code=upstream_response.status_code,
                headers=response_headers,
                media_type=upstream_response.headers.get("content-type"),
            )
        finally:
            with anyio.CancelScope(shield=True):
                await upstream_response.aclose()
    except httpx.HTTPError as exc:
        error_type = type(exc).__name__
        log.debug("preview_proxy_error", port=port, path=path, error=error_type)
        return JSONResponse(
            {"detail": f"Cannot connect to service on port {port}", "error": error_type},
            status_code=502,
        )
