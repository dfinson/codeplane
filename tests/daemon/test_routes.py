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
        assert len(routes) == 2

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

    def test_status_contains_repo_root(self, client: TestClient, _tmp_path: Path) -> None:
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
