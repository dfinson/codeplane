"""Shared Copilot client construction helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from copilot import CopilotClient


def create_copilot_client(*, github_token: str | None = None) -> CopilotClient:
    """Build a Copilot client."""
    from copilot import CopilotClient

    if github_token is not None:
        return CopilotClient(github_token=github_token, use_logged_in_user=False)
    return CopilotClient()
