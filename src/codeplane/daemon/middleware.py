"""HTTP middleware for request validation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

REPO_HEADER = "X-CodePlane-Repo"

# Type alias for the call_next function
CallNext = Callable[[Request], Awaitable[Response]]


class RepoValidationMiddleware(BaseHTTPMiddleware):
    """Validate X-CodePlane-Repo header on all requests."""

    def __init__(self, app: Any, repo_root: Path) -> None:
        super().__init__(app)
        self.repo_root = repo_root.resolve()

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        """Validate repo header and dispatch request."""
        # Skip validation for health endpoint
        if request.url.path == "/health":
            return await call_next(request)

        repo_header = request.headers.get(REPO_HEADER)

        if repo_header is None:
            return JSONResponse(
                {
                    "code": 4001,
                    "error": "REPO_HEADER_MISSING",
                    "message": f"Missing required header: {REPO_HEADER}",
                },
                status_code=400,
            )

        received_path = Path(repo_header).resolve()
        if received_path != self.repo_root:
            return JSONResponse(
                {
                    "code": 4002,
                    "error": "REPO_MISMATCH",
                    "message": "Repository path mismatch",
                    "expected": str(self.repo_root),
                    "received": str(received_path),
                },
                status_code=400,
            )

        return await call_next(request)
