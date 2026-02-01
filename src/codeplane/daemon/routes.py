"""HTTP routes for the CodePlane daemon."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from codeplane.daemon.lifecycle import DaemonController


def create_routes(controller: DaemonController) -> list[Route]:
    """Create HTTP routes bound to the daemon controller."""

    async def health(request: Request) -> JSONResponse:
        """Health check endpoint."""
        _ = request  # unused
        return JSONResponse(
            {
                "status": "healthy",
                "repo_root": str(controller.repo_root),
                "daemon_version": "0.1.0",
            }
        )

    async def status(request: Request) -> JSONResponse:
        """Detailed status endpoint."""
        _ = request  # unused
        indexer_status = controller.indexer.status
        return JSONResponse(
            {
                "repo_root": str(controller.repo_root),
                "indexer": {
                    "state": indexer_status.state.value,
                    "queue_size": indexer_status.queue_size,
                    "last_error": indexer_status.last_error,
                },
                "watcher": {
                    "running": controller.watcher._watch_task is not None,
                },
            }
        )

    return [
        Route("/health", health, methods=["GET"]),
        Route("/status", status, methods=["GET"]),
    ]
