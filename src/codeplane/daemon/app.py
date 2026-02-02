"""Starlette application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await controller.start()
        yield
        await controller.stop()

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
    )

    # Add middleware to inject repo header into responses
    app.add_middleware(RepoHeaderMiddleware, repo_root=repo_root)

    return app
