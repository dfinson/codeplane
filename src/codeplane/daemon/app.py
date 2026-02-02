"""Starlette application factory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.routing import BaseRoute, Mount

from codeplane.daemon.middleware import RepoHeaderMiddleware
from codeplane.daemon.routes import create_routes

if TYPE_CHECKING:
    from codeplane.daemon.lifecycle import ServerController
    from codeplane.index.ops import IndexCoordinator


def create_app(
    controller: ServerController,
    repo_root: Path,
    coordinator: IndexCoordinator,
) -> Starlette:
    """Create the Starlette application with MCP server mounted."""
    from codeplane.mcp.context import AppContext
    from codeplane.mcp.server import create_mcp_server

    routes: list[BaseRoute] = list(create_routes(controller))

    # Create MCP server and get its ASGI app
    codeplane_dir = repo_root / ".codeplane"
    context = AppContext.create(
        repo_root=repo_root,
        db_path=codeplane_dir / "index.db",
        tantivy_path=codeplane_dir / "tantivy",
        coordinator=coordinator,
    )
    mcp = create_mcp_server(context)
    mcp_app = mcp.http_app()

    # Mount MCP at /mcp
    routes.append(Mount("/mcp", app=mcp_app))

    app = Starlette(
        routes=routes,
        on_startup=[controller.start],
        on_shutdown=[controller.stop],
    )

    # Add middleware to inject repo header into responses
    app.add_middleware(RepoHeaderMiddleware, repo_root=repo_root)

    return app
