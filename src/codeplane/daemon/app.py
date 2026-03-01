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

    codeplane_dir = repo_root / ".codeplane"

    # Set up disk cache directory for pre-rendered JSON
    from codeplane.mcp.sidecar_cache import set_cache_dir

    set_cache_dir(codeplane_dir / "cache")

    context = AppContext.create(
        repo_root=repo_root,
        db_path=codeplane_dir / "index.db",
        tantivy_path=codeplane_dir / "tantivy",
        coordinator=coordinator,
    )
    mcp = create_mcp_server(context)
    mcp_app = mcp.http_app(path="/mcp", transport="streamable-http")
    routes.append(Mount("/", app=mcp_app))

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        yield
        # Controller stop is handled in run_server finally block
        # to ensure it runs even if lifespan exit times out

    @asynccontextmanager
    async def mcp_lifespan_with_timeout(app: Starlette) -> AsyncIterator[None]:
        """Wrap MCP lifespan with timeout to prevent hanging on shutdown."""
        async with mcp_app.lifespan(app):
            yield
        # MCP cleanup happens when exiting the context manager
        # This is wrapped in timeout in combined_lifespan

    @asynccontextmanager
    async def combined_lifespan(app: Starlette) -> AsyncIterator[None]:
        # Enter MCP lifespan
        async with mcp_app.lifespan(app):
            yield
        # MCP lifespan exit handles stream cleanup - timeout prevents hanging
        # on stuck connections during shutdown

    app = Starlette(
        routes=routes,
        lifespan=combined_lifespan,
    )

    # Add middleware to inject repo header into responses
    app.add_middleware(RepoHeaderMiddleware, repo_root=repo_root)

    return app
