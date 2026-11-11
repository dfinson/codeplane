"""Tests for the preview proxy endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.preview import router
from backend.di import PreviewHttpClient


@pytest.fixture()
def preview_client() -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.build_request.side_effect = httpx.Request
    return client


@pytest.fixture()
def app(preview_client: AsyncMock) -> FastAPI:
    """Minimal FastAPI app with the preview router and a mock DI override."""
    from dishka import Provider, Scope, make_async_container, provide
    from dishka.integrations.fastapi import setup_dishka

    class TestProvider(Provider):
        scope = Scope.REQUEST

        @provide
        def client(self) -> PreviewHttpClient:
            return PreviewHttpClient(preview_client)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    container = make_async_container(TestProvider())
    setup_dishka(container, app)
    return app


class TestPreviewProxy:
    @pytest.mark.anyio()
    async def test_port_below_1024_rejected(self, app: FastAPI) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/preview/80/index.html")
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"]

    @pytest.mark.anyio()
    async def test_port_above_65535_rejected(self, app: FastAPI) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/preview/70000/index.html")
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"]

    @pytest.mark.anyio()
    async def test_successful_proxy(self, app: FastAPI, preview_client: AsyncMock) -> None:
        upstream = httpx.Response(
            200,
            content=b"<html>OK</html>",
            headers={"content-type": "text/html"},
        )
        preview_client.send.return_value = upstream

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/preview/3000/index.html")

        assert resp.status_code == 200
        assert resp.text == "<html>OK</html>"
        preview_client.send.assert_called_once()

    @pytest.mark.anyio()
    async def test_upstream_connection_error_returns_502(self, app: FastAPI, preview_client: AsyncMock) -> None:
        preview_client.send.side_effect = httpx.ConnectError("refused")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/preview/3000/")

        assert resp.status_code == 502
        assert "Cannot connect" in resp.json()["detail"]

    @pytest.mark.anyio()
    async def test_query_string_forwarded(self, app: FastAPI, preview_client: AsyncMock) -> None:
        upstream = httpx.Response(200, content=b"ok")
        preview_client.send.return_value = upstream

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.get("/api/preview/8080/path?foo=bar&baz=1")

        call_args = preview_client.build_request.call_args
        assert "foo=bar" in call_args.kwargs["url"]

    @pytest.mark.anyio()
    async def test_blocked_headers_not_forwarded(self, app: FastAPI, preview_client: AsyncMock) -> None:
        upstream = httpx.Response(200, content=b"ok")
        preview_client.send.return_value = upstream

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.get(
                "/api/preview/3000/",
                headers={"x-forwarded-for": "evil", "x-real-ip": "evil"},
            )

        call_args = preview_client.build_request.call_args
        forwarded = call_args.kwargs.get("headers", {})
        assert "x-forwarded-for" not in forwarded
        assert "x-real-ip" not in forwarded


class TestPreviewTransportPreservation:
    @pytest.mark.anyio()
    async def test_concurrent_ports_never_receive_another_preview_cookie(self):
        import asyncio

        from starlette.requests import Request

        from backend.api.preview import preview_proxy

        seen = []

        async def upstream(request):
            seen.append(request.headers.get("cookie"))
            await asyncio.sleep(0)
            return httpx.Response(200, headers={"set-cookie": f"preview={request.url.port}; Path=/"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:

            async def visit(port):
                request = Request(
                    {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []},
                    receive=AsyncMock(return_value={"type": "http.request", "body": b""}),
                )
                return await preview_proxy(port, "", request, PreviewHttpClient(client))

            await visit(3000)
            await asyncio.gather(visit(4000), visit(5000))
        assert seen == [None, None, None]

    @pytest.mark.anyio()
    async def test_cancelled_read_closes_upstream(self):
        import asyncio

        from starlette.requests import Request

        from backend.api.preview import preview_proxy

        class CancelledStream(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b"partial"
                raise asyncio.CancelledError

            async def aclose(self):
                self.closed = True

        stream = CancelledStream()
        request = Request(
            {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []},
            receive=AsyncMock(return_value={"type": "http.request", "body": b""}),
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
        ) as client:
            with pytest.raises(asyncio.CancelledError):
                await preview_proxy(3000, "", request, PreviewHttpClient(client))
        assert stream.closed

    @pytest.mark.anyio()
    async def test_redirect_is_returned_without_following(self):
        from starlette.requests import Request

        from backend.api.preview import preview_proxy

        seen = []

        async def upstream(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://external.example.com/next"})

        request = Request(
            {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []},
            receive=AsyncMock(return_value={"type": "http.request", "body": b""}),
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), follow_redirects=True) as client:
            response = await preview_proxy(3000, "", request, PreviewHttpClient(client))
        assert response.status_code == 302
        assert seen == ["http://127.0.0.1:3000/"]

    @pytest.mark.anyio()
    async def test_decode_error_still_closes_upstream(self):
        from starlette.requests import Request

        from backend.api.preview import preview_proxy

        class InvalidGzip(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b"not-gzip"

            async def aclose(self):
                self.closed = True

        stream = InvalidGzip()
        request = Request(
            {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []},
            receive=AsyncMock(return_value={"type": "http.request", "body": b""}),
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)
            )
        ) as client:
            response = await preview_proxy(3000, "", request, PreviewHttpClient(client))
        assert response.status_code == 502
        assert stream.closed


class TestRuntimeContract:
    @pytest.mark.anyio()
    async def test_credentials_and_response_cookies_never_cross_preview_boundary(self) -> None:
        from starlette.requests import Request

        from backend.api.preview import preview_proxy

        seen: list[httpx.Request] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, content=b"preview", headers={"set-cookie": "other=secret; Path=/"})

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/preview/3000/",
                "query_string": b"x=1",
                "headers": [
                    (b"cookie", b"cpl_session=private"),
                    (b"authorization", b"Bearer private"),
                    (b"cf-access-jwt-assertion", b"private"),
                    (b"x-codeplane-internal-token", b"private"),
                    (b"x-ordinary", b"keep"),
                ],
            },
            receive=AsyncMock(return_value={"type": "http.request", "body": b"payload"}),
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            response = await preview_proxy(3000, "", request, PreviewHttpClient(client))
        assert not (
            {"cookie", "authorization", "cf-access-jwt-assertion", "x-codeplane-internal-token"} & set(seen[0].headers)
        )
        assert "set-cookie" not in response.headers
        assert (seen[0].method, seen[0].content, seen[0].headers["x-ordinary"]) == ("POST", b"payload", "keep")
        assert (response.status_code, response.body) == (201, b"preview")

    @pytest.mark.anyio()
    async def test_shared_jar_never_replays_cookies_between_preview_ports(self) -> None:
        from starlette.requests import Request

        from backend.api.preview import preview_proxy

        seen: list[str | None] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("cookie"))
            return httpx.Response(200, headers={"set-cookie": "preview_secret=private; Path=/"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            for port in (3000, 4000, 3000):
                request = Request(
                    {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []},
                    receive=AsyncMock(return_value={"type": "http.request", "body": b""}),
                )
                await preview_proxy(port, "", request, PreviewHttpClient(client))
        assert seen == [None, None, None]

    @pytest.mark.anyio()
    @pytest.mark.parametrize("compressed", [False, True])
    async def test_decoded_limit_stops_consumption_and_closes_upstream(self, monkeypatch, compressed) -> None:
        import gzip

        from starlette.requests import Request

        from backend.api import preview

        class CountingStream(httpx.AsyncByteStream):
            consumed = 0
            closed = False

            async def __aiter__(self):
                chunks = [b"a" * 8, b"b" * 8, b"c" * 8]
                if compressed:
                    packed = gzip.compress(b"".join(chunks))
                    chunks = [packed[:-8], packed[-8:], b""]
                for chunk in chunks:
                    self.consumed += 1
                    yield chunk

            async def aclose(self):
                self.closed = True

        stream = CountingStream()
        monkeypatch.setattr(preview, "_MAX_RESPONSE_BYTES", 10)
        request = Request(
            {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []},
            receive=AsyncMock(return_value={"type": "http.request", "body": b""}),
        )
        headers = {"content-encoding": "gzip"} if compressed else {}
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, headers=headers, stream=stream))
        ) as client:
            response = await preview.preview_proxy(3000, "", request, PreviewHttpClient(client))
        assert response.status_code == 502
        assert stream.closed
        assert stream.consumed < 3, "The cap must stop reads, not reject after full download"
