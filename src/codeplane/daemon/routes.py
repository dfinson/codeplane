"""HTTP routes for the CodePlane daemon.

Provides health and status endpoints with comprehensive diagnostics.
"""

from __future__ import annotations

import importlib.metadata
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from codeplane.daemon.lifecycle import ServerController


def _get_version() -> str:
    """Get package version from installed metadata."""
    try:
        return importlib.metadata.version("codeplane")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _get_db_stats(db_path: Path) -> dict[str, Any]:
    """Get SQLite database statistics."""
    stats: dict[str, Any] = {
        "exists": db_path.exists(),
    }
    if db_path.exists():
        stats["size_bytes"] = db_path.stat().st_size
        stats["size_mb"] = round(db_path.stat().st_size / 1024 / 1024, 2)

        # Check WAL file size if it exists
        wal_path = Path(str(db_path) + "-wal")
        if wal_path.exists():
            stats["wal_size_bytes"] = wal_path.stat().st_size
    return stats


def _get_runtime_info() -> dict[str, Any]:
    """Get Python runtime information."""
    return {
        "python_version": sys.version.split()[0],
        "pid": os.getpid(),
    }


def create_routes(controller: ServerController) -> list[Route]:
    """Create HTTP routes bound to the daemon controller."""
    start_time = time.time()
    version = _get_version()

    async def health(request: Request) -> JSONResponse:
        """Health check endpoint.

        Returns a quick status suitable for liveness probes.
        For detailed diagnostics, use /status instead.
        """
        _ = request  # unused
        return JSONResponse(
            {
                "status": "healthy",
                "repo_root": str(controller.repo_root),
                "version": version,
                "uptime_seconds": round(time.time() - start_time, 1),
            }
        )

    async def status(request: Request) -> JSONResponse:
        """Detailed status endpoint with comprehensive diagnostics."""
        _ = request  # unused
        indexer_status = controller.indexer.status

        # Get database path from coordinator if available
        db_path = controller.repo_root / ".codeplane" / "index.db"

        response: dict[str, Any] = {
            "repo_root": str(controller.repo_root),
            "version": version,
            "uptime_seconds": round(time.time() - start_time, 1),
            "runtime": _get_runtime_info(),
            "indexer": {
                "state": indexer_status.state.value,
                "queue_size": indexer_status.queue_size,
                "last_error": indexer_status.last_error,
            },
            "watcher": {
                "running": controller.watcher._watch_task is not None,
            },
            "database": _get_db_stats(db_path),
        }

        # Include last indexing stats if available and valid
        # We check the type name to avoid serializing mock objects in tests
        stats = indexer_status.last_stats
        if stats is not None and type(stats).__name__ == "IndexStats":
            response["last_index"] = {
                "files_added": stats.files_added,
                "files_updated": stats.files_updated,
                "files_removed": stats.files_removed,
                "duration_seconds": round(stats.duration_seconds, 2),
            }

        return JSONResponse(response)

    return [
        Route("/health", health, methods=["GET"]),
        Route("/status", status, methods=["GET"]),
    ]
