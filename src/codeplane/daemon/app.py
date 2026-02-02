"""Starlette application factory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from starlette.applications import Starlette

from codeplane.daemon.middleware import RepoHeaderMiddleware
from codeplane.daemon.routes import create_routes

if TYPE_CHECKING:
    from codeplane.daemon.lifecycle import ServerController


def create_app(controller: ServerController, repo_root: Path) -> Starlette:
    """Create the Starlette application."""
    routes = create_routes(controller)

    app = Starlette(
        routes=routes,
        on_startup=[controller.start],
        on_shutdown=[controller.stop],
    )

    # Add middleware to inject repo header into responses
    app.add_middleware(RepoHeaderMiddleware, repo_root=repo_root)

    return app
