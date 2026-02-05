"""HTTP middleware for response header injection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REPO_HEADER = "X-CodePlane-Repo"


class RepoHeaderMiddleware:
    """Inject X-CodePlane-Repo header into all responses.

    Uses pure ASGI middleware to avoid breaking streaming responses (SSE).
    """

    def __init__(self, app: ASGIApp, repo_root: Path) -> None:
        self.app = app
        self.repo_root = repo_root.resolve()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> Any:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REPO_HEADER.lower().encode(), str(self.repo_root).encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
