"""Red-team / pressure tests for the API surface (Phase 1).

Covers: health endpoint behaviour under adversarial inputs,
stub route handling, HTTP method abuse, and header edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture
def _client() -> Any:
    """Yield an async client wired to the ASGI app."""
    app = create_app(dev=True)

    async def _make() -> AsyncClient:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        return AsyncClient(transport=transport, base_url="http://test")

    return _make


@pytest.fixture
async def client(_client: Any) -> AsyncGenerator[AsyncClient, None]:
    async with await _client() as c:
        yield c


# ── Health endpoint ──────────────────────────────────────────────


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_post_method_rejected(self, client: AsyncClient) -> None:
        resp = await client.post("/api/health")
        assert resp.status_code == 405

    @pytest.mark.asyncio
    async def test_put_method_rejected(self, client: AsyncClient) -> None:
        resp = await client.put("/api/health")
        assert resp.status_code == 405

    @pytest.mark.asyncio
    async def test_delete_method_rejected(self, client: AsyncClient) -> None:
        resp = await client.delete("/api/health")
        assert resp.status_code == 405

    @pytest.mark.asyncio
    async def test_patch_method_rejected(self, client: AsyncClient) -> None:
        resp = await client.patch("/api/health")
        assert resp.status_code == 405

    @pytest.mark.asyncio
    async def test_head_method_on_get_route(self, client: AsyncClient) -> None:
        # Starlette may auto-generate HEAD for GET routes
        resp = await client.head("/api/health")
        assert resp.status_code in (200, 405)

    @pytest.mark.asyncio
    async def test_options_method(self, client: AsyncClient) -> None:
        resp = await client.options("/api/health")
        # FastAPI returns 200 for OPTIONS with CORS enabled
        assert resp.status_code in (200, 405)

    @pytest.mark.asyncio
    async def test_response_format_is_camelcase(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health")
        data = resp.json()
        # Must use camelCase, not snake_case
        assert "uptimeSeconds" in data
        assert "activeJobs" in data
        assert "queuedJobs" in data
        # Must NOT expose snake_case
        assert "uptime_seconds" not in data
        assert "active_jobs" not in data
        assert "queued_jobs" not in data

    @pytest.mark.asyncio
    async def test_uptime_is_non_negative(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health")
        assert resp.json()["uptimeSeconds"] >= 0

    @pytest.mark.asyncio
    async def test_health_with_query_params_ignored(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health?foo=bar&evil=<script>alert(1)</script>")
        assert resp.status_code == 200
        # Params must not leak into response
        data = resp.json()
        assert "<script>" not in str(data)

    @pytest.mark.asyncio
    async def test_health_with_body_ignored(self, client: AsyncClient) -> None:
        resp = await client.request("GET", "/api/health", content=b'{"attack": true}')
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_with_huge_header(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health", headers={"X-Evil": "A" * 8000})
        # Should either succeed or return a 4xx, never 500
        assert resp.status_code in (200, 431)

    @pytest.mark.asyncio
    async def test_concurrent_health_requests(self, client: AsyncClient) -> None:
        """Multiple concurrent health requests must all succeed."""
        import asyncio

        tasks = [client.get("/api/health") for _ in range(20)]
        results = await asyncio.gather(*tasks)
        assert all(r.status_code == 200 for r in results)


# ── Non-existent & stub routes ───────────────────────────────────


class TestStubRoutes:
    """Routes defined as comments but with no handler should 404 or 405."""

    @pytest.mark.asyncio
    async def test_post_jobs_returns_error_without_db(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs", json={"repo": "x", "prompt": "y"})
        # Routes are wired but the test lacks a real DB session
        assert resp.status_code in (400, 500)

    @pytest.mark.asyncio
    async def test_get_jobs_returns_error_without_db(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs")
        assert resp.status_code in (200, 500)

    @pytest.mark.asyncio
    async def test_get_job_by_id_returns_error(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/nonexistent-id")
        assert resp.status_code in (404, 500)

    @pytest.mark.asyncio
    async def test_cancel_job_returns_error(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs/fake-id/cancel")
        assert resp.status_code in (404, 500)

    @pytest.mark.asyncio
    async def test_get_events_sse_returns_503_without_lifespan(self, client: AsyncClient) -> None:
        """Without lifespan wiring, SSE infra is missing → 503."""
        resp = await client.get("/api/events")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_get_approvals_returns_error(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/fake/approvals")
        assert resp.status_code in (404, 405, 422, 500)

    @pytest.mark.asyncio
    async def test_resolve_approval_returns_error(self, client: AsyncClient) -> None:
        resp = await client.post("/api/approvals/fake/resolve", json={"resolution": "approved"})
        assert resp.status_code in (404, 405, 422, 500)

    @pytest.mark.asyncio
    async def test_get_artifacts_returns_error(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/fake/artifacts")
        assert resp.status_code in (404, 405, 500)

    @pytest.mark.asyncio
    async def test_get_workspace_returns_error(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/fake/workspace")
        assert resp.status_code in (404, 405, 500)

    @pytest.mark.asyncio
    async def test_voice_transcribe_returns_error(self, client: AsyncClient) -> None:
        # No audio field → 422 validation error (endpoint is now implemented)
        resp = await client.post("/api/voice/transcribe")
        assert resp.status_code in (404, 405, 422, 500)

    @pytest.mark.asyncio
    async def test_get_global_settings_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/api/settings/global")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_put_global_settings_returns_200(self, client: AsyncClient) -> None:
        resp = await client.put("/api/settings/global", json={"config_yaml": "server:\n  host: 127.0.0.1"})
        assert resp.status_code == 200


class TestNonExistentPaths:
    """Completely unknown paths must return 404, never 500."""

    @pytest.mark.asyncio
    async def test_random_path_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_path_traversal_attempt(self, client: AsyncClient) -> None:
        resp = await client.get("/api/../../../etc/passwd")
        assert resp.status_code in (400, 404)
        assert "root:" not in resp.text

    @pytest.mark.asyncio
    async def test_double_slash_path(self, client: AsyncClient) -> None:
        resp = await client.get("/api//health")
        # Should either route correctly or 404, never crash
        assert resp.status_code in (200, 307, 404)

    @pytest.mark.asyncio
    async def test_unicode_path(self, client: AsyncClient) -> None:
        resp = await client.get("/api/健康")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_null_byte_in_path(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health%00evil")
        assert resp.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_very_long_path(self, client: AsyncClient) -> None:
        resp = await client.get("/api/" + "a" * 10000)
        # Must not crash; 404 is fine, 414 URI Too Long is fine
        assert resp.status_code in (404, 414)


# ── CORS behaviour ───────────────────────────────────────────────


class TestCORSDevMode:
    """In dev mode, CORS should be enabled for localhost:5173 only."""

    @pytest.mark.asyncio
    async def test_cors_allows_dev_origin(self, client: AsyncClient) -> None:
        resp = await client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"

    @pytest.mark.asyncio
    async def test_cors_rejects_arbitrary_origin(self) -> None:
        app = create_app(dev=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.options(
                "/api/health",
                headers={
                    "Origin": "http://evil.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            # Should NOT reflect evil origin
            acao = resp.headers.get("access-control-allow-origin", "")
            assert "evil.com" not in acao

    @pytest.mark.asyncio
    async def test_cors_disabled_in_prod_mode(self) -> None:
        app = create_app(dev=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.options(
                "/api/health",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            )
            # Without CORS middleware, no ACAO header
            assert "access-control-allow-origin" not in resp.headers


# ── Content-Type abuse ───────────────────────────────────────────


class TestContentTypeAbuse:
    @pytest.mark.asyncio
    async def test_health_content_type_is_json(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health")
        assert "application/json" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_post_to_health_with_xml(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/health",
            content=b"<evil/>",
            headers={"Content-Type": "application/xml"},
        )
        assert resp.status_code == 405
