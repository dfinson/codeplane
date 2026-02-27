"""Tests for daemon/routes.py module.

Covers:
- create_routes() function
- /health endpoint
- /status endpoint
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.routing import Router
from starlette.testclient import TestClient

from codeplane.daemon.routes import create_routes


class TestCreateRoutes:
    """Tests for create_routes function."""

    @pytest.fixture
    def mock_controller(self, tmp_path: Path) -> MagicMock:
        """Create mock ServerController."""
        controller = MagicMock()
        controller.repo_root = tmp_path

        # Mock indexer status
        status = MagicMock()
        status.state.value = "idle"
        status.queue_size = 0
        status.last_error = None
        controller.indexer.status = status

        # Mock watcher
        controller.watcher._watch_task = None

        return controller

    def test_returns_list_of_routes(self, mock_controller: MagicMock) -> None:
        """Returns a list of Route objects."""
        routes = create_routes(mock_controller)
        assert isinstance(routes, list)
        assert len(routes) == 5

    def test_health_route_exists(self, mock_controller: MagicMock) -> None:
        """Health route is defined."""
        routes = create_routes(mock_controller)
        paths = [r.path for r in routes]
        assert "/health" in paths

    def test_status_route_exists(self, mock_controller: MagicMock) -> None:
        """Status route is defined."""
        routes = create_routes(mock_controller)
        paths = [r.path for r in routes]
        assert "/status" in paths

    def test_sidecar_cache_routes_exist(self, mock_controller: MagicMock) -> None:
        """All sidecar cache routes are defined."""
        routes = create_routes(mock_controller)
        paths = [r.path for r in routes]
        assert "/sidecar/cache/list" in paths
        assert "/sidecar/cache/slice" in paths
        assert "/sidecar/cache/meta" in paths


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        """Create test client with routes."""
        controller = MagicMock()
        controller.repo_root = tmp_path

        routes = create_routes(controller)
        app = Router(routes=routes)
        return TestClient(app)

    def test_health_returns_200(self, client: TestClient) -> None:
        """Health check returns 200 OK."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_json(self, client: TestClient) -> None:
        """Health check returns JSON."""
        response = client.get("/health")
        data = response.json()
        assert isinstance(data, dict)

    def test_health_contains_status(self, client: TestClient) -> None:
        """Health response contains status field."""
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_contains_repo_root(self, client: TestClient, tmp_path: Path) -> None:
        """Health response contains repo_root field."""
        response = client.get("/health")
        data = response.json()
        assert data["repo_root"] == str(tmp_path)

    def test_health_contains_version(self, client: TestClient) -> None:
        """Health response contains version field."""
        response = client.get("/health")
        data = response.json()
        assert "version" in data


class TestStatusEndpoint:
    """Tests for /status endpoint."""

    @pytest.fixture
    def mock_controller(self, tmp_path: Path) -> MagicMock:
        """Create mock ServerController."""
        controller = MagicMock()
        controller.repo_root = tmp_path

        # Mock indexer status
        status = MagicMock()
        status.state.value = "idle"
        status.queue_size = 5
        status.last_error = None
        controller.indexer.status = status

        # Mock watcher
        controller.watcher._watch_task = MagicMock()  # Running

        return controller

    @pytest.fixture
    def client(self, mock_controller: MagicMock) -> TestClient:
        """Create test client with routes."""
        routes = create_routes(mock_controller)
        app = Router(routes=routes)
        return TestClient(app)

    def test_status_returns_200(self, client: TestClient) -> None:
        """Status endpoint returns 200 OK."""
        response = client.get("/status")
        assert response.status_code == 200

    def test_status_returns_json(self, client: TestClient) -> None:
        """Status endpoint returns JSON."""
        response = client.get("/status")
        data = response.json()
        assert isinstance(data, dict)

    def test_status_contains_repo_root(self, client: TestClient, tmp_path: Path) -> None:  # noqa: ARG002
        """Status response contains repo_root."""
        response = client.get("/status")
        data = response.json()
        assert "repo_root" in data

    def test_status_contains_indexer_info(self, client: TestClient) -> None:
        """Status response contains indexer info."""
        response = client.get("/status")
        data = response.json()
        assert "indexer" in data
        assert data["indexer"]["state"] == "idle"
        assert data["indexer"]["queue_size"] == 5

    def test_status_contains_watcher_info(self, client: TestClient) -> None:
        """Status response contains watcher info."""
        response = client.get("/status")
        data = response.json()
        assert "watcher" in data
        assert data["watcher"]["running"] is True

    def test_status_watcher_not_running(self, tmp_path: Path) -> None:
        """Status shows watcher not running when watch_task is None."""
        controller = MagicMock()
        controller.repo_root = tmp_path

        status = MagicMock()
        status.state.value = "idle"
        status.queue_size = 0
        status.last_error = None
        controller.indexer.status = status

        controller.watcher._watch_task = None  # Not running

        routes = create_routes(controller)
        app = Router(routes=routes)
        client = TestClient(app)

        response = client.get("/status")
        data = response.json()
        assert data["watcher"]["running"] is False


# =============================================================================
# Sidecar Cache Endpoints
# =============================================================================


class TestSidecarCacheListEndpoint:
    """Tests for /sidecar/cache/list endpoint."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        controller = MagicMock()
        controller.repo_root = tmp_path
        routes = create_routes(controller)
        app = Router(routes=routes)
        return TestClient(app)

    def test_missing_params_returns_400(self, client: TestClient) -> None:
        resp = client.get("/sidecar/cache/list")
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_missing_endpoint_returns_400(self, client: TestClient) -> None:
        resp = client.get("/sidecar/cache/list?session=s1")
        assert resp.status_code == 400

    def test_empty_list(self, client: TestClient) -> None:
        resp = client.get("/sidecar/cache/list?session=s1&endpoint=e1")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_list_returns_cached_entries(self, client: TestClient) -> None:
        from codeplane.mcp.sidecar_cache import cache_put, get_sidecar_cache

        get_sidecar_cache().clear()
        cache_put("s1", "e1", {"x": 1})
        resp = client.get("/sidecar/cache/list?session=s1&endpoint=e1")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["session_id"] == "s1"
        assert entries[0]["endpoint_key"] == "e1"
        get_sidecar_cache().clear()


class TestSidecarCacheSliceEndpoint:
    """Tests for /sidecar/cache/slice endpoint."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        controller = MagicMock()
        controller.repo_root = tmp_path
        routes = create_routes(controller)
        app = Router(routes=routes)
        return TestClient(app)

    def test_missing_cache_returns_400(self, client: TestClient) -> None:
        resp = client.get("/sidecar/cache/slice")
        assert resp.status_code == 400

    def test_not_found_returns_404(self, client: TestClient) -> None:
        resp = client.get("/sidecar/cache/slice?cache=nonexistent")
        assert resp.status_code == 404

    def test_slice_returns_payload(self, client: TestClient) -> None:
        from codeplane.mcp.sidecar_cache import cache_put, get_sidecar_cache

        get_sidecar_cache().clear()
        cid = cache_put("s1", "e1", {"data": [1, 2, 3]})
        resp = client.get(f"/sidecar/cache/slice?cache={cid}&path=data")
        assert resp.status_code == 200
        result = resp.json()
        assert "value" in result
        assert result["value"] == [1, 2, 3]
        get_sidecar_cache().clear()

    def test_slice_with_max_bytes(self, client: TestClient) -> None:
        from codeplane.mcp.sidecar_cache import cache_put, get_sidecar_cache

        get_sidecar_cache().clear()
        cid = cache_put("s1", "e1", {"data": "x" * 10_000})
        resp = client.get(f"/sidecar/cache/slice?cache={cid}&max_bytes=500")
        assert resp.status_code == 200
        result = resp.json()
        assert "truncated" in result
        get_sidecar_cache().clear()


class TestSidecarCacheMetaEndpoint:
    """Tests for /sidecar/cache/meta endpoint."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        controller = MagicMock()
        controller.repo_root = tmp_path
        routes = create_routes(controller)
        app = Router(routes=routes)
        return TestClient(app)

    def test_missing_cache_returns_400(self, client: TestClient) -> None:
        resp = client.get("/sidecar/cache/meta")
        assert resp.status_code == 400

    def test_not_found_returns_404(self, client: TestClient) -> None:
        resp = client.get("/sidecar/cache/meta?cache=nonexistent")
        assert resp.status_code == 404

    def test_meta_returns_schema(self, client: TestClient) -> None:
        from codeplane.mcp.sidecar_cache import cache_put, get_sidecar_cache

        get_sidecar_cache().clear()
        cid = cache_put("s1", "e1", {"items": [1, 2], "count": 2})
        resp = client.get(f"/sidecar/cache/meta?cache={cid}")
        assert resp.status_code == 200
        result = resp.json()
        assert "byte_size" in result
        assert "sections" in result
        get_sidecar_cache().clear()
