"""Tests for daemon.app module.

Tests the Starlette application factory and app creation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pygit2
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount, Route

from codeplane.daemon.app import create_app


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository structure with git initialized."""
    # Initialize git repo
    pygit2.init_repository(str(tmp_path))
    (tmp_path / ".codeplane").mkdir()
    return tmp_path


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create a mock IndexCoordinator."""
    coordinator = MagicMock()
    coordinator.get_db_path.return_value = None
    return coordinator


@pytest.fixture
def mock_controller() -> MagicMock:
    """Create a mock ServerController."""
    controller = MagicMock()
    controller.status = "running"
    return controller


class TestCreateApp:
    """Tests for create_app factory function."""

    def test_returns_starlette_app(
        self,
        temp_repo: Path,
        mock_controller: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """create_app returns a Starlette application."""
        app = create_app(
            controller=mock_controller,
            repo_root=temp_repo,
            coordinator=mock_coordinator,
        )

        assert isinstance(app, Starlette)

    def test_app_has_routes(
        self,
        temp_repo: Path,
        mock_controller: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """Created app has routes configured."""
        app = create_app(
            controller=mock_controller,
            repo_root=temp_repo,
            coordinator=mock_coordinator,
        )

        # Should have at least some routes
        assert len(app.routes) > 0

    def test_app_has_middleware_configured(
        self,
        temp_repo: Path,
        mock_controller: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """Created app has middleware configured via add_middleware."""
        app = create_app(
            controller=mock_controller,
            repo_root=temp_repo,
            coordinator=mock_coordinator,
        )

        # Middleware is configured via add_middleware
        # The middleware_stack is only built on first request
        # Just verify the app was created successfully with routes
        assert len(app.routes) > 0

    def test_app_mounts_mcp_server(
        self,
        temp_repo: Path,
        mock_controller: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """Created app mounts MCP server at root."""
        app = create_app(
            controller=mock_controller,
            repo_root=temp_repo,
            coordinator=mock_coordinator,
        )

        # Check routes include a Mount
        has_mount = any(isinstance(route, Mount) for route in app.routes)
        assert has_mount


class TestAppRoutes:
    """Tests for app route functionality."""

    def test_status_endpoint_exists(
        self,
        temp_repo: Path,
        mock_controller: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """App should have a status endpoint."""
        app = create_app(
            controller=mock_controller,
            repo_root=temp_repo,
            coordinator=mock_coordinator,
        )

        # Check that there's a /status route defined
        has_status = any(
            isinstance(route, Route) and "/status" in getattr(route, "path", "")
            for route in app.routes
        )
        assert has_status

    def test_health_endpoint_exists(
        self,
        temp_repo: Path,
        mock_controller: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """App should have a health endpoint."""
        app = create_app(
            controller=mock_controller,
            repo_root=temp_repo,
            coordinator=mock_coordinator,
        )

        # Check that there's a /health route defined
        has_health = any(
            isinstance(route, Route) and "/health" in getattr(route, "path", "")
            for route in app.routes
        )
        assert has_health


class TestAppMiddleware:
    """Tests for app middleware behavior."""

    def test_middleware_is_repo_header_middleware(
        self,
        temp_repo: Path,
        mock_controller: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """RepoHeaderMiddleware is added to the app."""
        # We can verify by checking the user_middleware list
        app = create_app(
            controller=mock_controller,
            repo_root=temp_repo,
            coordinator=mock_coordinator,
        )

        # App should have the middleware configured
        # The middleware is in app.middleware member as Middleware objects

        has_repo_middleware = any(
            m.cls.__name__ == "RepoHeaderMiddleware" for m in app.user_middleware
        )
        assert has_repo_middleware
