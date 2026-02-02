"""HTTP middleware for response header injection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REPO_HEADER = "X-CodePlane-Repo"

# Type alias for the call_next function
CallNext = Callable[[Request], Awaitable[Response]]


class RepoHeaderMiddleware(BaseHTTPMiddleware):  # type: ignore[misc]
    """Inject X-CodePlane-Repo header into all responses."""

    def __init__(self, app: Any, repo_root: Path) -> None:
        super().__init__(app)
        self.repo_root = repo_root.resolve()

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        """Dispatch request and inject repo header into response."""
        response = await call_next(request)
        response.headers[REPO_HEADER] = str(self.repo_root)
        return response
