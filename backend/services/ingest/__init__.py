"""Imported CLI session ingestion sources."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.ingest.claude_source import ClaudeSessionStateWatcher as ClaudeSessionStateWatcher
    from backend.services.ingest.copilot_source import SessionStateWatcher as SessionStateWatcher

__all__ = ["ClaudeSessionStateWatcher", "SessionStateWatcher"]


def __getattr__(name: str) -> object:
    if name == "ClaudeSessionStateWatcher":
        from backend.services.ingest.claude_source import ClaudeSessionStateWatcher

        return ClaudeSessionStateWatcher
    if name == "SessionStateWatcher":
        from backend.services.ingest.copilot_source import SessionStateWatcher

        return SessionStateWatcher
    raise AttributeError(name)
