"""Test that the health endpoint returns a valid response."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import create_app


@pytest.fixture
def app():
    return create_app(dev=True)


@pytest.mark.asyncio
async def test_health_returns_healthy(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["version"] == "0.1.0"
    assert "uptimeSeconds" in data
